"""Backup export, restore into an empty database, verification and integrity."""

import hashlib
import json
from pathlib import Path

import pytest
from alembic import command
from sqlalchemy import Engine, text

from app.application.delivery import WebhookMessage, WebhookResponse
from app.backup import (
    BackupError,
    check_integrity,
    export_database,
    restore_database,
    verify_database,
)
from app.fictional_desk import FictionalDesk
from app.infrastructure.db.models import Base
from app.infrastructure.db.session import build_agent_service, build_delivery_service
from app.infrastructure.llm.demo import DemoRulesProvider
from tests.persistence.conftest import alembic_config

pytestmark = pytest.mark.postgres


class _Accepting:
    """A receiver that accepts every event without a network."""

    def send(self, message: WebhookMessage, timeout_seconds: float) -> WebhookResponse:
        """Answer 200."""
        return WebhookResponse(200, None, False)


def _fill(engine: Engine) -> FictionalDesk:
    """Data in every table family: decisions, documents, deliveries, agent runs."""
    desk = FictionalDesk(engine, "pytest")
    approved = desk.approved("A")
    desk.submit("B", total="31.00")
    build_delivery_service(engine, _Accepting(), "s" * 32).deliver_due()
    build_agent_service(engine, DemoRulesProvider()).run(
        approved, actor=desk.team.approver
    )
    return desk


def _empty_schema(test_database_url: str) -> None:
    """Drop and recreate every table through the migrations."""
    config = alembic_config(test_database_url)
    command.downgrade(config, "base")
    command.upgrade(config, "head")


def _replace_table_file(directory: Path, table: str, lines: list[str]) -> None:
    """Change a table and its manifest digest to test validation after hash checks."""
    content = ("\n".join(lines) + "\n").encode()
    (directory / f"{table}.jsonl").write_bytes(content)
    manifest = json.loads((directory / "manifest.json").read_text())
    manifest["tables"][table]["sha256"] = hashlib.sha256(content).hexdigest()
    manifest["tables"][table]["rows"] = len(lines)
    (directory / "manifest.json").write_text(json.dumps(manifest))


def _assert_empty(engine: Engine) -> None:
    """Check that a failed restore left no row in any application table."""
    with engine.connect() as connection:
        for table in Base.metadata.sorted_tables:
            assert (
                connection.execute(
                    text(f'SELECT count(*) FROM "{table.name}"')
                ).scalar_one()
                == 0
            ), table.name


def test_round_trip_restores_identical_data(
    engine: Engine, test_database_url: str, tmp_path: Path
) -> None:
    """Export, wipe, restore: every table hashes the same and stays usable."""
    _fill(engine)
    backup = tmp_path / "backup"
    backup.mkdir(mode=0o755)
    backup.chmod(0o755)
    manifest = export_database(engine, backup)
    assert manifest["alembic_revision"] == "0006"
    assert manifest["tables"]["audit_events"]["rows"] > 0
    assert oct((backup / "manifest.json").stat().st_mode & 0o777) == "0o600"
    assert oct(backup.stat().st_mode & 0o777) == "0o700"
    assert verify_database(engine, backup)["matches"] is True

    _empty_schema(test_database_url)
    restored = restore_database(engine, backup)
    assert restored["matches_manifest"] is True
    assert verify_database(engine, backup)["matches"] is True
    integrity = check_integrity(engine)
    assert integrity["ok"] and integrity["workflows"] == 2
    assert integrity["reconciliation_snapshots"] == 1

    # Identity values continue after the restored rows; new work is accepted.
    FictionalDesk(engine, "pytest").approved("C")
    after = verify_database(engine, backup)
    assert after["matches"] is False
    assert "audit_events" in {item["table"] for item in after["differences"]}


