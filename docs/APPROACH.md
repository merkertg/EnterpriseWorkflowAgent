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

Stages 01–09 are implemented. Stage 01 establishes a small FastAPI service,
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
Stage 05 reconciles invoice lines with the order under versioned tolerance
rules ([ADR 0005](adr/0005-deterministic-reconciliation.md)), and
`uv run --locked python -m app.reconciliation_cli demo` exercises it.
Stage 06 adds token-authenticated employees with unit-scoped roles, the
business HTTP API, approval on a fresh matched reconciliation and a
transactional outbox ([ADR 0006](adr/0006-employee-decisions-and-outbox.md));
`uv run --locked python -m app.decisions_demo` exercises it.
Stage 07 adds a bounded review agent behind a model-provider port with
deterministic offline providers ([ADR 0007](adr/0007-bounded-review-agent.md));
`uv run --locked python -m app.agent_demo` exercises it.
Stage 08 adds a Streamlit page that is a plain HTTP client of the API
([ADR 0008](adr/0008-streamlit-client-over-http.md)); start it with
`uv run --locked --group ui python -m streamlit run ui/app.py`.
Stage 09 delivers outbox events to a signed loopback sandbox receiver with
bounded retries, tightens resource and request limits, and adds an
evaluation suite ([ADR 0009](adr/0009-outbox-delivery-and-evaluations.md));
`uv run --locked python -m app.outbox_demo` and
`uv run --locked python -m app.evals run` exercise it.
The default local port is `8003`. The first endpoint is `GET /health`; with
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
    dependencies.py       # Services, bearer authentication, Idempotency-Key
    errors.py             # Stable error codes mapped to HTTP statuses
    routes/health.py      # Liveness endpoint
    routes/business.py    # Documents, workflows, reconciliations, decisions
    schemas/              # Request and response contracts
  domain/                 # Money, documents, rules, transitions, content hashes
  application/            # Use cases, access rules, tokens and persistence ports
  infrastructure/db/      # SQLAlchemy tables, repositories, engine and unit of work
  infrastructure/documents/   # Text decoding and the isolated PDF worker
  infrastructure/extraction/  # Deterministic and model-backed field extractors
  infrastructure/llm/     # Deterministic offline model providers
  infrastructure/webhook/ # Signed outbox sender: loopback only, no proxy or redirect
  evals/                  # Evaluation cases, runner and reports
  demo.py                 # Fictional offline scenarios; no HTTP or storage
  persistence_demo.py     # Fictional PostgreSQL scenario and read-back command
  samples.py              # Fictional text/PDF samples and a tiny PDF writer
  documents_cli.py        # Document intake, evidence and demo commands
  reconciliation_cli.py   # Reconciliation demo and snapshot reports
  users_cli.py            # Employee accounts and API tokens
  decisions_demo.py       # Fictional Stage 06 decision scenario
  agent_demo.py           # Fictional offline Stage 07 agent scenarios
  ui_demo_setup.py        # Fictional employees, tokens and samples for the page
  fictional_desk.py       # Shared fictional setup for demos and evaluations
  outbox_worker.py        # Delivery worker, status and requeue commands
  outbox_demo.py          # Fictional Stage 09 delivery scenarios
ui/                       # Streamlit page and its HTTP client; no app imports
sandbox/                  # Fictional webhook receiver; no app imports
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
| `infrastructure/` | SQLAlchemy repositories, PDF/text extraction, deterministic and model-backed field extractors, deterministic model providers, the signed webhook sender (implemented); a real model adapter (planned) | Implements application ports |
| `api/` | HTTP schemas, bearer authentication, request validation and error mapping (implemented in Stage 06) | Application use cases |

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
Since Stage 07 the agent runtime uses deterministic providers behind a
`ModelProvider` port; a real provider adapter may follow and is verified
separately. Tool names, arguments (strict schemas), result sizes, wall time
per call, provider and tool calls and refusals are bounded. Approval, payment
and code-execution commands are not agent tools. Tools re-check the
employee's rights in their own transactions, and the agent's draft next step
is checked against stored facts before it is shown. Traces keep names, codes,
sizes and hashes, never prompts or document text.

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
Since Stage 06 the application builds the HumanDecision from an authenticated
employee whose role and business unit it has just checked.

Stage 03 implements transactional persistence, an audit trail, optimistic
concurrency and command idempotency ([ADR 0003](adr/0003-postgresql-persistence-audit-idempotency.md)).
Invoices, purchase orders and policies are stored as append-only versions in
typed tables with `NUMERIC` money and a SHA-256 hash of canonical content,
re-verified on every load. A workflow pins explicit version IDs; each check
stores an immutable snapshot of those IDs, hashes and findings. One transaction
writes the idempotency record, snapshot, workflow update and audit event. Since
Stage 04 an invoice version can also come from a stored document whose
extraction records the source of every field.
Stage 05 reconciliation snapshots add the source document hash, the
extraction and a hash of its fields, the rule version, duplicate context and
line-level results. Historical snapshots retain their original inputs instead
of silently following the latest PO or rules; freshness is computed against
the currently applicable versions, and stale snapshots are recomputed exactly
from their own inputs.

Since Stage 06 an employee's approval references one reconciliation snapshot
([ADR 0006](adr/0006-employee-decisions-and-outbox.md)). Inside the approval
transaction the application re-reads the employee's rights, enforces four
eyes, takes advisory locks on the purchase order, rules and invoice-number
keys, and verifies that the snapshot's invoice, order and rule versions and
duplicate context still match the applicable inputs and that it is
`matched`. Writers of new order and rule versions and duplicate starts take
the same locks, so the result cannot change between this check and commit.
Changed inputs require a new reconciliation and a new decision. Tests change
the order or the rules between reconciliation and approval, and show that
these writers wait while the lock is held.

Stage 06 records the decision as an `invoice.approved` or `invoice.rejected`
event in an append-only outbox within the same transaction as the decision and
its audit event, together with a `pending` delivery row. Since Stage 09 a
worker claims one due row at a time with `FOR UPDATE SKIP LOCKED`, sends a
signed request to the fixed loopback receiver set by `SANDBOX_WEBHOOK_URL`
and records every attempt in an append-only log; documents and API requests
cannot select the destination, and the sender ignores proxies and refuses
redirects. Failures retry with 2–16 s backoff and end as `exhausted`, which
the API and the page show. Delivery is at least once; the receiver
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

Since Stage 06 the HTTP API covers document upload, workflow commands,
reconciliation, decisions, audit and outbox reads with bearer tokens. Since Stage 07
a bounded agent runs with deterministic offline providers, and since Stage 08
a Streamlit page uses the same API; since Stage 09 outbox events reach a
local sandbox receiver only. Account management over HTTP, token expiry and
SSO, delivery to any real system, a real model adapter and manual field
correction remain unimplemented. The wider project does not
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
