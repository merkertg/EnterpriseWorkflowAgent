"""The users CLI and the decisions demo run in separate processes."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from app.application.errors import UnauthenticatedError
from app.application.users import UserService

pytestmark = pytest.mark.postgres
ROOT = Path(__file__).resolve().parents[2]


def _run(database_url: str, module: str, *arguments: str) -> object:
    """Run a module in a fresh interpreter and parse its JSON output."""
    completed = subprocess.run(
        [sys.executable, "-m", module, *arguments],
        cwd=ROOT,
        env={**os.environ, "DATABASE_URL": database_url},
        capture_output=True,
        text=True,
        check=True,
        timeout=120,
    )
    return json.loads(completed.stdout)


def test_users_cli_manages_accounts_and_tokens(
    user_service: UserService, test_database_url: str
) -> None:
    """create, token, deactivate, activate and revoke change what signs in."""
    created = _run(
        test_database_url,
        "app.users_cli",
        "create",
        "cli-approver",
        "--name",
        "Fictional CLI approver",
        "--role",
        "approver",
        "--role",
        "viewer",
        "--unit",
        "UNIT-A",
    )
    assert created == {
        "user_id": "cli-approver",
        "display_name": "Fictional CLI approver",
        "roles": ["approver", "viewer"],
        "units": ["UNIT-A"],
        "active": True,
    }
    issued = _run(test_database_url, "app.users_cli", "token", "cli-approver")
    assert isinstance(issued, dict)
    secret = str(issued["token"])
    assert user_service.authenticate(secret).user_id == "cli-approver"

    _run(test_database_url, "app.users_cli", "deactivate", "cli-approver")
    with pytest.raises(UnauthenticatedError):
        user_service.authenticate(secret)
    _run(test_database_url, "app.users_cli", "activate", "cli-approver")
    _run(test_database_url, "app.users_cli", "revoke", str(issued["token_id"]))
    with pytest.raises(UnauthenticatedError):
        user_service.authenticate(secret)
    listed = _run(test_database_url, "app.users_cli", "list")
    assert isinstance(listed, list) and [u["user_id"] for u in listed] == [
        "cli-approver"
    ]
    assert secret not in json.dumps(listed)

    duplicate = subprocess.run(
        [sys.executable, "-m", "app.users_cli", "create", "cli-approver"]
        + ["--name", "Again", "--role", "viewer", "--unit", "UNIT-A"],
        cwd=ROOT,
        env={**os.environ, "DATABASE_URL": test_database_url},
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert duplicate.returncode == 1
    assert duplicate.stderr.startswith("user_exists:")


def test_decisions_demo_and_show(
    user_service: UserService, test_database_url: str
) -> None:
    """Every refusal has its code; show reads the approval in a new process."""
    output = _run(test_database_url, "app.decisions_demo")
    assert isinstance(output, dict)
    steps = {
        step["step"]: step.get("error", step.get("state")) for step in output["steps"]
    }
    assert steps == {
        "viewer approves": "missing_permission",
        "approver from another unit approves": "not_found",
        "starter approves own workflow": "self_approval_forbidden",
        "approve on stale reconciliation": "stale_reconciliation",
        "approve with stale workflow version": "version_conflict",
        "approve on fresh matched reconciliation": "approved",
        "same request repeated": "approved",
        "reject after approval": "invalid_transition",
        "approve with discrepancies": "reconciliation_not_matched",
        "reject with discrepancies": "rejected",
        "revoked token signs in": "unauthenticated",
        "deactivated user signs in": "unauthenticated",
    }
    approved = output["approved"]
    shown = _run(
        test_database_url, "app.decisions_demo", "show", approved["workflow_id"]
    )
    assert shown == approved
    [event] = approved["outbox"]
    assert event["event_type"] == "invoice.approved"
    assert "ewa_" not in json.dumps(output)
