"""``python -m app.evals``: run the evaluation suite or list its cases.

``run`` needs ``DATABASE_URL``: cases write fictional workflows with a random
run suffix. Reports go to ``var/evals/`` (ignored by Git). The exit code is 0
when every case passed, 1 when any failed, 2 when the setup is missing.
"""

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from sqlalchemy.exc import OperationalError, ProgrammingError

from app.config import Settings
from app.evals.cases import CASES
from app.evals.runner import run_cases, write_report
from app.infrastructure.db.session import create_database_engine

DEFAULT_REPORTS = Path("var/evals")


def main(argv: Sequence[str] | None = None) -> int:
    """Parse a command, run it and print a summary."""
    parser = argparse.ArgumentParser(
        prog="python -m app.evals",
        description="Stage 09 evaluation cases with explicit expected outcomes.",
    )
    commands = parser.add_subparsers(dest="command")
    run = commands.add_parser("run", help="run cases and write reports (default)")
    run.add_argument("--case", action="append", dest="cases", metavar="CASE_ID")
    run.add_argument("--reports", type=Path, default=DEFAULT_REPORTS)
    commands.add_parser("list", help="print case IDs, categories and expectations")
    arguments = parser.parse_args(argv)

    if arguments.command == "list":
        for item in CASES:
            print(
                json.dumps(
                    {
                        "case_id": item.case_id,
                        "category": item.category.value,
                        "expected": item.expected,
                    }
                )
            )
        return 0
    settings = Settings()
    if settings.database_url is None:
        print("Set DATABASE_URL to run the evaluations.", file=sys.stderr)
        return 2
    try:
        engine = create_database_engine(settings.database_url.get_secret_value())
    except ValueError as error:
        print(str(error), file=sys.stderr)
        return 2
    cases = getattr(arguments, "cases", None)
    reports = getattr(arguments, "reports", DEFAULT_REPORTS)
    try:
        report = run_cases(engine, cases)
    except ValueError as error:
        print(str(error), file=sys.stderr)
        return 2
    except OperationalError:
        print("Cannot connect to the database in DATABASE_URL.", file=sys.stderr)
        return 1
    except ProgrammingError:
        print("Schema is missing: run `alembic upgrade head` first.", file=sys.stderr)
        return 1
    finally:
        engine.dispose()
    json_path, markdown_path = write_report(report, reports)
    for result in report.results:
        mark = "pass" if result.passed else "FAIL"
        print(f"{mark}  {result.case.category.value:<18} {result.case.case_id}")
        if not result.passed:
            print(f"      expected {json.dumps(result.case.expected)}")
            print(f"      observed {json.dumps(result.observed)} {result.error or ''}")
    print(
        f"{report.passed}/{len(report.results)} passed. Reports: {json_path}, "
        f"{markdown_path}"
    )
    return 0 if report.passed == len(report.results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
