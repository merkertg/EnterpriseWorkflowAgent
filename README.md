# Enterprise Workflow Agent

A staged Python learning project for a corporate document workflow. The target
scenario reviews a supplier invoice against a purchase order: receive a
document, extract fields with source evidence, apply corporate rules, show
discrepancies, obtain an employee's decision and record the next process step.
All examples will use fictional data.

**Current scope: Stages 01–04 — API foundation, domain, PostgreSQL storage and
document intake.**
The repository contains a FastAPI liveness endpoint, validated settings, exact
money and purchasing models, seven basic validation reasons and an immutable
workflow. Stage 03 persists it in PostgreSQL through Alembic migrations, with
append-only hashed input versions, validation snapshots, a transactional audit
trail, optimistic locking and idempotent commands. Stage 04 accepts bounded
UTF-8 text and text-layer PDFs, extracts invoice fields deterministically and
records the exact page and character span behind every value; incomplete
extractions wait for review instead of producing an invoice. Isolated tests,
PostgreSQL integration tests, Ruff, strict mypy, a locked environment and a
GitHub Actions workflow cover this work. Actual results and unverified items are
recorded in [verification](docs/VERIFICATION.md). Detailed reconciliation,
authenticated approvals, HTTP workflow and upload endpoints and an LLM are
planned.

The planned design is a modular monolith. Deterministic code calculates amounts,
checks rules and controls state transitions. The AI assistant proposes fields and
explanations; authorized employees make approval decisions. The tutorial does not
initiate payments or act on real corporate systems.

**Visual guides:** [Russian PDF](docs/guide/enterprise-workflow-agent-guide.pdf)
and [English PDF](docs/guide/enterprise-workflow-agent-guide-en.pdf), 22 pages
each, with diagrams for stages 01–04.

**По-русски:** основа API — [этап 01](docs/STAGE_01.md), предметная модель,
правила и состояния — [этап 02](docs/STAGE_02.md), хранение, транзакции и
аудит — [этап 03](docs/STAGE_03.md), документы и происхождение полей —
[этап 04](docs/STAGE_04.md).
Все этапы и критерии готовности — в [дорожной карте](docs/ROADMAP.md).

## Quickstart

