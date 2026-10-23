"""Exercise the standalone demonstration and the standard-library domain boundary."""

import json
import os
import subprocess
import sys
from pathlib import Path

from app.demo import run_demo

ROOT = Path(__file__).resolve().parents[1]


def test_demo_has_known_outcomes_and_exact_totals() -> None:
    """Expected values are stated independently of the code that computes them."""
    output = json.loads(json.dumps(run_demo()))
    assert output["happy_path"]["history"] == [
        "received",
        "awaiting_approval",
        "approved",
    ]
    approved = output["happy_path"]["result"]
    assert approved["declared_total"] == "30.00"
    assert approved["calculated_total"] == "30.00"
    assert approved["decision"]["actor_id"] == "demo-approver-1"
    assert approved["issues"] == []
    assert output["mismatch"]["state"] == "needs_review"
    assert [issue["code"] for issue in output["mismatch"]["issues"]] == [
        "invoice_total_mismatch",
        "purchase_order_total_exceeded",
    ]
    assert [issue["code"] for issue in output["missing_order"]["issues"]] == [
        "missing_purchase_order"
    ]
    assert output["identity_verified"] is False


def test_demo_is_deterministic() -> None:
    """The same fictional inputs produce the same JSON without clocks or randomness."""
    assert run_demo() == run_demo()


def test_cli_runs_without_third_party_packages_or_application_settings() -> None:
    """Disable site-packages to prove the domain/demo do not need the web framework."""
    environment = os.environ.copy()
    environment.update({"APP_NAME": "", "APP_ENV": "invalid", "APP_DEBUG": "invalid"})
    result = subprocess.run(
        [sys.executable, "-S", "-m", "app.demo"],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=True,
        timeout=10,
    )
    assert result.stderr == ""
    assert json.loads(result.stdout) == json.loads(json.dumps(run_demo()))
