"""Project facts for the guide, read from code, uv.lock and verification.json.

Everything that can be read from the project is read at build time, so the
PDF cannot drift from enums, limits, tables, migrations or locked versions.
"""

import ast
import json
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.api.errors import ERROR_STATUS
from app.api.routes.business import router as business_router
from app.application.access import ROLE_PERMISSIONS, Permission
from app.application.ports import CommandName, OutboxEventType
from app.application.reconciliation import StaleReason
from app.domain.documents import DocumentLimits, RejectionCode
from app.domain.extraction import (
    HEADER_FIELDS,
    LINE_PARTS,
    FieldStatus,
    ProblemCode,
)
from app.domain.reconciliation import RECONCILIATION_ENGINE, DiscrepancyCode
from app.domain.rules import IssueCode
from app.domain.workflow import WorkflowState
from app.infrastructure.db.models import APPEND_ONLY_TABLES, Base
from app.infrastructure.extraction.labeled_text import EXTRACTOR_NAME

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SHOWN_PACKAGES = (
    "fastapi",
    "pydantic",
    "uvicorn",
    "sqlalchemy",
    "alembic",
    "psycopg",
    "pypdf",
    "pytest",
    "ruff",
    "mypy",
    "reportlab",
)


@dataclass(frozen=True, slots=True)
class Facts:
    """Values shown in the guide, each taken from the project itself."""

    python: str
    versions: dict[str, str]
    lock_packages: int
    issue_codes: tuple[str, ...]
    states: tuple[str, ...]
    commands: tuple[str, ...]
    rejection_codes: tuple[str, ...]
    problem_codes: tuple[str, ...]
    field_statuses: tuple[str, ...]
    header_fields: tuple[str, ...]
    line_parts: tuple[str, ...]
    limits: DocumentLimits
    tables: tuple[str, ...]
    append_only: tuple[str, ...]
    migrations: tuple[str, ...]
    extractor: str
    discrepancy_codes: tuple[str, ...]
    stale_reasons: tuple[str, ...]
    engine: str
    permissions: tuple[str, ...]
    roles: dict[str, tuple[str, ...]]
    outbox_types: tuple[str, ...]
    routes: tuple[tuple[str, str], ...]
    error_statuses: tuple[tuple[str, int], ...]


def read_source(relative: str) -> str:
    """Read a project file so the guide shows real code."""
    return (PROJECT_ROOT / relative).read_text(encoding="utf-8")


def definition(relative: str, name: str) -> str:
    """Return the source of a top-level or nested function or class by name."""
    source = read_source(relative)
    for item in ast.walk(ast.parse(source)):
        if (
            isinstance(item, ast.FunctionDef | ast.ClassDef)
            and item.name == name
            and item.end_lineno is not None
        ):
            first = min([item.lineno] + [d.lineno for d in item.decorator_list])
            lines = source.splitlines()[first - 1 : item.end_lineno]
            indent = len(lines[0]) - len(lines[0].lstrip())
            return "\n".join(line[indent:] for line in lines)
    raise ValueError(f"{name} not found in {relative}")


def load_facts() -> Facts:
    """Collect versions, enum values, limits, tables and migrations."""
    lock = tomllib.loads(read_source("uv.lock"))
    packages: list[dict[str, Any]] = lock["package"]
    versions = {
        str(item["name"]): str(item["version"])
        for item in packages
        if item["name"] in SHOWN_PACKAGES
    }
    missing = set(SHOWN_PACKAGES) - versions.keys()
    if missing:
        raise ValueError(f"uv.lock lacks {sorted(missing)}")
    migrations = tuple(
        path.stem
        for path in sorted((PROJECT_ROOT / "migrations/versions").glob("[0-9]*.py"))
    )
    return Facts(
        python=read_source(".python-version").strip(),
        versions=versions,
        lock_packages=len(packages),
        issue_codes=tuple(item.value for item in IssueCode),
        states=tuple(item.value for item in WorkflowState),
        commands=tuple(item.value for item in CommandName),
        rejection_codes=tuple(item.value for item in RejectionCode),
        problem_codes=tuple(item.value for item in ProblemCode),
        field_statuses=tuple(item.value for item in FieldStatus),
        header_fields=HEADER_FIELDS,
        line_parts=LINE_PARTS,
        limits=DocumentLimits(),
        tables=tuple(table.name for table in Base.metadata.sorted_tables),
        append_only=APPEND_ONLY_TABLES,
        migrations=migrations,
        extractor=EXTRACTOR_NAME,
        discrepancy_codes=tuple(item.value for item in DiscrepancyCode),
        stale_reasons=tuple(item.value for item in StaleReason),
        engine=RECONCILIATION_ENGINE,
        permissions=tuple(item.value for item in Permission),
        roles={
            role.value: tuple(item.value for item in Permission if item in allowed)
            for role, allowed in ROLE_PERMISSIONS.items()
        },
        outbox_types=tuple(item.value for item in OutboxEventType),
        routes=tuple(
            (method, str(getattr(route, "path", "")))
            for route in business_router.routes
            for method in sorted(getattr(route, "methods", ()))
        ),
        error_statuses=tuple((kind.code, status) for kind, status in ERROR_STATUS),
    )


def _non_negative_int(value: object, name: str) -> int:
    """Require a non-negative integer count."""
    if type(value) is not int or value < 0:
        raise ValueError(f"{name}: a non-negative integer is required")
    return value


def load_verification(path: Path) -> dict[str, Any]:
    """Read verification.json and reject missing or inconsistent fields."""
    data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    for key in (
        "stage",
        "implemented_stages",
        "verified_on",
        "git",
        "environment",
        "tests",
        "checks",
        "remote_ci",
    ):
        if key not in data:
            raise ValueError(f"verification.json lacks {key}")
    tests = data["tests"]
    total = _non_negative_int(tests.get("total"), "tests.total")
    with_db = tests.get("with_database", {})
    without_db = tests.get("without_database", {})
    passed = _non_negative_int(with_db.get("passed"), "with_database.passed")
    failed = _non_negative_int(with_db.get("failed"), "with_database.failed")
    skipped = _non_negative_int(with_db.get("skipped"), "with_database.skipped")
    if passed + failed + skipped != total:
        raise ValueError("with_database: passed + failed + skipped != total")
    offline = _non_negative_int(without_db.get("passed"), "without_database.passed")
    postgres = _non_negative_int(tests.get("postgres"), "tests.postgres")
    if (
        _non_negative_int(without_db.get("skipped"), "without_database.skipped")
        != postgres
    ):
        raise ValueError("without_database.skipped must equal tests.postgres")
    if offline + postgres != total:
        raise ValueError("without_database.passed + postgres != total")
    for item in data["checks"]:
        if not isinstance(item.get("command"), str) or item.get("status") not in {
            "passed",
            "failed",
        }:
            raise ValueError("every check needs a command and passed/failed status")
    if data["remote_ci"].get("status") not in {"not_verified", "passed", "failed"}:
        raise ValueError("remote_ci.status must be not_verified, passed or failed")
    return data
