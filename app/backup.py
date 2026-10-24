"""Export, restore and verify the fictional learning data of one database.

``python -m app.backup export [DIR]`` writes one canonical JSON Lines file per
table, rows ordered by primary key, and a manifest with the schema revision,
row counts and SHA-256 of every file. All tables are read in one
``REPEATABLE READ`` transaction, so the files describe a single moment.

``restore DIR`` loads a backup into an empty database at the same revision in
one transaction. Before committing, it exports the restored tables again in
memory and compares them with the manifest; any difference rolls everything
back. ``verify DIR`` compares a live database with a backup, and ``check``
re-reads stored versions, document bytes and reconciliation snapshots and
confirms their hashes and results.

A backup contains document bytes, employees and token hashes. Even with
fictional data it belongs in ``var/`` (ignored by Git), never in a commit.
"""

import argparse
import base64
import hashlib
import json
import math
import os
import re
import sys
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, cast
from uuid import UUID

from sqlalchemy import Column, Connection, Engine, Table, bindparam, select, text
from sqlalchemy import types as sqltypes
from sqlalchemy.exc import DataError, IntegrityError, OperationalError, ProgrammingError

from app import __version__
from app.application.errors import ApplicationError
from app.config import Settings
from app.infrastructure.db.models import APPEND_ONLY_TABLES, Base
from app.infrastructure.db.session import (
    build_document_service,
    build_reconciliation_service,
    build_workflow_service,
    create_database_engine,
)

FORMAT = "ewa-backup/1"
MANIFEST = "manifest.json"
DEFAULT_DIRECTORY = Path("var/backups")
_BATCH = 500


class BackupError(Exception):
    """A backup cannot be written, read or restored as asked."""


@dataclass(frozen=True, slots=True)
class TableDigest:
    """Row count and SHA-256 of a table's canonical lines."""

    rows: int
    sha256: str


def _tables() -> list[Table]:
    """Every application table, parents before children."""
    return list(Base.metadata.sorted_tables)


def _encode(value: object) -> object:
    """A JSON value that decodes back to exactly the stored value."""
    if isinstance(value, Decimal | UUID):
        return str(value)
    if isinstance(value, datetime):
        # One time zone for every server, so equal instants hash equally.
        return value.astimezone(UTC).isoformat()
    if isinstance(value, bytes | memoryview):
        return base64.b64encode(bytes(value)).decode("ascii")
    return value


def _decode_value(kind: sqltypes.TypeEngine[Any], value: object) -> object:
    """Decode only the JSON types this schema exports, without lossy coercion."""
    if isinstance(kind, sqltypes.Numeric):
        if not isinstance(value, str):
            raise ValueError("Expected a decimal string")
        amount = Decimal(value)
        if not amount.is_finite():
            raise ValueError("Expected a finite decimal")
        return amount
    if isinstance(kind, sqltypes.DateTime):
        if not isinstance(value, str):
            raise ValueError("Expected a timestamp string")
        instant = datetime.fromisoformat(value)
        if instant.utcoffset() is None:
            raise ValueError("Expected a timestamp with a time zone")
        return instant
    if isinstance(kind, sqltypes.Uuid):
        if not isinstance(value, str):
            raise ValueError("Expected a UUID string")
        return UUID(value)
    if isinstance(kind, sqltypes.LargeBinary):
        if not isinstance(value, str):
            raise ValueError("Expected base64 text")
        return base64.b64decode(value, validate=True)
    if isinstance(kind, sqltypes.Boolean):
        if type(value) is not bool:
            raise ValueError("Expected a JSON boolean")
        return value
    if isinstance(kind, sqltypes.Integer):
        if type(value) is not int:
            raise ValueError("Expected a JSON integer")
        return value
    if isinstance(kind, sqltypes.String):
        if not isinstance(value, str) or (
            kind.length is not None and len(value) > kind.length
        ):
            raise ValueError("Expected text within the column's length limit")
        return value
    if isinstance(kind, sqltypes.ARRAY):
        if not isinstance(value, list):
            raise ValueError("Expected a JSON array")
        return [_decode_value(kind.item_type, item) for item in value]
    if isinstance(kind, sqltypes.JSON):
        return value
    raise ValueError("Unsupported backup column type")


