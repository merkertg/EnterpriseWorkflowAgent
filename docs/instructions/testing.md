# Testing and verification

[README](../../README.md) · [Stage 01 explanation](../STAGE_01.md)

Run from the repository root:

```bash
uv sync --locked --group ui
uv run --locked ruff check .
uv run --locked ruff format --check .
uv run --locked --group ui mypy app tests scripts migrations ui sandbox
uv run --locked --group ui pytest
uv run --locked python scripts/check_docs.py
```

The `ui` group installs Streamlit for the Stage 08 interface. Without it,
`uv run --locked mypy app tests scripts migrations sandbox` and `uv run --locked
pytest` still work: the two interface test modules that need Streamlit are
reported as skipped modules, so read the pytest summary.

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
`/docs`, `/redoc`, and `/openapi.json` should also be available. With
`DATABASE_URL` set, also check `GET /me` without a token (`401`) and with a
token from `python -m app.users_cli token` (`200`).

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

Stage 05 tests cover inclusive tolerance boundaries, price differences that
add up, HALF_UP line rounding, quantities and partial billing, unknown lines,
headers and currency, missing rules, orders and documents, duplicates,
determinism, rule validation and hashes. Their PostgreSQL tests cover pinned
inputs, snapshot reuse, quoted sources, staleness after order and rule
changes, reproduction, re-reconciliation after `revise`, duplicates and
rejection, invoices without documents or orders, edited rules, concurrent
reconciliations and the CLI in separate processes.

Stage 06 tests without a database cover the role-permission matrix, unit
scoping, `Principal` validation, token shape and hashing, refusal of malformed
tokens without opening a transaction, required business unit and reason, the
503 answer without `DATABASE_URL` and the OpenAPI security description. Their
PostgreSQL tests cover approval with outbox and audit links, idempotent replay
without a second event, rejection events, refusals by role, unit and four eyes
that write nothing, other-unit reads, a new order or rule version between
reconciliation and approval, unmatched and foreign snapshots, deactivation and
revocation, rollback of decision and outbox after an injected audit failure,
writers waiting on the approval's advisory lock, the HTTP API through
`TestClient` (401, `/me`, the full flow, stale snapshot reasons, strict bodies,
upload limits, foreign units) and the users CLI and decisions demo in separate
processes.

Stage 07 tests without a database drive the agent loop with fake tools and
scripted providers: a trace without content, results linked by call ID, seven
refused calls (unknown tool, five kinds of invalid arguments, another
workflow), the refusal, provider-call and tool-call limits, provider and tool
timeouts, the result size limit, provider refusals, exceptions and invalid
answers, every branch of `review_draft`, the schema validator and limits, the
model field extractor (correct, fabricated and unusable output) and the demo
provider's plan. Their PostgreSQL tests cover a draft that changes nothing but
the trace, a document instruction that is refused and never stored, an
explanation returned but not stored, drafts checked against stored facts
including real staleness, rights for runs, tools and traces, shortened and
marked document values, model fields through document intake, the agent HTTP
endpoints and the demo in separate processes.

Stage 08 tests check the interface's HTTP client without a server (http(s)
only, token and key headers, quoted paths, raw uploads, error details,
unreachable servers without the token in messages, 404 as "nothing yet",
stable keys and table rows), the Streamlit page headless with a fake client
through `streamlit.testing.v1.AppTest` (token prompt, bad URL, 401, empty
list, missing document and reconciliation, refused approval with reasons,
replaced agent draft, refused and review-only uploads), and the page end to
end against the real API and PostgreSQL: the full scenario, a repeated start
form answered from the stored result, a click from an outdated page refused
with one outbox event, rights, staleness and recovery, a document
instruction and the assistant. API tests cover the new workflow list and
document endpoints; the setup command runs twice in separate processes.

Stage 09 tests without a database cover signing and canonical bodies,
backoff, exhaustion and requeue on an in-memory outbox, unsafe retry
policies, the webhook sender over real loopback HTTP (2xx with the
duplicate flag, 503, wrong secret, timeout, closed port, refused redirect,
ignored proxy variables, non-loopback URLs), the receiver's size, header,
timestamp, signature, body and conflict checks and failure modes, settings
validation of the URL and secret, 411/413 for command bodies, the PDF
worker's reported limits, text cap, CPU limit and signal deaths, the agent's
hourly quota, the page's delivery notices and the evaluation catalogue and
report. With PostgreSQL they cover delivery through the API, a crash after
sending (no loss, one receiver action), `SKIP LOCKED` between two workers,
exhaustion, requeue and counts, the append-only attempt log, the migration
back-fill, the quota with `429`, the worker CLI and demo in separate
processes, the full evaluation suite and an exhausted delivery shown on the
page. The memory-limit test runs only on Linux and is reported as skipped
elsewhere.

Stage 10 tests without a database check the release files: digest-pinned
base images, a non-root user and `--locked` installs in the `Dockerfile`;
loopback-only ports, required secrets, the worker in the receiver's network
namespace and the CI database digest in `compose.yaml`; the `.dockerignore`
exclusions; one version in `pyproject.toml`, the package and the image tags;
the restorable key cycle; and that `app.backup` and `app.smoke` stop without
`DATABASE_URL`. With PostgreSQL they cover a full backup round trip (export,
empty schema, restore, verify, integrity, new work after restore), refusals
for a non-empty target, a changed file, another revision and a reused
directory, an integrity check that finds edited document bytes, and the
smoke check against a real HTTP server, receiver and worker thread. The
Docker stack itself is checked by hand and by the CI `compose` job; see
[release](release.md).

`tests/test_guide_pdfs.py` checks both PDF guides with pypdf: the source
fingerprint stored in each PDF must match a fresh hash of the generator,
`docs/guide/verification.json`, `app/`, `ui/`, `sandbox/`, migrations and `uv.lock`; every section
title must be present; the English guide must contain no Cyrillic. If it
fails, rebuild with `uv run --locked --group guide python scripts/build_guide.py`
and inspect every page as described in the [guide README](../guide/README.md).

Run the fictional scenarios directly with
`uv run --locked python -m app.demo`; see [domain usage](domain.md). A successful
basic check does not prove detailed reconciliation; approval additionally
requires a fresh matched reconciliation and an authorized employee.

[GitHub Actions](../CI_CD.md) repeats the configured checks. Local success
does not confirm a remote run; inspect the result for the relevant commit.