def test_restore_refuses_unsafe_targets_and_damaged_files(
    engine: Engine, test_database_url: str, tmp_path: Path
) -> None:
    """Non-empty database, changed file, other revision, reused directory."""
    _fill(engine)
    backup = tmp_path / "backup"
    export_database(engine, backup)
    with pytest.raises(BackupError, match="empty database"):
        restore_database(engine, backup)
    with pytest.raises(BackupError, match="not empty"):
        export_database(engine, backup)

    damaged = tmp_path / "damaged"
    export_database(engine, damaged)
    lines = (damaged / "workflows.jsonl").read_text().splitlines()
    lines[0] = lines[0].replace('"approved"', '"rejected"')
    (damaged / "workflows.jsonl").write_text("\n".join(lines) + "\n")
    with pytest.raises(BackupError, match="SHA-256"):
        restore_database(engine, damaged)

    other = tmp_path / "other"
    export_database(engine, other)
    manifest = json.loads((other / "manifest.json").read_text())
    manifest["alembic_revision"] = "0005"
    (other / "manifest.json").write_text(json.dumps(manifest))
    _empty_schema(test_database_url)
    with pytest.raises(BackupError, match="revision"):
        restore_database(engine, other)
    # Nothing was written by the refused restores.
    with engine.connect() as connection:
        assert connection.execute(text("SELECT count(*) FROM workflows")).scalar() == 0


def test_integrity_check_finds_changed_document_bytes(engine: Engine) -> None:
    """Bytes edited behind the application's back are reported, not trusted."""
    _fill(engine)
    with engine.begin() as connection:
        connection.execute(
            text(
                "ALTER TABLE source_documents "
                "DISABLE TRIGGER trg_source_documents_append_only"
            )
        )
        # Same length (a check constraint guards the size), different bytes.
        connection.execute(
            text(
                "UPDATE source_documents "
                "SET content = overlay(content placing '#'::bytea from 1 for 1)"
            )
        )
        connection.execute(
            text(
                "ALTER TABLE source_documents "
                "ENABLE TRIGGER trg_source_documents_append_only"
            )
        )
    report = check_integrity(engine)
    assert report["ok"] is False
    assert {item["kind"] for item in report["problems"]} == {"document"}
    assert {item["code"] for item in report["problems"]} == {"stored_data_integrity"}


def test_constraint_failure_rolls_back_previously_loaded_tables(
    engine: Engine, test_database_url: str, tmp_path: Path
) -> None:
    """Typed but invalid business state fails cleanly and rolls back parent rows."""
    _fill(engine)
    directory = tmp_path / "backup"
    export_database(engine, directory)
    lines = (directory / "workflows.jsonl").read_text().splitlines()
    first = json.loads(lines[0])
    first["state"] = "unknown_state"
    lines[0] = json.dumps(first, sort_keys=True, separators=(",", ":"))
    _replace_table_file(directory, "workflows", lines)
    _empty_schema(test_database_url)
    with pytest.raises(BackupError, match="violate the database schema"):
        restore_database(engine, directory)
    _assert_empty(engine)


def test_final_digest_failure_rolls_back_all_loaded_rows(
    engine: Engine, test_database_url: str, tmp_path: Path
) -> None:
    """A noncanonical row order passes file hashes but cannot commit restored data."""
    _fill(engine)
    directory = tmp_path / "backup"
    export_database(engine, directory)
    lines = (directory / "users.jsonl").read_text().splitlines()
    assert len(lines) > 1
    _replace_table_file(directory, "users", list(reversed(lines)))
    _empty_schema(test_database_url)
    with pytest.raises(BackupError, match="Restored data differs"):
        restore_database(engine, directory)
    _assert_empty(engine)
    with engine.connect() as connection:
        assert tuple(
            connection.execute(
                text("SELECT last_value, is_called FROM audit_events_id_seq")
            ).one()
        ) == (1, False)


def test_restore_refuses_a_concurrent_writer_without_waiting(
    engine: Engine, tmp_path: Path
) -> None:
    """A writer cannot commit extra rows between restoration's digest and commit."""
    directory = tmp_path / "backup"
    export_database(engine, directory)
    with engine.begin() as writer:
        writer.execute(text("LOCK TABLE workflows IN ROW EXCLUSIVE MODE"))
        with pytest.raises(BackupError, match="target is in use"):
            restore_database(engine, directory)
    _assert_empty(engine)