def _decode(column: Column[Any], value: object) -> object:
    """Reject invalid or missing values before passing a backup row to SQL."""
    if value is None:
        if not column.nullable:
            raise BackupError(f"{column.table.name}.{column.name} cannot be null.")
        return None
    try:
        return _decode_value(column.type, value)
    except (ValueError, InvalidOperation, OverflowError) as error:
        raise BackupError(
            f"Invalid value for {column.table.name}.{column.name}."
        ) from error


def _json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    """Reject duplicate keys instead of silently keeping a damaged row's last value."""
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON key")
        result[key] = value
    return result


def _invalid_constant(value: str) -> object:
    """Keep nonstandard NaN and Infinity out of manifests and JSONB values."""
    raise ValueError("Nonfinite JSON constant")


def _json_float(value: str) -> float:
    """Reject JSON exponents that overflow Python's finite number representation."""
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("Nonfinite JSON number")
    return number


def _json_value(content: str | bytes) -> object:
    """Parse standard JSON without duplicate keys or nonfinite numeric constants."""
    return json.loads(
        content,
        object_pairs_hook=_json_object,
        parse_constant=_invalid_constant,
        parse_float=_json_float,
    )


def _file_rows(table: Table, path: Path) -> Iterator[tuple[bytes, dict[str, object]]]:
    """Validate every JSON line's exact columns and types while streaming the file."""
    columns = {column.name: column for column in table.columns}
    try:
        with path.open("rb") as handle:
            for number, line in enumerate(handle, start=1):
                try:
                    document = _json_value(line)
                except ValueError as error:
                    raise BackupError(
                        f"{path.name} line {number} is not valid JSON."
                    ) from error
                if (
                    not line.endswith(b"\n")
                    or not isinstance(document, dict)
                    or set(document) != set(columns)
                ):
                    raise BackupError(
                        f"{path.name} line {number} must contain exactly this "
                        "table's columns and end with a newline."
                    )
                yield (
                    line,
                    {
                        name: _decode(column, document[name])
                        for name, column in columns.items()
                    },
                )
    except OSError as error:
        raise BackupError(f"Cannot read {path.name}.") from error


def _lines(connection: Connection, table: Table) -> Iterator[bytes]:
    """Canonical JSON lines: sorted keys, no spaces, primary-key order."""
    ordered = select(table).order_by(*table.primary_key.columns)
    for row in connection.execute(ordered).mappings():
        document = {key: _encode(value) for key, value in row.items()}
        yield (
            json.dumps(
                document, sort_keys=True, separators=(",", ":"), ensure_ascii=False
            ).encode("utf-8")
            + b"\n"
        )


def _digest(lines: Iterator[bytes]) -> TableDigest:
    """Count and hash lines without keeping them."""
    digest = hashlib.sha256()
    count = 0
    for line in lines:
        digest.update(line)
        count += 1
    return TableDigest(count, digest.hexdigest())


def _revision(connection: Connection) -> str:
    """The Alembic revision of the database."""
    return str(
        connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
    )


def digests(connection: Connection) -> dict[str, TableDigest]:
    """Row counts and hashes of every table, as an export would write them."""
    return {table.name: _digest(_lines(connection, table)) for table in _tables()}


def _snapshot_connection(engine: Engine) -> Connection:
    """A read-only connection that sees one consistent snapshot."""
    connection = engine.connect().execution_options(isolation_level="REPEATABLE READ")
    connection.execute(text("SET TRANSACTION READ ONLY"))
    return connection


