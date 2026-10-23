"""The reconciliation demo and report run end to end in separate processes."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from app.application.reconciliation import ReconciliationService

pytestmark = pytest.mark.postgres
ROOT = Path(__file__).resolve().parents[2]


def _cli(database_url: str, *arguments: str) -> dict[str, object]:
    """Run the module in a fresh interpreter and parse its JSON output."""
    completed = subprocess.run(
        [sys.executable, "-m", "app.reconciliation_cli", *arguments],
        cwd=ROOT,
        env={**os.environ, "DATABASE_URL": database_url},
        capture_output=True,
        text=True,
        check=True,
        timeout=120,
    )
    parsed = json.loads(completed.stdout)
    assert isinstance(parsed, dict)
    return parsed


def test_demo_story_and_show(
    reconciliation_service: ReconciliationService, test_database_url: str
) -> None:
    """Expected outcomes per scenario, then a report read in a new process."""
    output = _cli(test_database_url, "demo")
    scenarios = output["reconciliations"]
    assert isinstance(scenarios, dict)
    codes = {
        name: [item["code"] for item in report["discrepancies"]]
        for name, report in scenarios.items()
    }
    assert codes == {
        "matched": [],
        "price_variance": ["unit_price_mismatch", "expected_total_mismatch"],
        "quantity_and_unknown_line": ["quantity_exceeds_order", "unknown_line"],
        "duplicate": ["duplicate_invoice"],
    }
    stale = output["after_inputs_changed"]
    assert isinstance(stale, dict)
    assert stale["snapshot_a"]["stale_reasons"] == [
        "purchase_order_changed",
        "rules_changed",
        "duplicates_changed",
    ]
    fixed = output["after_fix"]
    assert isinstance(fixed, dict)
    assert (fixed["status"], fixed["current"], fixed["reproducible"]) == (
        "matched",
        True,
        True,
    )
    no_rule = output["no_rule_for_currency"]
    assert isinstance(no_rule, dict)
    assert [d["code"] for d in no_rule["discrepancies"]] == ["no_rule_for_currency"]
    shown = _cli(test_database_url, "show", str(fixed["snapshot_id"]))
    assert shown == fixed
