"""The offline agent demo runs end to end and its traces can be read back."""

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from app.application.workflows import WorkflowService

pytestmark = pytest.mark.postgres
ROOT = Path(__file__).resolve().parents[2]


def _cli(database_url: str, *arguments: str) -> dict[str, Any]:
    """Run the module in a fresh interpreter and parse its JSON output."""
    completed = subprocess.run(
        [sys.executable, "-m", "app.agent_demo", *arguments],
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


def test_agent_demo_and_show(service: WorkflowService, test_database_url: str) -> None:
    """Every scenario ends with its expected code; workflows stay unchanged."""
    output = _cli(test_database_url)
    runs = output["runs"]
    assert isinstance(runs, dict)
    outcome = {
        name: (r["stop_reason"], r["next_step"], r["override_reason"])
        for name, r in runs.items()
    }
    assert outcome == {
        "clean_invoice": ("final_answer", "ready_for_approval", None),
        "price_difference": ("final_answer", "needs_human_review", None),
        "document_injection": ("too_many_refusals", None, None),
        "fabricated_citation": (
            "final_answer",
            "needs_human_review",
            "unsupported_citation",
        ),
        "overstated_result": (
            "final_answer",
            "needs_human_review",
            "reconciliation_not_matched",
        ),
        "looping_model": ("provider_call_limit", None, None),
        "slow_model": ("provider_timeout", None, None),
        "malformed_answer": ("invalid_final_answer", None, None),
    }
    injection = runs["document_injection"]
    assert [s["error"] for s in injection["trace"] if s["kind"] == "refused_call"] == [
        "unknown_tool",
        "invalid_arguments",
        "out_of_scope",
    ]
    assert output["refused_before_run"] == {
        "viewer": "missing_permission",
        "other_unit_clerk": "not_found",
    }
    assert set(output["workflow_states_after"].values()) == {"awaiting_approval"}
    proposals = output["model_field_proposals"]
    assert {k: v["status"] for k, v in proposals.items()} == {
        "model_clean": "complete",
        "model_fabricated_total": "needs_review",
        "model_malformed_output": "needs_review",
    }
    assert output["network"] is False and "ewa_" not in json.dumps(output)
    shown = _cli(test_database_url, "show", injection["run_id"])
    assert shown == {
        k: v for k, v in injection.items() if k not in ("explanation", "cited_codes")
    }
