"""Invalid command arguments fail before any transaction or connection is opened."""

from collections.abc import Callable
from typing import Any, cast
from uuid import uuid4

import pytest

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
            "WF-1", expected_version=1, decision="yes", context=CONTEXT
        ),
        lambda s: s.reject("WF-1", expected_version=1, decision=None, context=CONTEXT),
        lambda s: s.start(
            "WF-1",
            invoice_version_id="not-a-uuid",
            purchase_order_version_id=None,
            policy_version_id=uuid4(),
            context=CONTEXT,
        ),
        lambda s: s.record_invoice({"invoice_id": "INV-1"}),
        lambda s: s.get_snapshot("not-a-uuid"),
    ],
)
def test_wrong_argument_types_are_rejected(
    service: WorkflowService, call: Callable[[Any], object]
) -> None:
    """Decisions, version IDs and documents must already be typed values."""
    with pytest.raises(DomainValidationError) as error:
        call(service)
    assert error.value.code == "invalid_type"