def export_database(engine: Engine, directory: Path) -> dict[str, Any]:
    """Write every table and the manifest into a new private directory."""
    if directory.exists():
        if not directory.is_dir():
            raise BackupError(f"{directory} is not a directory.")
        if any(directory.iterdir()):
            raise BackupError(f"{directory} is not empty; choose a new directory.")
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    # mkdir's mode does not change an existing empty directory's permissions.
    directory.chmod(0o700)
    tables: dict[str, dict[str, object]] = {}
    with _snapshot_connection(engine) as connection:
        revision = _revision(connection)
        for table in _tables():
            path = directory / f"{table.name}.jsonl"
            digest = hashlib.sha256()
            count = 0
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "wb") as handle:
                for line in _lines(connection, table):
                    handle.write(line)
                    digest.update(line)
                    count += 1
            tables[table.name] = {
                "file": path.name,
                "rows": count,
                "sha256": digest.hexdigest(),
            }
        connection.rollback()
    manifest = {
        "format": FORMAT,
        "app_version": __version__,
        "alembic_revision": revision,
        "created_at": datetime.now(UTC).isoformat(),
        "tables": tables,
    }
    descriptor = os.open(
        directory / MANIFEST, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600
    )
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        json.dump(manifest, handle, indent=2, sort_keys=True)
        handle.write("\n")
    return manifest


def _manifest_schema(value: object) -> dict[str, Any]:
    """Validate manifest fields before any file name or row metadata is trusted."""
    fields = {"format", "app_version", "alembic_revision", "created_at", "tables"}
    if not isinstance(value, dict) or set(value) != fields:
        raise BackupError("Manifest must contain exactly the format's required fields.")
    if value["format"] != FORMAT:
        raise BackupError(f"Unsupported backup format {value['format']!r}.")
    if (
        not isinstance(value["app_version"], str)
        or not value["app_version"].strip()
        or len(value["app_version"]) > 128
    ):
        raise BackupError("Manifest app_version must be nonempty text.")
    revision = value["alembic_revision"]
    if (
        not isinstance(revision, str)
        or re.fullmatch(r"[A-Za-z0-9_]{1,32}", revision) is None
    ):
        raise BackupError("Manifest alembic_revision is invalid.")
    try:
        if not isinstance(value["created_at"], str):
            raise ValueError("Timestamp must be text")
        if datetime.fromisoformat(value["created_at"]).utcoffset() is None:
            raise ValueError("Timestamp requires a time zone")
    except (ValueError, OverflowError) as error:
        raise BackupError(
            "Manifest created_at must be a timestamp with a time zone."
        ) from error
    tables = value["tables"]
    expected = {table.name for table in _tables()}
    if not isinstance(tables, dict) or set(tables) != expected:
        raise BackupError("The backup's tables differ from this schema.")
    for name, entry in tables.items():
        if not isinstance(entry, dict) or set(entry) != {"file", "rows", "sha256"}:
            raise BackupError(f"Manifest entry for {name} is invalid.")
        if entry["file"] != f"{name}.jsonl":
            raise BackupError(f"Manifest file for {name} must be {name}.jsonl.")
        if type(entry["rows"]) is not int or entry["rows"] < 0:
            raise BackupError(
                f"Manifest rows for {name} must be a nonnegative integer."
            )
        if (
            not isinstance(entry["sha256"], str)
            or re.fullmatch(r"[0-9a-f]{64}", entry["sha256"]) is None
        ):
            raise BackupError(f"Manifest SHA-256 for {name} is invalid.")
    return cast(dict[str, Any], value)


def read_manifest(directory: Path) -> dict[str, Any]:
    """Validate manifest, every file's digest and each row's schema without a DB."""
    try:
        manifest = _manifest_schema(
            _json_value((directory / MANIFEST).read_text(encoding="utf-8"))
        )
    except (OSError, ValueError) as error:
        raise BackupError(f"No readable {MANIFEST} in {directory}.") from error
    for table in _tables():
        entry = manifest["tables"][table.name]
        path = directory / f"{table.name}.jsonl"
        digest = hashlib.sha256()
        count = 0
        for line, _row in _file_rows(table, path):
            digest.update(line)
            count += 1
        if digest.hexdigest() != entry["sha256"]:
            raise BackupError(
                f"{path.name} does not match its SHA-256 in the manifest."
            )
        if count != entry["rows"]:
            raise BackupError(f"{path.name} does not have {entry['rows']} rows.")
    return manifest


