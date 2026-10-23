"""Persistent commands: transitions, snapshots, audit, idempotency and atomicity."""

from dataclasses import replace
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import Engine, text

from app.application.errors import (
    IdempotencyKeyReusedError,
    NotFoundError,
    VersionConflictError,
    WorkflowAlreadyExistsError,
)
from app.application.ports import CommandName
from app.application.workflows import WorkflowService
from app.domain.errors import DomainValidationError
from app.domain.money import Currency
from app.domain.rules import ValidationPolicy
from app.domain.workflow import HumanDecision, WorkflowState
from app.infrastructure.db.repository import SqlAlchemyWorkflowRepository
from app.infrastructure.db.session import build_workflow_service, create_database_engine
from tests.persistence.conftest import TABLES, Seed, context

pytestmark = pytest.mark.postgres

DECISION = HumanDecision("REVIEWER-TEST-1", "Fictional documents reviewed")


def _counts(engine: Engine) -> dict[str, int]:
    """Count rows in every table to prove that nothing partial was written."""
    with engine.connect() as connection:
        return {
            table: connection.execute(
                text(f"SELECT count(*) FROM {table}")
            ).scalar_one()
            for table in TABLES
        }


def test_start_stores_a_received_workflow_and_audit_event(
    service: WorkflowService, seed: Seed
) -> None:
    """The first command creates version 1 and one audit row."""
    assert (seed.started.state, seed.started.version) == (WorkflowState.RECEIVED, 1)
    stored = service.get_workflow(seed.workflow_id)
    assert stored.workflow.invoice == seed.invoice
    assert stored.purchase_order_version == seed.purchase_order_version
    [event] = service.audit_trail(seed.workflow_id)
    assert (event.command, event.previous_state, event.new_state) == (
        CommandName.START,
        None,
        WorkflowState.RECEIVED,
    )
    assert (event.source, event.idempotency_key) == ("pytest", "start-1")
    assert event.event_id is not None and event.occurred_at is not None


def test_happy_path_versions_snapshot_and_audit(
    service: WorkflowService, seed: Seed
) -> None:
    """start -> check -> approve gives versions 1, 2, 3 and a pinned snapshot."""
    checked = service.check(seed.workflow_id, expected_version=1, context=context("c"))
    approved = service.approve(
        seed.workflow_id, expected_version=2, decision=DECISION, context=context("a")
    )
    assert (checked.state, checked.version) == (WorkflowState.AWAITING_APPROVAL, 2)
    assert (approved.state, approved.version) == (WorkflowState.APPROVED, 3)
    assert checked.snapshot_id is not None and approved.snapshot_id is None

    snapshot = service.get_snapshot(checked.snapshot_id)
    assert snapshot.invoice_version == seed.invoice_version
    assert snapshot.purchase_order_version == seed.purchase_order_version
    assert snapshot.policy_version == seed.policy_version
    assert snapshot.workflow_version == 2
    assert snapshot.result.is_valid

    stored = service.get_workflow(seed.workflow_id)
    assert stored.last_snapshot_id == checked.snapshot_id
    assert stored.workflow.decision == DECISION
    trail = service.audit_trail(seed.workflow_id)
    assert [(e.command.value, e.workflow_version) for e in trail] == [
        ("start", 1),
        ("check", 2),
        ("approve", 3),
    ]
    assert trail[2].actor_id == "REVIEWER-TEST-1"


def test_data_survives_a_new_engine_and_connection_pool(
    test_database_url: str, service: WorkflowService, seed: Seed
) -> None:
    """A separate engine, like a restarted process, reads the same workflow."""
    service.check(seed.workflow_id, expected_version=1, context=context("c"))
    before = service.get_workflow(seed.workflow_id)
    other_engine = create_database_engine(test_database_url)
    try:
        after = build_workflow_service(other_engine).get_workflow(seed.workflow_id)
    finally:
        other_engine.dispose()
    assert after == before


def test_findings_are_stored_and_audited_as_codes(
    engine: Engine, service: WorkflowService, seed: Seed
) -> None:
    """Issue codes are audited; document descriptions never enter audit rows."""
    order_v2 = service.record_purchase_order(
        replace(seed.purchase_order, supplier_id="SUPPLIER-OTHER")
    )
    service.revise(
        seed.workflow_id,
        expected_version=1,
        invoice_version_id=seed.invoice_version.version_id,
        purchase_order_version_id=order_v2.version_id,
        policy_version_id=seed.policy_version.version_id,
        context=context("revise"),
    )
    checked = service.check(seed.workflow_id, expected_version=2, context=context("c"))
    assert checked.state == WorkflowState.NEEDS_REVIEW
    assert service.audit_trail(seed.workflow_id)[-1].issue_codes == (
        "supplier_mismatch",
    )
    with engine.connect() as connection:
        audit_text = str(
            connection.execute(text("SELECT json_agg(a) FROM audit_events a")).scalar()
        )
    for line in seed.invoice.lines:
        assert line.description not in audit_text


def test_same_key_and_request_replays_without_new_writes(
    engine: Engine, service: WorkflowService, seed: Seed
) -> None:
    """A retried request returns the stored outcome and writes nothing new."""
    first = service.check(seed.workflow_id, expected_version=1, context=context("c"))
    counts = _counts(engine)
    second = service.check(seed.workflow_id, expected_version=1, context=context("c"))
    assert second == replace(first, replayed=True)
    assert _counts(engine) == counts


def test_same_key_with_another_request_is_rejected(
    service: WorkflowService, seed: Seed
) -> None:
    """A key identifies one intent; other arguments cannot reuse it."""
    service.check(seed.workflow_id, expected_version=1, context=context("c"))
    with pytest.raises(IdempotencyKeyReusedError):
        service.reject(
            seed.workflow_id,
            expected_version=2,
            decision=DECISION,
            context=context("c"),
        )


