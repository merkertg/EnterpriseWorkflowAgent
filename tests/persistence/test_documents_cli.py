"""The documents CLI stores a file and opens its evidence from new processes."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from app.application.documents import DocumentService
from app.samples import write_samples

pytestmark = pytest.mark.postgres
ROOT = Path(__file__).resolve().parents[2]


def _cli(database_url: str, *arguments: str) -> object:
    """Run the module in a fresh interpreter and parse its JSON output."""
    completed = subprocess.run(
        [sys.executable, "-m", "app.documents_cli", *arguments],
        cwd=ROOT,
        env={**os.environ, "DATABASE_URL": database_url},
        capture_output=True,
        text=True,
        check=True,
        timeout=120,
    )
    return json.loads(completed.stdout)


def test_ingest_then_evidence_in_separate_processes(
    document_service: DocumentService, test_database_url: str, tmp_path: Path
) -> None:
    """The stored total is cited on page 2 of the sample PDF."""
    write_samples(tmp_path)
    stored = _cli(test_database_url, "ingest", str(tmp_path / "clean_invoice.pdf"))
    assert isinstance(stored, dict)
    assert (stored["status"], stored["pages"]) == ("complete", 2)
    evidence = _cli(
        test_database_url, "evidence", stored["document_id"], "declared_total"
    )
    assert isinstance(evidence, dict)
    assert evidence["citations"] == [
        {
            "page": 2,
            "line_number": 1,
            "start": 11,
            "end": 16,
            "quote": "30.00",
            "line": "Total due: 30.00",
        }
    ]
    shown = _cli(test_database_url, "show", stored["document_id"])
    assert isinstance(shown, dict)
    assert shown["extraction_id"] == stored["extraction_id"]


def test_demo_runs_end_to_end(
    document_service: DocumentService, test_database_url: str
) -> None:
    """Samples, refusals, evidence and a checked workflow in one run."""
    output = _cli(test_database_url, "demo")
    assert isinstance(output, dict)
    assert output["refused"] == {
        "picture.png": "unsupported_type",
        "too_large.txt": "too_large",
        "scanned.pdf": "no_text_layer",
        "latin1.txt": "invalid_text_encoding",
    }
    statuses = {name: doc["status"] for name, doc in output["documents"].items()}
    assert statuses == {
        "clean_invoice.txt": "complete",
        "clean_invoice.pdf": "complete",
        "incomplete_invoice.txt": "needs_review",
        "instruction_invoice.txt": "complete",
    }
    assert output["workflow"]["state_after_check"] == "awaiting_approval"
