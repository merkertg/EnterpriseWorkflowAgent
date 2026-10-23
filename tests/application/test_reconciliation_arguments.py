"""Invalid reconciliation arguments fail before any transaction is opened."""

from collections.abc import Callable
from typing import Any

import pytest

from app.application.ports import UnitOfWork
from app.application.reconciliation import ReconciliationService
from app.domain.errors import DomainValidationError
from app.reconciliation_cli import main


def _never_opened() -> UnitOfWork:
    """Fail loudly if a use case reaches persistence with invalid arguments."""
    raise AssertionError("A transaction must not be opened for invalid input.")


@pytest.mark.parametrize(
    "call",
    [
        lambda s: s.record_rules({"rules_id": "x"}),
        lambda s: s.reconcile("WF-1", rules_version_id="not-a-uuid"),
        lambda s: s.reconcile(" ", rules_version_id="not-a-uuid"),
        lambda s: s.get_snapshot("not-a-uuid"),
        lambda s: s.latest(""),
    ],
)
def test_wrong_arguments_are_rejected_locally(
    call: Callable[[ReconciliationService], Any],
) -> None:
    """Types and blank IDs are checked before storage is touched."""
    with pytest.raises(DomainValidationError):
        call(ReconciliationService(_never_opened))


def test_cli_needs_a_database(capsys: pytest.CaptureFixture[str]) -> None:
    """Without DATABASE_URL the CLI exits with code 2 and explains why."""
    assert main(["demo"]) == 2
    assert "DATABASE_URL" in capsys.readouterr().err
