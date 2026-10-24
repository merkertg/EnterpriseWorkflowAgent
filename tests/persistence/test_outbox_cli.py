"""The outbox worker and the delivery demo run as separate processes."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import Engine

from app.fictional_desk import FictionalDesk
from sandbox.receiver import ReceiverStore, create_receiver, serving

pytestmark = pytest.mark.postgres
ROOT = Path(__file__).resolve().parents[2]
SECRET = "fictional-cli-secret-" + "c" * 20


def _run(
    env: dict[str, str], module: str, *arguments: str
) -> subprocess.CompletedProcess[str]:
    """Run a module in a fresh interpreter with extra environment variables."""
    return subprocess.run(
        [sys.executable, "-m", module, *arguments],
        cwd=ROOT,
        env={**os.environ, **env},
        capture_output=True,
        text=True,
        check=False,
        timeout=180,
    )


def test_worker_delivers_reports_and_requeues(
    engine: Engine, test_database_url: str, tmp_path: Path
) -> None:
    """run --once prints attempts; status counts; requeue refuses unknown IDs."""
    workflow_id = FictionalDesk(engine, "pytest").approved("CLI")
    base = {"DATABASE_URL": test_database_url}
    missing = _run(base, "app.outbox_worker", "status")
    assert missing.returncode == 2 and "SANDBOX_WEBHOOK_SECRET" in missing.stderr
    store = ReceiverStore(tmp_path / "receiver.sqlite3")
    with serving(create_receiver(store, SECRET)) as url:
        env = {**base, "SANDBOX_WEBHOOK_URL": url, "SANDBOX_WEBHOOK_SECRET": SECRET}
        delivered = _run(env, "app.outbox_worker", "run", "--once")
        status = _run(env, "app.outbox_worker", "status")
        unknown = _run(
            env, "app.outbox_worker", "requeue", "00000000-0000-0000-0000-000000000000"
        )
    [attempt] = [json.loads(line) for line in delivered.stdout.splitlines()]
    assert (attempt["workflow_id"], attempt["status"]) == (workflow_id, "delivered")
    assert json.loads(status.stdout)["counts"]["delivered"] == 1
    assert unknown.returncode == 1 and unknown.stderr.startswith("not_found")
    assert SECRET not in delivered.stdout + status.stdout + unknown.stderr
    elsewhere = _run(
        {**env, "SANDBOX_WEBHOOK_URL": "http://203.0.113.9/webhook"},
        "app.outbox_worker",
        "status",
    )
    assert elsewhere.returncode == 2
    assert "sandbox_webhook_url" in elsewhere.stderr


def test_outbox_demo_shows_every_mode(engine: Engine, test_database_url: str) -> None:
    """Retries, a lost answer, a timeout, a forged signature, exhaustion and
    two workers, each ending with one receiver action per event."""
    completed = _run({"DATABASE_URL": test_database_url}, "app.outbox_demo")
    assert completed.returncode == 0, completed.stderr
    scenarios = json.loads(completed.stdout)["scenarios"]
    lost = scenarios["C: answer lost after the receiver acted"]
    assert (lost["receiver_actions"], lost["receiver_receipts"]) == (1, 2)
    timed = scenarios["D: timeout while the receiver was still working"]
    assert timed["rounds"][0][0]["error"] == "timeout"
    assert timed["receiver_actions"] == 1
    forged = scenarios["E: wrong secret refused, right secret accepted"]
    assert forged["rounds"][0][0]["http_status"] == 401
    exhausted = scenarios["F: attempts exhausted, visible, then requeued"]
    assert exhausted["status_seen_by_viewer"] == "exhausted"
    assert exhausted["status"] == "delivered"
    both = scenarios["G: two workers at once"]
    assert sum(both["attempts_per_worker"]) == 6
    assert (both["receiver_actions"], both["receiver_receipts"]) == (6, 6)
