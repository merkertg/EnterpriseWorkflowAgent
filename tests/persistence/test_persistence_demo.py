"""The PostgreSQL demo runs end to end and a new process can read it back."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from app.application.workflows import WorkflowService
from app.persistence_demo import run_persistence_demo

pytestmark = pytest.mark.postgres

ROOT = Path(__file__).resolve().parents[2]


def test_demo_story_has_expected_outcomes(service: WorkflowService) -> None:
    """Replay, conflict, new versions and a rejected re-check, all fictional."""
    output = run_persistence_demo(service, "pytest-run")
    steps = output["steps"]
    assert isinstance(steps, list)
    assert [(s["command"], s.get("state"), s.get("version")) for s in steps] == [
        ("start", "received", 1),
        ("check", "awaiting_approval", 2),
        ("check", "awaiting_approval", 2),
        ("approve", None, None),
        ("revise", "received", 3),
        ("check", "needs_review", 4),
        ("reject", "rejected", 5),
    ]
    assert steps[2]["replayed"] is True
    assert steps[3]["error"] == "version_conflict"
    snapshots = output["snapshots"]
    assert isinstance(snapshots, dict)
    first, second = snapshots["first_check"], snapshots["after_revision"]
    assert (first["purchase_order"]["version_no"], first["purchase_order_total"]) == (
        1,
        "30.00",
    )
    assert (second["purchase_order"]["version_no"], second["policy"]["version_no"]) == (
        2,
        2,
    )
    assert second["issue_codes"] == ["purchase_order_total_exceeded"]
    assert output["identity_verified"] is False


def test_cli_run_then_show_in_separate_processes(
    service: WorkflowService, test_database_url: str
) -> None:
    """Each process opens its own connections; show reads what run committed."""
    environment = {**os.environ, "DATABASE_URL": test_database_url}

    def cli(*arguments: str) -> dict[str, object]:
        """Run the module in a fresh interpreter and parse its JSON output."""
        completed = subprocess.run(
            [sys.executable, "-m", "app.persistence_demo", *arguments],
            cwd=ROOT,
            env=environment,
            capture_output=True,
            text=True,
            check=True,
            timeout=60,
        )
        parsed = json.loads(completed.stdout)
        assert isinstance(parsed, dict)
        return parsed

    stored = cli()["stored"]
    assert isinstance(stored, dict)
    assert cli("show", str(stored["workflow_id"])) == stored