def _differences(
    manifest: dict[str, Any], found: dict[str, TableDigest]
) -> list[dict[str, object]]:
    """Tables whose rows or hashes differ from the manifest."""
    problems: list[dict[str, object]] = []
    for name, entry in manifest["tables"].items():
        actual = found[name]
        if (actual.rows, actual.sha256) != (entry["rows"], entry["sha256"]):
            problems.append(
                {
                    "table": name,
                    "backup_rows": entry["rows"],
                    "database_rows": actual.rows,
                    "same_content": actual.sha256 == entry["sha256"],
                }
            )
    return problems


def _insert_sql(connection: Connection, table: Table) -> Any:
    """INSERT with typed parameters; identity columns keep their stored values."""
    quote = connection.dialect.identifier_preparer.quote
    names = [column.name for column in table.columns]
    overriding = (
        " OVERRIDING SYSTEM VALUE"
        if any(column.identity is not None for column in table.columns)
        else ""
    )
    statement = text(
        f"INSERT INTO {quote(table.name)} ({', '.join(quote(n) for n in names)})"
        f"{overriding} VALUES ({', '.join(f':{n}' for n in names)})"
    )
    return statement.bindparams(
        *(bindparam(column.name, type_=column.type) for column in table.columns)
    )


def late_columns() -> dict[str, tuple[str, ...]]:
    """Foreign-key columns that point to a table loaded later.

    ``workflows`` and its snapshots reference each other, so no table order
    satisfies every key. Such columns are inserted as NULL and filled in after
    every table is loaded. That needs a nullable column on a table that
    allows UPDATE; any other cycle makes the schema unrestorable.
    """
    tables = _tables()
    order = {table.name: index for index, table in enumerate(tables)}
    late: dict[str, list[str]] = {}
    for table in tables:
        for key in table.foreign_keys:
            if order[key.column.table.name] < order[table.name]:
                continue
            if not key.parent.nullable or table.name in APPEND_ONLY_TABLES:
                raise BackupError(
                    f"{table.name}.{key.parent.name} closes a key cycle that "
                    "cannot be filled in after loading."
                )
            late.setdefault(table.name, []).append(key.parent.name)
    return {name: tuple(columns) for name, columns in late.items()}


def _load(
    connection: Connection, table: Table, path: Path, deferred: tuple[str, ...]
) -> list[dict[str, object]]:
    """Insert a table's lines in batches; return the deferred values to set."""
    statement = _insert_sql(connection, table)
    keys = [column.name for column in table.primary_key.columns]
    later: list[dict[str, object]] = []
    batch: list[dict[str, object]] = []
    for _line, row in _file_rows(table, path):
        if any(row[name] is not None for name in deferred):
            later.append({name: row[name] for name in (*keys, *deferred)})
        batch.append({**row, **dict.fromkeys(deferred)})
        if len(batch) == _BATCH:
            connection.execute(statement, batch)
            batch = []
    if batch:
        connection.execute(statement, batch)
    return later


def _fill_late(
    connection: Connection,
    table: Table,
    deferred: tuple[str, ...],
    rows: list[dict[str, object]],
) -> None:
    """Set the deferred foreign keys once their targets exist."""
    quote = connection.dialect.identifier_preparer.quote
    keys = [column.name for column in table.primary_key.columns]
    statement = text(
        f"UPDATE {quote(table.name)} SET "
        + ", ".join(f"{quote(name)} = :{name}" for name in deferred)
        + " WHERE "
        + " AND ".join(f"{quote(name)} = :{name}" for name in keys)
    ).bindparams(
        *(
            bindparam(name, type_=table.columns[name].type)
            for name in (*keys, *deferred)
        )
    )
    for start in range(0, len(rows), _BATCH):
        connection.execute(statement, rows[start : start + _BATCH])


