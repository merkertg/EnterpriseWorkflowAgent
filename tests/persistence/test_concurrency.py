"""Concurrent commands: one winner, no lost update, duplicates replay."""

import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from functools import partial

import pytest
from sqlalchemy import Engine

from app.application.errors import VersionConflictError
from app.application.ports import CommandResult, IdempotencyRecord
from app.application.workflows import WorkflowService
from app.domain.workflow import HumanDecision
from app.infrastructure.db.repository import SqlAlchemyWorkflowRepository
from app.infrastructure.db.session import create_session_factory
from app.infrastructure.db.unit_of_work import SqlAlchemyUnitOfWork
from tests.persistence.conftest import Seed, context

pytestmark = pytest.mark.postgres


def _together(*calls: Callable[[], CommandResult]) -> list[CommandResult | Exception]:
    """Release all calls at once and collect each result or raised error."""
    barrier = threading.Barrier(len(calls))

    def run(call: Callable[[], CommandResult]) -> CommandResult | Exception:
        """Wait for the other threads, then run one service call."""
        barrier.wait()
        try:
            return call()
        except Exception as error:  # The test inspects the error type.
            return error

    with ThreadPoolExecutor(len(calls)) as pool:
        return list(pool.map(run, calls))


def test_concurrent_duplicates_execute_once(
    service: WorkflowService, seed: Seed
) -> None:
    """Two deliveries of the same request: one executes, the other replays."""
    request = partial(
        service.check, seed.workflow_id, expected_version=1, context=context("dup")
    )
    results = _together(request, request)
    assert all(isinstance(result, CommandResult) for result in results), results
    outcomes = sorted(
        (result.replayed, result.version)
        for result in results
        if isinstance(result, CommandResult)
    )
    assert outcomes == [(False, 2), (True, 2)]
    checks = [e for e in service.audit_trail(seed.workflow_id) if e.command == "check"]
    assert len(checks) == 1


def test_concurrent_different_commands_have_one_winner(
    service: WorkflowService, seed: Seed
) -> None:
    """Two intents based on version 1: one commits, the other gets a conflict."""
    decision = HumanDecision("REVIEWER-TEST-1", "Fictional concurrent rejection")
    results = _together(
        partial(
            service.check, seed.workflow_id, expected_version=1, context=context("a")
        ),
        partial(
            service.reject,
            seed.workflow_id,
            expected_version=1,
            decision=decision,
            context=context("b"),
        ),
    )
    winners = [result for result in results if isinstance(result, CommandResult)]
    losers = [result for result in results if isinstance(result, Exception)]
    assert len(winners) == 1 and winners[0].version == 2
    assert len(losers) == 1 and isinstance(losers[0], VersionConflictError)
    assert len(service.audit_trail(seed.workflow_id)) == 2


def test_compare_and_set_blocks_a_lost_update(
    engine: Engine, service: WorkflowService, seed: Seed
) -> None:
    """A writer that read version 1 cannot overwrite a committed version 2.

    This drives the repository directly so the conditional UPDATE is tested
    separately from the use case's earlier version comparison.
    """
    with SqlAlchemyUnitOfWork(create_session_factory(engine)) as stale:
        before = stale.repository.get_workflow(seed.workflow_id)
        assert before is not None
        service.check(seed.workflow_id, expected_version=1, context=context("c"))
        with pytest.raises(VersionConflictError):
            stale.repository.update_workflow(before, expected_version=1)
    assert service.get_workflow(seed.workflow_id).version == 2


def test_duplicate_committed_between_lookup_and_read_is_replayed(
    service: WorkflowService, seed: Seed, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Deterministic interleaving found by the threaded test.

    The second delivery finds no key, then the first delivery commits, then the
    second reads version 2. It must answer with a replay, not a conflict.
    """
    original = SqlAlchemyWorkflowRepository.get_idempotency_record
    competitor: list[CommandResult] = []
    interleaved = threading.Event()

    def lookup_then_let_duplicate_commit(
        repository: SqlAlchemyWorkflowRepository, key: str
    ) -> IdempotencyRecord | None:
        """Return the real lookup, but commit the duplicate right after it once."""
        found = original(repository, key)
        if not interleaved.is_set():
            # Set first: the duplicate's own lookup must not interleave again.
            interleaved.set()
            competitor.append(
                service.check(
                    seed.workflow_id, expected_version=1, context=context("dup")
                )
            )
        return found

    monkeypatch.setattr(
        SqlAlchemyWorkflowRepository,
        "get_idempotency_record",
        lookup_then_let_duplicate_commit,
    )
    result = service.check(seed.workflow_id, expected_version=1, context=context("dup"))
    assert competitor[0].replayed is False
    assert result == replace(competitor[0], replayed=True)
