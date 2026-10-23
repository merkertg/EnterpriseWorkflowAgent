# Engineering approach

## Product scenario

Enterprise Workflow Agent is a staged learning project for reviewing a supplier
invoice against a structured purchase order (PO). Its examples use fictional organizations,
documents, users and amounts. The intended workflow is:

1. Receive a document and identify its source.
2. Extract candidate fields and retain evidence for each important field.
3. Validate the fields and reconcile the invoice with the PO using deterministic
   rules, retaining the exact input versions in the result.
4. Show discrepancies and request an authorized employee's decision.
5. Record the decision and, in a later stage, demonstrate the next step through
   a signed event delivered to a fixed local demo receiver.

This is two-document reconciliation between an invoice and a purchase order.
Goods receipt and three-way matching are outside the learning scenario.

An approval completes the learning workflow. It does not transfer money, notify a
real supplier or modify a production ERP system.

## Delivery strategy

Stages 01–04 are implemented. Stage 01 establishes a small FastAPI service,
environment settings, tests, static checks and documentation. Stage 02 adds a
standard-library domain with immutable purchasing models, exact money, seven
basic rule findings and in-memory workflow transitions.
`uv run --locked python -m app.demo`
demonstrates three fictional scenarios without any external service. Stage 03
persists workflows in PostgreSQL with migrations, hashed input versions,
validation snapshots, audit, optimistic locking and idempotency;
`uv run --locked python -m app.persistence_demo` exercises it. Stage 04 accepts
bounded text and text-layer PDFs, extracts fields with verified source spans
and is exercised by `uv run --locked python -m app.documents_cli demo`.
The default local port is `8003`. The initial endpoint is `GET /health`; with
default settings it returns:

```json
{"status": "ok", "service": "enterprise-workflow-agent"}
```

The `service` field follows `APP_NAME`. `APP_ENV` is restricted to `development`,
`test` or `production`; `APP_DEBUG` defaults to `false`. This endpoint establishes
that the HTTP application can respond. It does not certify a database, document
pipeline, AI provider or business workflow.

The [roadmap](ROADMAP.md) defines later stages and their completion criteria.
Future capabilities are planned until their code, tests and documentation exist.
[VERIFICATION.md](VERIFICATION.md) records actual checks and limitations; a
workflow file alone is not evidence of a successful GitHub Actions run.

## Architecture

Use a modular monolith: one deployable Python application with explicit internal
boundaries. This keeps a complete request easy to trace while allowing external
systems to be replaced by adapters. Do not create empty abstraction layers only
to match a future directory diagram.

The implemented application contains:

```text
app/
  main.py                 # Application creation and route registration
  config.py               # Validated environment settings
  api/
    routes/health.py      # HTTP endpoint
    schemas/health.py     # Response contract
  domain/                 # Money, documents, rules, transitions, content hashes
  application/            # WorkflowService, records and persistence ports
  infrastructure/db/      # SQLAlchemy tables, repositories, engine and unit of work
  infrastructure/documents/   # Text decoding and the isolated PDF worker
  infrastructure/extraction/  # Deterministic field extractor
  demo.py                 # Fictional offline scenarios; no HTTP or storage
  persistence_demo.py     # Fictional PostgreSQL scenario and read-back command
  samples.py              # Fictional text/PDF samples and a tiny PDF writer
  documents_cli.py        # Document intake, evidence and demo commands
migrations/               # Alembic environment and revisions
tests/
```

Domain, application and database infrastructure boundaries exist now; a test
checks that inner layers import no outer frameworks. Add further adapters when
their behavior is introduced:

| Boundary | Responsibility | Dependencies |
| --- | --- | --- |
| `domain/` | Typed business objects, money, rules and allowed state transitions | Python and explicit domain types; no HTTP, database or model SDK |
| `application/` | Use cases, authorization requirements and transaction orchestration | Domain and ports for persistence, extraction and events |
| `infrastructure/` | SQLAlchemy repositories, PDF/text extraction and the deterministic field extractor (implemented); model providers and event delivery (planned) | Implements application ports |
| `api/` | HTTP schemas, authentication context, request validation and error mapping | Application use cases |

The composition root wires adapters into use cases. Domain decisions must not
depend on an HTTP handler, database session or LLM response format. Introduce
interfaces at actual external boundaries rather than for every class.

## AI and deterministic control

The model proposes structured fields and explanations. Ordinary Python code
validates schemas, computes amounts, applies policy, checks roles and authorizes
state transitions. Use `Decimal` in Python and PostgreSQL `NUMERIC` for money,
with explicit currency, precision and rounding rules.

Missing evidence, an unsupported currency or ambiguous extraction requires
review. A plausible explanation is not proof that a field is correct. Documents
are untrusted data and cannot grant instructions, permissions or tool access.