def _continue_identity(
    connection: Connection, table: Table, column: Column[Any]
) -> None:
    """Restart the identity sequence transactionally after the restored values."""
    quote = connection.dialect.identifier_preparer.quote
    name, target = quote(column.name), quote(table.name)
    last = connection.execute(text(f"SELECT MAX({name}) FROM {target}")).scalar_one()
    sequence_schema, sequence_name = connection.execute(
        text(
            "SELECT n.nspname, c.relname FROM pg_class c "
            "JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE c.oid = CAST(pg_get_serial_sequence(:table, :column) AS regclass)"
        ),
        {"table": table.name, "column": column.name},
    ).one()
    next_value = 1 if last is None else int(last) + 1
    sequence = f"{quote(sequence_schema)}.{quote(sequence_name)}"
    # Unlike setval(), ALTER SEQUENCE RESTART is rolled back on transaction failure.
    connection.execute(text(f"ALTER SEQUENCE {sequence} RESTART WITH {next_value}"))


def _restore(connection: Connection, directory: Path, manifest: dict[str, Any]) -> str:
    """Lock out writers, load and verify every table, then restart identity values."""
    revision = _revision(connection)
    if revision != manifest["alembic_revision"]:
        raise BackupError(
            f"The database is at revision {revision}, the backup at "
            f"{manifest['alembic_revision']}; run alembic upgrade to match."
        )
    tables = _tables()
    quote = connection.dialect.identifier_preparer.quote
    try:
        # Prevent both concurrent restores and a live writer committing after the
        # final digest. NOWAIT gives the operator a clear refusal instead of hanging.
        connection.execute(
            text(
                f"LOCK TABLE {', '.join(quote(table.name) for table in tables)} "
                "IN SHARE ROW EXCLUSIVE MODE NOWAIT"
            )
        )
    except OperationalError as error:
        if getattr(error.orig, "sqlstate", None) != "55P03":
            raise
        raise BackupError(
            "Restore target is in use; stop database writers and retry."
        ) from error
    filled = [
        table.name
        for table in tables
        if connection.execute(select(table).limit(1)).first() is not None
    ]
    if filled:
        raise BackupError(
            "Restore needs an empty database; these tables have rows: "
            + ", ".join(filled)
        )
    deferred = late_columns()
    later: dict[str, list[dict[str, object]]] = {}
    for table in tables:
        later[table.name] = _load(
            connection,
            table,
            directory / f"{table.name}.jsonl",
            deferred.get(table.name, ()),
        )
    for table in tables:
        if table.name in deferred and later[table.name]:
            _fill_late(connection, table, deferred[table.name], later[table.name])
    problems = _differences(manifest, digests(connection))
    if problems:
        raise BackupError(f"Restored data differs from the backup: {problems}")
    for table in tables:
        for column in table.columns:
            if column.identity is not None:
                _continue_identity(connection, table, column)
    return revision


def restore_database(engine: Engine, directory: Path) -> dict[str, Any]:
    """Load a backup into an empty database; commit only if it matches exactly."""
    manifest = read_manifest(directory)
    try:
        with engine.begin() as connection:
            revision = _restore(connection, directory, manifest)
    except (DataError, IntegrityError) as error:
        raise BackupError(
            "Backup rows violate the database schema; no rows were restored."
        ) from error
    return {
        "restored_from": str(directory),
        "alembic_revision": revision,
        "rows": {name: entry["rows"] for name, entry in manifest["tables"].items()},
        "matches_manifest": True,
    }


def verify_database(engine: Engine, directory: Path) -> dict[str, Any]:
    """Compare a live database with a backup, table by table."""
    manifest = read_manifest(directory)
    with _snapshot_connection(engine) as connection:
        revision = _revision(connection)
        problems = _differences(manifest, digests(connection))
        connection.rollback()
    return {
        "backup": str(directory),
        "same_revision": revision == manifest["alembic_revision"],
        "differences": problems,
        "matches": not problems and revision == manifest["alembic_revision"],
    }


