"""Damaged manifests and typed rows are refused before opening a database."""

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import create_engine

from app import __version__
from app import backup as backup_module
from app.backup import FORMAT, MANIFEST, BackupError, read_manifest
from app.infrastructure.db.models import Base

STAMP = "2026-10-24T00:00:00+00:00"
IDENTIFIER = "00000000-0000-4000-8000-000000000001"


@pytest.fixture
def backup(tmp_path: Path) -> Path:
    """Create a complete empty fictional export without connecting to PostgreSQL."""
    directory = tmp_path / "backup"
    directory.mkdir()
    entries: dict[str, object] = {}
    for table in Base.metadata.sorted_tables:
        (directory / f"{table.name}.jsonl").write_bytes(b"")
        entries[table.name] = {
            "file": f"{table.name}.jsonl",
            "rows": 0,
            "sha256": hashlib.sha256(b"").hexdigest(),
        }
    manifest = {
        "format": FORMAT,
        "app_version": __version__,
        "alembic_revision": "0006",
        "created_at": STAMP,
        "tables": entries,
    }
    (directory / MANIFEST).write_text(json.dumps(manifest), encoding="utf-8")
    return directory


def _manifest(directory: Path) -> dict[str, Any]:
    """Read the test manifest so a case can deliberately damage one field."""
    value: dict[str, Any] = json.loads((directory / MANIFEST).read_text())
    return value


def _write_manifest(directory: Path, value: object) -> None:
    """Write a deliberately malformed or repaired test manifest."""
    (directory / MANIFEST).write_text(json.dumps(value), encoding="utf-8")


def _write_rows(directory: Path, table: str, content: bytes) -> None:
    """Update bytes and their digest together so row-schema checks are exercised."""
    (directory / f"{table}.jsonl").write_bytes(content)
    manifest = _manifest(directory)
    manifest["tables"][table]["rows"] = content.count(b"\n")
    manifest["tables"][table]["sha256"] = hashlib.sha256(content).hexdigest()
    _write_manifest(directory, manifest)


def _row(directory: Path, table: str, value: dict[str, object]) -> None:
    """Write one test row with a matching manifest digest."""
    _write_rows(directory, table, json.dumps(value).encode() + b"\n")


def _invoice() -> dict[str, object]:
    """A typed invoice-header row whose references need no database to parse."""
    return {
        "id": IDENTIFIER,
        "invoice_id": "FICTIONAL-INVOICE",
        "version_no": 1,
        "content_hash": "0" * 64,
        "supplier_id": "FICTIONAL-SUPPLIER",
        "purchase_order_id": "FICTIONAL-PO",
        "currency": "USD",
        "declared_total": "1.00",
        "created_at": STAMP,
    }


def _user() -> dict[str, object]:
    """A typed fictional employee row covers arrays, booleans and timestamps."""
    return {
        "user_id": "fictional-clerk",
        "display_name": "Fictional clerk",
        "roles": ["clerk"],
        "units": ["FICTIONAL-UNIT"],
        "active": True,
        "created_at": STAMP,
        "updated_at": STAMP,
    }


def test_empty_manifest_is_complete_and_readable(backup: Path) -> None:
    """An export with no rows remains a valid, independently checkable backup."""
    assert read_manifest(backup)["alembic_revision"] == "0006"


@pytest.mark.parametrize("value", [None, [], False, 0, "manifest"])
def test_manifest_root_must_be_an_object(backup: Path, value: object) -> None:
    """Valid JSON with the wrong root type produces a controlled backup error."""
    _write_manifest(backup, value)
    with pytest.raises(BackupError, match="required fields"):
        read_manifest(backup)


@pytest.mark.parametrize(
    "field", ["format", "app_version", "alembic_revision", "created_at", "tables"]
)
def test_manifest_requires_each_documented_field(backup: Path, field: str) -> None:
    """A missing revision or table map must not escape as a KeyError later."""
    value = _manifest(backup)
    del value[field]
    _write_manifest(backup, value)
    with pytest.raises(BackupError, match="required fields"):
        read_manifest(backup)


@pytest.mark.parametrize(
    "field,value",
    [
        ("format", "ewa-backup/other"),
        ("app_version", None),
        ("app_version", " "),
        ("alembic_revision", None),
        ("alembic_revision", ""),
        ("alembic_revision", "../0006"),
        ("created_at", False),
        ("created_at", "not-a-time"),
        ("created_at", "2026-10-24T00:00:00"),
        ("tables", None),
        ("tables", []),
    ],
)
def test_manifest_checks_field_types_and_values(
    backup: Path, field: str, value: object
) -> None:
    """Manifest values are checked before they are used as metadata or identifiers."""
    manifest = _manifest(backup)
    manifest[field] = value
    _write_manifest(backup, manifest)
    with pytest.raises(BackupError):
        read_manifest(backup)


@pytest.mark.parametrize("extra", [True, False])
def test_manifest_table_set_must_match_schema(backup: Path, extra: bool) -> None:
    """An unknown or omitted table cannot be silently skipped during restoration."""
    manifest = _manifest(backup)
    if extra:
        manifest["tables"]["unknown"] = {}
    else:
        del manifest["tables"]["users"]
    _write_manifest(backup, manifest)
    with pytest.raises(BackupError, match="tables differ"):
        read_manifest(backup)


@pytest.mark.parametrize("entry", [None, [], {"rows": 0}])
def test_manifest_table_entry_requires_exact_fields(
    backup: Path, entry: object
) -> None:
    """Malformed table entries are rejected without indexing into an unknown type."""
    manifest = _manifest(backup)
    manifest["tables"]["users"] = entry
    _write_manifest(backup, manifest)
    with pytest.raises(BackupError, match="entry for users"):
        read_manifest(backup)


