"""Invalid command arguments fail before any transaction or connection is opened."""

from collections.abc import Callable
from typing import Any, cast
from uuid import uuid4

import pytest

from app.application.access import Principal, Role
from app.application.ports import CommandContext, UnitOfWork
from app.application.workflows import WorkflowService
from app.domain.errors import DomainValidationError


def _never_opened() -> UnitOfWork:
    """Fail loudly if a use case reaches persistence with invalid arguments."""
    raise AssertionError("A transaction must not be opened for invalid input.")


@pytest.fixture
def service() -> WorkflowService:
    """A service whose persistence must stay unused in these tests."""
    return WorkflowService(_never_opened)


CONTEXT = CommandContext("key-1", "pytest")
APPROVER = Principal("approver-1", frozenset({Role.APPROVER}), frozenset({"UNIT-A"}))


@pytest.mark.parametrize(
    ("key", "source"), [("", "pytest"), ("key", " "), ("k", "x" * 65)]
)
def test_context_requires_bounded_key_and_source(key: str, source: str) -> None:
    """The idempotency key and source label are explicit and bounded."""
    with pytest.raises(DomainValidationError):
        CommandContext(key, source)


@pytest.mark.parametrize("version", [0, -1, True, "1", 1.0])
def test_expected_version_must_be_a_positive_int(
    service: WorkflowService, version: object
) -> None:
    """Booleans, strings and floats cannot pass as optimistic-lock versions."""
    with pytest.raises(DomainValidationError) as error:
        service.check("WF-1", expected_version=cast(int, version), context=CONTEXT)
    assert error.value.code == "invalid_version"


def test_workflow_id_and_context_are_validated(service: WorkflowService) -> None:
    """Blank IDs and a missing context are rejected locally."""
    with pytest.raises(DomainValidationError):
        service.check(" ", expected_version=1, context=CONTEXT)
    with pytest.raises(DomainValidationError):
        service.check("WF-1", expected_version=1, context=cast(Any, None))


@pytest.mark.parametrize(
    "call",
    [
        lambda s: s.approve(
            "WF-1",
            expected_version=1,
            reconciliation_snapshot_id=uuid4(),
            reason="ok",
            actor="approver-1",
            context=CONTEXT,
        ),
        lambda s: s.approve(
            "WF-1",
            expected_version=1,
            reconciliation_snapshot_id="not-a-uuid",
            reason="ok",
            actor=APPROVER,
            context=CONTEXT,
        ),
        lambda s: s.reject(
            "WF-1", expected_version=1, reason="no", actor=None, context=CONTEXT
        ),
        lambda s: s.check("WF-1", expected_version=1, context=CONTEXT, actor="x"),
        lambda s: s.start(
            "WF-1",
            invoice_version_id="not-a-uuid",
            purchase_order_version_id=None,
            policy_version_id=uuid4(),
            business_unit="UNIT-A",
            context=CONTEXT,
        ),
        lambda s: s.record_invoice({"invoice_id": "INV-1"}),
        lambda s: s.get_snapshot("not-a-uuid"),
    ],
)
def test_wrong_argument_types_are_rejected(
    service: WorkflowService, call: Callable[[Any], object]
) -> None:
    """Actors, version IDs and documents must already be typed values."""
    with pytest.raises(DomainValidationError) as error:
        call(service)
    assert error.value.code == "invalid_type"


@pytest.mark.parametrize("unit", ["", " ", "U" * 65])
def test_start_requires_a_business_unit(service: WorkflowService, unit: str) -> None:
    """A workflow without a bounded business unit could not be scoped."""
    with pytest.raises(DomainValidationError) as error:
        service.start(
            "WF-1",
            invoice_version_id=uuid4(),
            purchase_order_version_id=None,
            policy_version_id=uuid4(),
            business_unit=unit,
            context=CONTEXT,
        )
    assert error.value.field == "business_unit"


@pytest.mark.parametrize("reason", ["", "  ", "x" * 2001])
def test_decisions_require_a_bounded_reason(
    service: WorkflowService, reason: str
) -> None:
    """An empty or oversized reason is refused before a transaction opens."""
    with pytest.raises(DomainValidationError) as error:
        service.reject(
            "WF-1", expected_version=1, reason=reason, actor=APPROVER, context=CONTEXT
        )
    assert error.value.field == "reason"