def test_stale_expected_version_changes_nothing(
    engine: Engine, service: WorkflowService, seed: Seed
) -> None:
    """Optimistic locking rejects a command based on an older read."""
    service.check(seed.workflow_id, expected_version=1, context=context("c"))
    counts = _counts(engine)
    with pytest.raises(VersionConflictError):
        service.reject(
            seed.workflow_id,
            expected_version=1,
            decision=DECISION,
            context=context("r"),
        )
    assert _counts(engine) == counts
    assert service.get_workflow(seed.workflow_id).version == 2


def test_rejected_domain_command_does_not_consume_the_key(
    engine: Engine, service: WorkflowService, seed: Seed
) -> None:
    """Failed attempts are not cached; the key can carry a corrected request."""
    counts = _counts(engine)
    with pytest.raises(DomainValidationError) as error:
        service.approve(
            seed.workflow_id,
            expected_version=1,
            decision=DECISION,
            context=context("x"),
        )
    assert error.value.code == "invalid_transition"
    assert _counts(engine) == counts
    result = service.check(seed.workflow_id, expected_version=1, context=context("x"))
    assert result.replayed is False


def test_start_rejects_reused_ids_and_unknown_versions(
    service: WorkflowService, seed: Seed
) -> None:
    """A second start for the same ID and a missing version both fail clearly."""
    with pytest.raises(WorkflowAlreadyExistsError):
        service.start(
            seed.workflow_id,
            invoice_version_id=seed.invoice_version.version_id,
            purchase_order_version_id=None,
            policy_version_id=seed.policy_version.version_id,
            context=context("start-2"),
        )
    with pytest.raises(NotFoundError):
        service.start(
            "WF-TEST-2",
            invoice_version_id=uuid4(),
            purchase_order_version_id=None,
            policy_version_id=seed.policy_version.version_id,
            context=context("start-3"),
        )
    with pytest.raises(NotFoundError):
        service.check("WF-MISSING", expected_version=1, context=context("c"))


def test_failure_before_commit_leaves_no_partial_state(
    engine: Engine,
    service: WorkflowService,
    seed: Seed,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If the audit insert fails, the key, snapshot and state change roll back."""
    counts = _counts(engine)

    def fail(*_args: object) -> None:
        """Simulate a crash after every other write in the transaction."""
        raise RuntimeError("injected audit failure")

    monkeypatch.setattr(SqlAlchemyWorkflowRepository, "add_audit_event", fail)
    with pytest.raises(RuntimeError, match="injected"):
        service.check(seed.workflow_id, expected_version=1, context=context("c"))
    assert _counts(engine) == counts
    stored = service.get_workflow(seed.workflow_id)
    assert (stored.version, stored.workflow.state) == (1, WorkflowState.RECEIVED)

    monkeypatch.undo()
    retried = service.check(seed.workflow_id, expected_version=1, context=context("c"))
    assert (retried.version, retried.replayed) == (2, False)


def test_old_snapshot_keeps_original_inputs_after_order_and_policy_change(
    service: WorkflowService, seed: Seed
) -> None:
    """New PO and policy versions never rewrite the inputs of an earlier check."""
    first = service.check(seed.workflow_id, expected_version=1, context=context("c1"))
    assert first.snapshot_id is not None

    first_line = seed.purchase_order.lines[0]
    smaller_order = replace(
        seed.purchase_order,
        lines=(
            replace(first_line, quantity=Decimal("1")),
            seed.purchase_order.lines[1],
        ),
    )
    order_v2 = service.record_purchase_order(smaller_order)
    policy_v2 = service.record_policy(
        ValidationPolicy(seed.policy.policy_id, (Currency.USD,))
    )
    assert (order_v2.version_no, policy_v2.version_no) == (2, 2)

    service.revise(
        seed.workflow_id,
        expected_version=2,
        invoice_version_id=seed.invoice_version.version_id,
        purchase_order_version_id=order_v2.version_id,
        policy_version_id=policy_v2.version_id,
        context=context("revise"),
    )
    assert service.get_workflow(seed.workflow_id).last_snapshot_id is None
    second = service.check(seed.workflow_id, expected_version=3, context=context("c2"))
    assert second.state == WorkflowState.NEEDS_REVIEW
    assert second.snapshot_id is not None

    old = service.get_snapshot(first.snapshot_id)
    assert old.purchase_order_version == seed.purchase_order_version
    assert old.policy_version == seed.policy_version
    assert old.result.purchase_order == seed.purchase_order
    assert old.result.purchase_order.total.amount == Decimal("30.00")
    assert old.result.issues == ()

    new = service.get_snapshot(second.snapshot_id)
    assert new.purchase_order_version == order_v2
    assert new.policy_version == policy_v2
    assert [issue.code.value for issue in new.result.issues] == [
        "purchase_order_total_exceeded"
    ]
    revise_event = service.audit_trail(seed.workflow_id)[2]
    assert revise_event.command == CommandName.REVISE
    assert revise_event.purchase_order_version_id == order_v2.version_id


def test_reject_keeps_the_latest_snapshot_reference(
    service: WorkflowService, seed: Seed
) -> None:
    """A rejection after a check still shows which result the reviewer saw."""
    checked = service.check(seed.workflow_id, expected_version=1, context=context("c"))
    service.reject(
        seed.workflow_id, expected_version=2, decision=DECISION, context=context("r")
    )
    stored = service.get_workflow(seed.workflow_id)
    assert stored.workflow.state == WorkflowState.REJECTED
    assert stored.last_snapshot_id == checked.snapshot_id
    with pytest.raises(DomainValidationError):
        service.check(seed.workflow_id, expected_version=3, context=context("c2"))