@pytest.mark.parametrize(
    "field,value",
    [
        ("file", "../users.jsonl"),
        ("file", "/tmp/users.jsonl"),
        ("file", "other.jsonl"),
        ("rows", False),
        ("rows", -1),
        ("rows", "0"),
        ("rows", 0.0),
        ("sha256", None),
        ("sha256", "g" * 64),
        ("sha256", "a" * 63),
        ("sha256", "A" * 64),
    ],
)
def test_manifest_table_metadata_is_strict(
    backup: Path, field: str, value: object
) -> None:
    """File names are fixed, counts are integers and hashes use lowercase SHA-256."""
    manifest = _manifest(backup)
    manifest["tables"]["users"][field] = value
    _write_manifest(backup, manifest)
    with pytest.raises(BackupError):
        read_manifest(backup)


@pytest.mark.parametrize(
    "content",
    [b"{broken}\n", b"null\n", b"[]\n", b"{}\n", b"\xff\n"],
)
def test_matching_digest_does_not_make_an_invalid_row_valid(
    backup: Path, content: bytes
) -> None:
    """An edited manifest cannot turn malformed JSON or wrong columns into a row."""
    _write_rows(backup, "users", content)
    with pytest.raises(BackupError):
        read_manifest(backup)


@pytest.mark.parametrize("change", ["missing", "extra", "newline", "duplicate"])
def test_row_requires_exact_columns_unique_keys_and_newline(
    backup: Path, change: str
) -> None:
    """Each JSON line has one unambiguous complete row with a line terminator."""
    value = _user()
    if change == "missing":
        del value["active"]
    elif change == "extra":
        value["unknown"] = "value"
    content = json.dumps(value).encode() + b"\n"
    if change == "newline":
        content = content.rstrip(b"\n")
    elif change == "duplicate":
        content = content.replace(b'"active": true', b'"active": true,"active": false')
    _write_rows(backup, "users", content)
    with pytest.raises(BackupError):
        read_manifest(backup)


@pytest.mark.parametrize(
    "field,value",
    [
        ("active", "true"),
        ("active", 1),
        ("roles", "clerk"),
        ("roles", [1]),
        ("user_id", 1),
        ("display_name", "x" * 201),
        ("created_at", False),
        ("created_at", "not-a-time"),
        ("created_at", "2026-10-24T00:00:00"),
        ("updated_at", None),
    ],
)
def test_row_rejects_lossy_coercions_and_invalid_timestamp(
    backup: Path, field: str, value: object
) -> None:
    """An integer is not a boolean, a string is not an array, and time needs a zone."""
    row = _user()
    row[field] = value
    _row(backup, "users", row)
    with pytest.raises(BackupError):
        read_manifest(backup)


@pytest.mark.parametrize(
    "field,value",
    [
        ("id", "not-a-uuid"),
        ("id", 1),
        ("version_no", 1.0),
        ("version_no", True),
        ("declared_total", 1.0),
        ("declared_total", "not-a-decimal"),
        ("declared_total", "NaN"),
        ("declared_total", "Infinity"),
    ],
)
def test_row_requires_exact_decimal_uuid_and_integer_encodings(
    backup: Path, field: str, value: object
) -> None:
    """UUIDs and finite decimals are text; version numbers are JSON integers."""
    row = _invoice()
    row[field] = value
    _row(backup, "invoice_versions", row)
    with pytest.raises(BackupError):
        read_manifest(backup)


@pytest.mark.parametrize("value", ["@@@@", "é", 1])
def test_document_bytes_require_valid_base64(backup: Path, value: object) -> None:
    """Invalid base64 must not silently decode to different document bytes."""
    _row(
        backup,
        "source_documents",
        {
            "id": IDENTIFIER,
            "content_hash": "0" * 64,
            "media_type": "text/plain",
            "size_bytes": 1,
            "page_count": 1,
            "declared_name": None,
            "content": value,
            "created_at": STAMP,
        },
    )
    with pytest.raises(BackupError):
        read_manifest(backup)


@pytest.mark.parametrize("constant", [b"NaN", b"Infinity", b"1e999"])
def test_nonfinite_json_values_are_refused(backup: Path, constant: bytes) -> None:
    """Nonfinite numbers cannot be smuggled into a JSONB value or manifest."""
    content = json.dumps(_user()).encode().replace(b"true", constant) + b"\n"
    _write_rows(backup, "users", content)
    with pytest.raises(BackupError, match="not valid JSON"):
        read_manifest(backup)


def test_missing_file_is_a_controlled_error(backup: Path) -> None:
    """A missing table file is reported without an uncaught filesystem exception."""
    (backup / "users.jsonl").unlink()
    with pytest.raises(BackupError, match="Cannot read users.jsonl"):
        read_manifest(backup)


def test_cli_reports_bad_manifest_without_opening_database(
    backup: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A malformed backup exits 1 with a useful one-line message, not a traceback."""
    _write_manifest(backup, [])
    monkeypatch.setattr(backup_module, "_engine", lambda: create_engine("sqlite://"))
    assert backup_module.main(["restore", str(backup)]) == 1
    error = capsys.readouterr().err
    assert error.startswith("backup_error:")
    assert "Traceback" not in error


def test_cli_reports_filesystem_error_without_traceback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """An output path inside a regular file is refused before a database is opened."""
    parent = tmp_path / "regular-file"
    parent.write_text("fictional")
    monkeypatch.setattr(backup_module, "_engine", lambda: create_engine("sqlite://"))
    assert backup_module.main(["export", str(parent / "backup")]) == 1
    error = capsys.readouterr().err
    assert error.startswith("backup_error:")
    assert "Traceback" not in error