The first extraction implementation uses a deterministic parser and generated
fictional samples ([ADR 0004](adr/0004-document-intake-and-field-provenance.md)).
Every extractor only proposes fields; `assess_extraction` accepts a value only
if the stored page text at each cited span equals it, enforces a strict schema
and canonical formats, and sends incomplete results to review. PDFs are parsed
in a separate isolated process with a hard timeout.
The agent runtime starts with a fake provider, then optionally gains a real
provider adapter. Constrain tool names, arguments, output sizes, execution time
and total steps. Approval and payment commands are not agent tools. Tool calls
go through the same server-side validation as other application commands.

## State, persistence and side effects

Stage 02 first establishes the state machine in memory. State names and business
rules are defined in code and tested before persistence is added. Approval
transitions may be tested as domain commands, but an approval HTTP endpoint is
not exposed before user identity and role checks are introduced.

Current money values use an explicit two-decimal demo policy for USD, EUR and
UZS. Constructors reject floats, nonfinite values, invalid scale and out-of-range
values. Line products use HALF_UP once before summation, inside a private Decimal
context independent of a caller's precision, rounding or traps. Basic validation
does not yet compare individual PO lines, taxes, tolerances or invoice history.

A workflow retains the exact immutable invoice, PO and policy used by its check.
Contradictory states and stale or fabricated findings are rejected. Revision
keeps the invoice identity, resets the state and discards the old check. These
in-memory references are not persistent version IDs or cryptographic hashes.
HumanDecision records caller-supplied metadata; authenticated identity and roles
remain Stage 06 work.

Stage 03 implements transactional persistence, an audit trail, optimistic
concurrency and command idempotency ([ADR 0003](adr/0003-postgresql-persistence-audit-idempotency.md)).
Invoices, purchase orders and policies are stored as append-only versions in
typed tables with `NUMERIC` money and a SHA-256 hash of canonical content,
re-verified on every load. A workflow pins explicit version IDs; each check
stores an immutable snapshot of those IDs, hashes and findings. One transaction
writes the idempotency record, snapshot, workflow update and audit event. Since
Stage 04 an invoice version can also come from a stored document whose
extraction records the source of every field.
The Stage 05 reconciliation snapshot will add extracted-field versions and
detailed results. Historical snapshots retain their original inputs instead of
silently following the latest PO or policy. Stage 03 does not yet flag a pinned
version as outdated when a newer one exists.

An employee's approval references one reconciliation snapshot. Immediately before
the transition, the application verifies that its source, PO and policy versions
and hashes still match the applicable inputs. This check and the approval share
a transaction. Changed inputs require a new reconciliation and a new employee
decision. Tests change the PO and policy after calculation and ensure that a
stale result cannot be approved, including a change concurrent with approval.

Stage 06 records a future event in an outbox within the same transaction as the
employee's decision. Stage 09 must deliver that event to a fixed local demo
receiver through a signed sandbox webhook; documents and API requests cannot
select its destination. Delivery is retryable and may be repeated; the receiver
deduplicates by event ID. Do not claim exactly-once network delivery.

Keep logs and audit records useful without storing raw document text, personal
data or secrets. Test database configuration is separate: integration tests read
only `TEST_DATABASE_URL` and reject a database whose name does not end in `_test`.

## Quality and documentation

Python 3.12 and `uv` provide the initial environment and dependency workflow.
Use pytest for behavior, Ruff for linting/formatting and strict mypy for type
checks. Default tests are offline; later provider and HTTP integrations use
fakes or mocked transports. Report real-provider checks separately.

Test contracts and failure cases: invalid settings, illegal transitions, exact
money calculations, authorization, repeat commands, concurrency and bounded
provider failures. Broaden checks as each capability appears. File limits and
access checks start with the feature that needs them; security is not deferred
until the hardening stage.

Keep the README and task instructions in English. Provide detailed learning
guides, the roadmap, the development log and verification reports in Russian.
Architecture records remain in English. Each completed stage updates the relevant
guide and evidence. Bilingual PDF guides are generated by `scripts/build_guide.py`
from paired texts, code facts and `verification.json`; a stored source
fingerprint and a test keep them from silently falling behind the code.

## Current exclusions

The HTTP API currently exposes only liveness. HTTP document upload, detailed
reconciliation, authentication, HTTP workflow commands, an AI runtime, manual
field correction and a user interface remain unimplemented. The wider project does not
promise production readiness, real payments, autonomous approval or unrestricted
code execution. OCR, multi-tenant operation and production ERP connectors require
separate decisions and acceptance criteria.

## Tooling references

- [FastAPI testing](https://fastapi.tiangolo.com/tutorial/testing/) describes
  in-process request tests.
- [Pydantic Settings](https://docs.pydantic.dev/latest/concepts/pydantic_settings/)
  documents environment and dotenv configuration.
- [uv locking and syncing](https://docs.astral.sh/uv/concepts/projects/sync/)
  explains the checked-in lock and `--locked` commands.
- [SQLAlchemy ORM](https://docs.sqlalchemy.org/en/21/orm/) and
  [Alembic](https://alembic.sqlalchemy.org/en/latest/) document mappings and
  migrations.
