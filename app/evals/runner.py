"""Run evaluation cases and write a JSON and a Markdown report.

A case passes only when its observation equals the expected one exactly. An
exception inside a case is a failure with the exception's type and message,
never a crash of the whole run. The report always lists the suite's limits.
"""

import json
import secrets
import tempfile
import time
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import Engine

from app.evals.cases import CASES, EvalCase
from app.evals.context import EvalContext
from app.fictional_desk import FictionalDesk, ShiftedClock
from sandbox.receiver import Behaviour, ReceiverStore, create_receiver, serving

LIMITATIONS = (
    "Model behaviour is simulated by deterministic providers (demo-rules/1 and "
    "scripted models). Nothing here measures a real LLM.",
    "The cases are few, fictional and written by the people who wrote the code; "
    "they check known failure modes, not unknown ones.",
    "Everything runs on one machine: PostgreSQL, the API services and a "
    "loopback sandbox receiver with a SQLite file. No network faults beyond "
    "refused connections, error codes and slow answers are simulated.",
    "The PDF worker's memory limit (RLIMIT_AS) is enforced on Linux only; "
    "macOS rejects it, so there only CPU time, wall-clock time and text size "
    "are limited.",
    "Timing cases use margins of 0.2–1 s; a heavily loaded machine can make "
    "them slower but should not change their outcome.",
    "Passing every case is not a claim that the system is ready for production.",
)
PROVIDERS = ("demo-rules/1", "scripted (deterministic)")
STATEMENT = (
    "Results describe this learning project on a fictional evaluation set. "
    "They are not evidence of production readiness."
)


@dataclass(frozen=True, slots=True)
class CaseResult:
    """One case's expected and observed outcome."""

    case: EvalCase
    observed: dict[str, object]
    passed: bool
    duration_ms: int
    error: str | None


def _run_case(item: EvalCase, context: EvalContext) -> CaseResult:
    """Run one case; an exception becomes a failed result."""
    started = time.monotonic()
    try:
        observed = item.run(context)
        error = None
    except Exception as failure:  # Report and continue with the next case.
        observed = {}
        error = f"{type(failure).__name__}: {str(failure)[:200]}"
    duration = int((time.monotonic() - started) * 1000)
    passed = error is None and observed == item.expected
    return CaseResult(item, observed, passed, duration, error)


@dataclass(frozen=True, slots=True)
class EvalReport:
    """Every result of one run, with the run's identity."""

    run: str
    started_at: datetime
    earlier_events: int
    results: tuple[CaseResult, ...]

    @property
    def passed(self) -> int:
        """Cases whose observation equals the expectation."""
        return sum(result.passed for result in self.results)

    def categories(self) -> dict[str, dict[str, int]]:
        """Totals and passes per category, in case order."""
        counts: dict[str, dict[str, int]] = {}
        for result in self.results:
            entry = counts.setdefault(
                result.case.category.value, {"total": 0, "passed": 0}
            )
            entry["total"] += 1
            entry["passed"] += int(result.passed)
        return counts

    def to_json(self) -> dict[str, object]:
        """Summary, every case, the limitations and the statement."""
        return {
            "suite": "enterprise-workflow-agent evaluations",
            "stage": "09",
            "run": self.run,
            "started_at": self.started_at.isoformat(),
            "providers": list(PROVIDERS),
            "earlier_events_sent_to_eval_receiver": self.earlier_events,
            "total": len(self.results),
            "passed": self.passed,
            "failed": len(self.results) - self.passed,
            "by_category": self.categories(),
            "cases": [
                {
                    "case_id": result.case.case_id,
                    "category": result.case.category.value,
                    "description": result.case.description,
                    "expected": result.case.expected,
                    "observed": result.observed,
                    "passed": result.passed,
                    "duration_ms": result.duration_ms,
                    "error": result.error,
                }
                for result in self.results
            ],
            "limitations": list(LIMITATIONS),
            "statement": STATEMENT,
        }


def run_cases(engine: Engine, case_ids: Sequence[str] | None = None) -> EvalReport:
    """Run the selected cases (all by default) against one receiver and desk."""
    known = {item.case_id for item in CASES}
    unknown = sorted(set(case_ids or ()) - known)
    if unknown:
        raise ValueError(f"Unknown case IDs: {', '.join(unknown)}.")
    selected = [item for item in CASES if not case_ids or item.case_id in case_ids]
    started_at = datetime.now(UTC)
    desk = FictionalDesk(engine, "evals")
    secret = secrets.token_urlsafe(32)
    clock = ShiftedClock()
    behaviour = Behaviour()
    with tempfile.TemporaryDirectory(prefix="ewa-evals-") as folder:
        store = ReceiverStore(Path(folder) / "receiver.sqlite3")
        receiver = create_receiver(store, secret, behaviour, clock.now)
        with serving(receiver) as url:
            context = EvalContext(engine, desk, store, behaviour, clock, url, secret)
            earlier = context.flush()
            results = tuple(_run_case(item, context) for item in selected)
    return EvalReport(desk.run, started_at, earlier, results)


def _escape(text: str) -> str:
    """Keep a pipe from ending a Markdown table cell."""
    return text.replace("|", "\\|")


def _cell(value: object) -> str:
    """JSON in a Markdown table cell."""
    return "`" + json.dumps(value, ensure_ascii=False).replace("|", "\\|") + "`"


def markdown(report: EvalReport) -> str:
    """A readable version of the report for people."""
    lines = [
        "# Evaluation report",
        "",
        f"Run `{report.run}` started {report.started_at.isoformat()}. "
        f"Providers: {', '.join(PROVIDERS)}.",
        "",
        f"**{report.passed} of {len(report.results)} cases passed.** {STATEMENT}",
        "",
        "| Category | Passed |",
        "| --- | --- |",
    ]
    for name, counts in report.categories().items():
        lines.append(f"| {name} | {counts['passed']} / {counts['total']} |")
    lines += ["", "## Cases", "", "| Case | Result | Expected | Observed |"]
    lines.append("| --- | --- | --- | --- |")
    for result in report.results:
        verdict = "pass" if result.passed else f"FAIL {result.error or ''}".strip()
        lines.append(
            f"| `{result.case.case_id}`: {_escape(result.case.description)} | "
            f"{_escape(verdict)} | "
            f"{_cell(result.case.expected)} | {_cell(result.observed)} |"
        )
    lines += ["", "## Limitations", ""]
    lines += [f"- {text}" for text in LIMITATIONS]
    return "\n".join(lines) + "\n"


def write_report(report: EvalReport, directory: Path) -> tuple[Path, Path]:
    """Write ``eval-<time>-<run>.json`` and ``.md``; return both paths."""
    directory.mkdir(parents=True, exist_ok=True)
    stamp = report.started_at.strftime("%Y%m%dT%H%M%SZ")
    base = directory / f"eval-{stamp}-{report.run}"
    json_path = base.with_suffix(".json")
    markdown_path = base.with_suffix(".md")
    json_path.write_text(
        json.dumps(report.to_json(), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    markdown_path.write_text(markdown(report), encoding="utf-8")
    return json_path, markdown_path
