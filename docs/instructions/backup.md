# Backup and restore of the learning data

[README](../../README.md) · [Stage 10 guide (RU)](../STAGE_10.md) ·
[ADR 0010](../adr/0010-containers-and-logical-backups.md) ·
[Release](release.md)

`python -m app.backup` makes a logical, verifiable copy of every application
table and loads it into an empty database. All commands need `DATABASE_URL`.

## Commands

```bash
uv run --locked python -m app.backup export [DIR]     # default var/backups/backup-<UTC time>
uv run --locked python -m app.backup verify DIR       # compare the database with a backup
uv run --locked python -m app.backup restore DIR      # into an empty database at the same revision
uv run --locked python -m app.backup check            # re-check hashes and reproducible results
```

Exit codes: 0 success or a match, 1 a refusal, difference or database
error, 2 missing `DATABASE_URL`.

With Compose, mount a host directory:

```bash
mkdir -p var/backups
docker compose run --rm --no-deps --user "$(id -u):$(id -g)" \
  -v "$PWD/var/backups:/backups" api python -m app.backup export /backups/one
```

## Format

A directory with one `<table>.jsonl` per table and `manifest.json`:

| Item | Detail |
| --- | --- |
| Rows | Ordered by primary key; canonical JSON (sorted keys, no spaces) |
| Values | Decimals and UUIDs as strings, times in UTC, bytes as base64 |
| Manifest | Format `ewa-backup/1`, app version, Alembic revision, row count and SHA-256 per file |
| Permissions | Directory 0700, files 0600 |

The export reads every table in one `REPEATABLE READ`, read-only
transaction, so the files describe a single moment.

## Restore

`restore` checks the manifest's fields, fixed table file names, row types and
every file's digest before connecting. It requires the same Alembic revision
and an empty database. It then works in one transaction:

1. Lock every application table against writers. An active writer or another
   restore is refused immediately; stop API writes and the worker before retrying.
2. Insert the tables parents first, keeping the stored identity values of
   `audit_events` (`OVERRIDING SYSTEM VALUE`).
3. Fill the two `workflows` foreign keys that form a cycle with the snapshot
   tables after all tables are loaded.
4. Export the restored tables again in memory. Commit only if every count
   and hash equals the manifest.
5. Restart identity sequences after their restored maximum. Sequence changes
   use transactional `ALTER SEQUENCE RESTART`, so a failed restore rolls back
   both rows and sequence changes. Malformed data and database constraint
   failures return `backup_error` rather than a traceback.

Restore into a new PostgreSQL database with an upgraded empty schema, or
recreate only a disposable PostgreSQL database/volume. Preserve the receiver's
SQLite volume: it contains event IDs used to prevent duplicate business
actions. Remove neither the entire compose volumes nor receiver data as part
of a PostgreSQL-only restore.

## Checks

- `verify` reports, per table, whether rows and content still equal the
  backup.
- `check` re-reads every workflow (input version hashes) and every document's
  bytes, and recomputes every reconciliation snapshot.

These checks cover the listed PostgreSQL data only. The sandbox receiver's
SQLite database is a separate store and is not exported or restored here;
there is no atomic backup across PostgreSQL and the receiver. Preserve and
manage that store separately when keeping delivery history.

## Safety

A backup contains document bytes, employee records and token hashes, not
token secrets. Keep it under `var/` (ignored by Git) and never commit it. The
manifest detects corrupted or edited files, but it is not signed, so it
cannot prove who made a backup. There is no encryption and no schedule.
