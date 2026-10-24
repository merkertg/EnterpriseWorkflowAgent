"""The evaluation catalogue and its report, without a database."""

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.evals.__main__ import main
from app.evals.cases import CASES, Category, EvalCase
from app.evals.runner import LIMITATIONS, CaseResult, EvalReport, write_report


def test_every_roadmap_category_has_cases_with_explicit_expectations() -> None:
    """Injection, wrong fields, ambiguity, provider failure, timeout, retry,
    concurrency, malicious documents and access are all covered."""
    assert {item.category for item in CASES} == set(Category)
    ids = [item.case_id for item in CASES]
    assert len(ids) == len(set(ids))
    assert all(item.expected and item.description for item in CASES)


def test_list_prints_every_case(capsys: pytest.CaptureFixture[str]) -> None:
    """``list`` needs no database and shows each expectation."""
    assert main(["list"]) == 0
    lines = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert [line["case_id"] for line in lines] == [item.case_id for item in CASES]


def test_run_needs_a_database_and_known_cases(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Missing DATABASE_URL and unknown case IDs stop before any work."""
    assert main(["run"]) == 2
    monkeypatch.setenv(
        "DATABASE_URL", "postgresql+psycopg://user:pw@127.0.0.1:1/never_used"
    )
    assert main(["run", "--case", "no-such-case"]) == 2
    assert "no-such-case" in capsys.readouterr().err


def test_report_states_limits_and_marks_failures(tmp_path: Path) -> None:
    """A failed case shows expected and observed; limits are always written."""
    sample = EvalCase(
        "sample-case",
        Category.RETRY,
        "A sample | with a pipe.",
        {"status": "delivered"},
        lambda _context: {},
    )
    report = EvalReport(
        "RUN1",
        datetime(2026, 10, 24, tzinfo=UTC),
        0,
        (
            CaseResult(sample, {"status": "delivered"}, True, 5, None),
            CaseResult(sample, {}, False, 7, "RuntimeError: boom"),
        ),
    )
    json_path, markdown_path = write_report(report, tmp_path / "reports")
    data = json.loads(json_path.read_text())
    assert (data["total"], data["passed"], data["failed"]) == (2, 1, 1)
    assert data["limitations"] == list(LIMITATIONS)
    assert "not evidence of production readiness" in data["statement"]
    text = markdown_path.read_text()
    assert "FAIL RuntimeError: boom" in text and "## Limitations" in text
    assert "1 of 2 cases passed" in text