def check_integrity(engine: Engine) -> dict[str, Any]:
    """Re-read everything that carries a hash or a reproducible result."""
    workflows = build_workflow_service(engine)
    documents = build_document_service(engine)
    reconciliations = build_reconciliation_service(engine)
    with engine.connect() as connection:
        workflow_ids = list(
            connection.execute(text("SELECT workflow_id FROM workflows")).scalars()
        )
        document_ids = list(
            connection.execute(text("SELECT id FROM source_documents")).scalars()
        )
        snapshot_ids = list(
            connection.execute(
                text("SELECT id FROM reconciliation_snapshots")
            ).scalars()
        )
    problems: list[dict[str, str]] = []

    def attempt(kind: str, key: object, action: Any) -> None:
        """Run one check and record a failure instead of stopping."""
        try:
            if action() is False:
                problems.append({"kind": kind, "id": str(key), "code": "mismatch"})
        except ApplicationError as error:
            problems.append({"kind": kind, "id": str(key), "code": error.code})

    for workflow_id in workflow_ids:
        attempt(
            "workflow", workflow_id, lambda w=workflow_id: workflows.get_workflow(w)
        )
    for document_id in document_ids:
        attempt("document", document_id, lambda d=document_id: documents.get_content(d))
    for snapshot_id in snapshot_ids:
        attempt(
            "reconciliation",
            snapshot_id,
            lambda s=snapshot_id: (
                reconciliations.reproduce(s) == reconciliations.get_snapshot(s).result
            ),
        )
    return {
        "workflows": len(workflow_ids),
        "documents": len(document_ids),
        "reconciliation_snapshots": len(snapshot_ids),
        "problems": problems,
        "ok": not problems,
    }


def _engine() -> Engine | None:
    """Create an engine from DATABASE_URL, or report why it cannot."""
    settings = Settings()
    if settings.database_url is None:
        print("Set DATABASE_URL to use backups.", file=sys.stderr)
        return None
    try:
        return create_database_engine(settings.database_url.get_secret_value())
    except ValueError as error:
        print(str(error), file=sys.stderr)
        return None


def main(argv: Sequence[str] | None = None) -> int:
    """Parse a command, run it and print JSON, or a one-line error."""
    parser = argparse.ArgumentParser(
        prog="python -m app.backup",
        description="Export, restore and verify the fictional learning data.",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    export = commands.add_parser("export", help="write a new backup directory")
    export.add_argument("directory", type=Path, nargs="?")
    restore = commands.add_parser(
        "restore", help="load a backup into an empty database"
    )
    restore.add_argument("directory", type=Path)
    verify = commands.add_parser("verify", help="compare the database with a backup")
    verify.add_argument("directory", type=Path)
    commands.add_parser("check", help="re-check hashes and reproducible results")
    arguments = parser.parse_args(argv)

    engine = _engine()
    if engine is None:
        return 2
    try:
        if arguments.command == "export":
            stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
            directory = arguments.directory or DEFAULT_DIRECTORY / f"backup-{stamp}"
            manifest = export_database(engine, directory)
            output: dict[str, Any] = {
                "directory": str(directory),
                "alembic_revision": manifest["alembic_revision"],
                "rows": {k: v["rows"] for k, v in manifest["tables"].items()},
            }
            ok = True
        elif arguments.command == "restore":
            output = restore_database(engine, arguments.directory)
            ok = True
        elif arguments.command == "verify":
            output = verify_database(engine, arguments.directory)
            ok = bool(output["matches"])
        else:
            output = check_integrity(engine)
            ok = bool(output["ok"])
    except BackupError as error:
        print(f"backup_error: {error}", file=sys.stderr)
        return 1
    except OSError as error:
        print(
            f"backup_error: Cannot access backup files: {error.strerror}.",
            file=sys.stderr,
        )
        return 1
    except OperationalError:
        print("Cannot connect to the database in DATABASE_URL.", file=sys.stderr)
        return 1
    except ProgrammingError:
        print("Schema is missing: run `alembic upgrade head` first.", file=sys.stderr)
        return 1
    finally:
        engine.dispose()
    print(json.dumps(output, indent=2))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
