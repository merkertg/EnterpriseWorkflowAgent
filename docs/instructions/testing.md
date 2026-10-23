# Testing and verification

[README](../../README.md) · [Stage 01 explanation](../STAGE_01.md)

Run from the repository root:

```bash
uv sync --locked
uv run --locked ruff check .
uv run --locked ruff format --check .
uv run --locked mypy app tests scripts migrations
uv run --locked pytest
uv run --locked python scripts/check_docs.py
```

Ruff checks Python issues and formatting; `--check` does not rewrite files.
mypy checks type annotations in strict mode. pytest checks runtime behavior.
The documentation script checks local Markdown link targets.

The default run needs no database, Docker, model key or running Uvicorn;
FastAPI `TestClient` calls the application in the test process. PostgreSQL
integration tests in `tests/persistence/` are marked `postgres` and are
skipped unless the process environment provides a disposable database:

```bash
TEST_DATABASE_URL=postgresql+psycopg://enterprise_workflow:YOUR_PASSWORD@127.0.0.1:5432/enterprise_workflow_test uv run --locked pytest
```

`TEST_DATABASE_URL` is never read from `.env`. Its database name must end in
`_test`; other names stop the session with an error. Each session downgrades
and re-applies the real migrations, and each test truncates all tables.
`REQUIRE_TEST_DATABASE=1` turns a missing URL into a failure (CI uses it).
Select groups with `pytest -m postgres` or `pytest -m "not postgres"`.
Read pytest's summary: `skipped` means the database tests did not run.

To check the schema against the models, run `uv run --locked alembic check`
with `DATABASE_URL` pointing at a migrated database. To verify the API's
network path as well, start the server as described in
[Quickstart](quickstart.md) and run:

```bash
curl -i http://127.0.0.1:8003/health
```

Expected default response: HTTP `200` with
`{"status":"ok","service":"enterprise-workflow-agent"}`.
`/docs`, `/redoc`, and `/openapi.json` should also be available.

Tests cover the health contract, configuration and application construction.
Domain tests add money/quantity boundaries, exact independent expected totals,
caller Decimal context isolation, invalid documents, ordered findings, every
allowed and forbidden state transition, stale snapshots and input revision.
The CLI is also run with `python -S` to verify that the domain/demo need no
third-party packages and do not load web application settings.
Recorded test counts and actual command outcomes belong in
[VERIFICATION.md](../VERIFICATION.md), with a machine-readable summary in
[verification.json](../guide/verification.json). Do not treat expected output
in a guide as evidence of a completed run.

Stage 03 tests add canonical hashes, URL and test-database guards, argument
validation before any transaction, import boundaries, migration round trips and
model/schema equality, `NUMERIC` types, append-only triggers, check constraints,
version numbering and hash verification, atomic rollback, version conflicts,
idempotent replay and key reuse, concurrent duplicates and competing commands,
preserved historical snapshots, a new connection pool and the demo CLI in
separate processes.

Stage 04 tests add intake limits and rejection codes, the field schema and
evidence verification (including a fabricating extractor), exact parser
offsets, synonyms, ambiguity and ignored instruction text, the isolated PDF
worker (pages, encryption, malformed input, missing text, a killed slow worker,
unusable answers), reproducible samples and the documents CLI. Their
PostgreSQL tests cover storage, re-upload reuse, a shared invoice version,
review-only incomplete extractions, no writes on refusal, starting a workflow
from an extraction, tamper detection, concurrent uploads and the CLI in
separate processes. PDF tests start short-lived Python subprocesses; no extra
service is needed.

Run the fictional scenarios directly with
`uv run --locked python -m app.demo`; see [domain usage](domain.md). A successful
basic check does not prove detailed reconciliation or authenticated approval.

[GitHub Actions](../CI_CD.md) repeats the configured checks. Local success
does not confirm a remote run; inspect the result for the relevant commit.
