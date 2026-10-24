"""The whole evaluation suite against the test database."""

from pathlib import Path

import pytest
from sqlalchemy import Engine

from app.evals.cases import Category
from app.evals.runner import run_cases, write_report

pytestmark = pytest.mark.postgres


def test_every_case_meets_its_expectation(engine: Engine, tmp_path: Path) -> None:
    """Expected and observed outcomes are equal for every case."""
    report = run_cases(engine)
    failed = {
        result.case.case_id: (result.case.expected, result.observed, result.error)
        for result in report.results
        if not result.passed
    }
    assert failed == {}
    assert set(report.categories()) == {item.value for item in Category}
    json_path, markdown_path = write_report(report, tmp_path)
    assert json_path.exists() and "## Limitations" in markdown_path.read_text()


def test_a_single_case_can_be_run(engine: Engine) -> None:
    """--case selects cases; others are not run."""
    report = run_cases(engine, ["malicious-oversized"])
    [result] = report.results
    assert result.passed and result.case.case_id == "malicious-oversized"