Requirements: [uv](https://docs.astral.sh/uv/getting-started/installation/) and
Python 3.12. Run commands from this repository root. `uv` can install the matching
Python if it is missing.

```bash
uv sync --locked
uv run --locked uvicorn app.main:app --reload --host 127.0.0.1 --port 8003
```

In another terminal:

```bash
curl http://127.0.0.1:8003/health
```

Default response:

```json
{"status":"ok","service":"enterprise-workflow-agent"}
```

Open [Swagger UI](http://127.0.0.1:8003/docs) or
[OpenAPI JSON](http://127.0.0.1:8003/openapi.json). The API needs no database,
model key or Docker. `/health` checks only that the API responds.

Optional settings: `APP_NAME`, `APP_ENV` (`development`, `test`, `production`),
`APP_DEBUG` (default `false`) and `DATABASE_URL` (Stage 03 commands only). They can come from an optional `.env`, based on
[.env.example](.env.example), or process environment variables. Explicit factory
settings take priority, then environment variables, `.env`, and code defaults.
`APP_ENV=production` is a validated label; it does not configure production
security or deployment. Restart after changing settings.

## Domain demonstration

```bash
uv run --locked python -m app.demo
```

The offline demo prints three fictional outcomes as JSON:

- A `30.00 USD` invoice passes basic checks, then receives an explicit demo
  decision: `received → awaiting_approval → approved`.
- A declared `31.00 USD` against line totals and an order of `30.00 USD` produces
  two findings and stays in `needs_review`.
- A missing purchase order produces `missing_purchase_order` and requires review.

Amounts remain decimal strings. No files, database, credentials or web server are
needed. A caller-provided `HumanDecision` is educational metadata, not verified
identity or authorization. The HTTP API still exposes only `/health`.

Basic checks validate references, supplier, currency, self-total and an order
total ceiling. Detailed SKU/quantity/price matching, taxes, tolerances and duplicate
invoice detection belong to Stage 05. Passing these basic checks alone is not
proof of a fully reconciled invoice. See [domain usage](docs/instructions/domain.md).

## PostgreSQL persistence

Stage 03 needs PostgreSQL 18 (verified with 18.3) and a `DATABASE_URL` such as
`postgresql+psycopg://enterprise_workflow:YOUR_PASSWORD@127.0.0.1:5432/enterprise_workflow`
in the environment or an untracked `.env`:

```bash
uv run --locked alembic upgrade head
uv run --locked python -m app.persistence_demo
```

The demo records fictional invoice, PO and policy versions, then runs start,
check, an idempotent replay, a stale approval rejected with `version_conflict`,
new PO and policy versions, revise, a re-check needing review and a rejection.
It prints both validation snapshots and the audit trail; the earlier snapshot
still references PO version 1. Read a run back in a new process with
`uv run --locked python -m app.persistence_demo show WORKFLOW_ID`.

Each command commits its idempotency record, snapshot, workflow update and
audit event in one transaction. `actor_id` and `source` are caller-declared, not
authenticated. A newer PO version does not yet mark a pinned result stale; that
is Stage 05/06 work. See [persistence](docs/instructions/persistence.md).

## Documents and field provenance

```bash
uv run --locked python -m app.documents_cli write-samples var/samples
uv run --locked python -m app.documents_cli ingest var/samples/clean_invoice.pdf
uv run --locked python -m app.documents_cli evidence DOCUMENT_ID declared_total
uv run --locked python -m app.documents_cli demo
```

Files are accepted only as UTF-8 text or PDF with a text layer, up to 1 MiB,
20 pages and 200 000 characters; format comes from the bytes, not the name.
PDFs are parsed by pypdf in a separate `python -I` process that is killed after
5 seconds. Refused files (`unsupported_type`, `too_large`, `no_text_layer`,
`invalid_text_encoding` and others) are never stored, and `ingest` reports them
without a database.

A deterministic `Label: value` parser proposes fields; the domain accepts a
value only if the text at each cited span equals it exactly, and only a
complete, well-formed extraction becomes an invoice version that can start a
workflow. `evidence` prints the page, line number, offsets and quote. Text
inside a document is data: an "approve this invoice" sentence changes nothing.
Requires `DATABASE_URL` and migration `0002` for storage. See
[documents](docs/instructions/documents.md).

## Stages

| Stage | Deliverable | Status |
| --- | --- | --- |
| 01 | API foundation, settings, tests, checks and documentation | Implemented; see verification |
| 02 | Domain objects, workflow state machine and basic rules | Implemented; in-memory demo only |
| 03 | PostgreSQL, migrations, transactional audit and idempotency | Implemented; PostgreSQL verified locally |
| 04 | Safe text/PDF ingestion and fields with provenance | Implemented; verified locally |
| 05 | Deterministic invoice reconciliation and discrepancy reports | Planned |
| 06 | Employee approvals, roles and transactional outbox | Planned |
| 07 | Bounded AI tools, demo provider and optional real model | Planned |
| 08 | Streamlit interface through the HTTP API | Planned |
| 09 | Security, failure scenarios, evals and local sandbox event delivery | Planned |
| 10 | Docker, expanded CI and reproducible release | Planned |

The [roadmap](docs/ROADMAP.md) defines the scope and acceptance criteria for each
stage. Later modules are created with their first working feature.

## Checks

```bash
uv run --locked ruff check .
uv run --locked ruff format --check .
uv run --locked mypy app tests scripts migrations
uv run --locked pytest
uv run --locked python scripts/check_docs.py
```

Rebuild both PDF guides after changing code, migrations, `uv.lock` or
`docs/guide/verification.json`; a test fails if they are stale:

```bash
uv run --locked --group guide python scripts/build_guide.py
```

Tests use temporary directories and in-process HTTP requests and never read a
developer's `.env`. PostgreSQL integration tests run only when the process
environment sets `TEST_DATABASE_URL` to a disposable database whose name ends in
`_test`; otherwise they are reported as skipped. See
[testing](docs/instructions/testing.md). CI starts a PostgreSQL service and is
configured in [ci.yml](.github/workflows/ci.yml); a successful remote run has
not been verified.

## Documentation

| Purpose | Source |
| --- | --- |
| Stage plan and completion criteria (RU) | [ROADMAP](docs/ROADMAP.md) |
| Detailed learning guides (RU) | [STAGE 01](docs/STAGE_01.md), [STAGE 02](docs/STAGE_02.md), [STAGE 03](docs/STAGE_03.md), [STAGE 04](docs/STAGE_04.md) |
| Short task instructions (EN) | [Quickstart](docs/instructions/quickstart.md), [API](docs/instructions/usage.md), [domain](docs/instructions/domain.md), [persistence](docs/instructions/persistence.md), [documents](docs/instructions/documents.md), [testing](docs/instructions/testing.md), [project structure](docs/instructions/project-structure.md) |
| Design and decisions (EN) | [Approach](docs/APPROACH.md), [ADR 0001](docs/adr/0001-modular-monolith-and-tooling.md), [ADR 0002](docs/adr/0002-domain-rules-and-workflow.md), [ADR 0003](docs/adr/0003-postgresql-persistence-audit-idempotency.md), [ADR 0004](docs/adr/0004-document-intake-and-field-provenance.md) |
| CI explanation (RU) | [CI/CD](docs/CI_CD.md) |
| Work completed and check evidence (RU) | [Development log](docs/DEVELOPMENT_LOG.md), [verification](docs/VERIFICATION.md) |
| Visual PDF guides (RU and EN) | [Russian PDF](docs/guide/enterprise-workflow-agent-guide.pdf), [English PDF](docs/guide/enterprise-workflow-agent-guide-en.pdf), [how they are built](docs/guide/README.md) |
| Machine-readable status | [Verification JSON](docs/guide/verification.json) |
| Contributor rules | [AGENTS.md](AGENTS.md) |
