# Using the system

[← README](../../README.md) · [All documentation](../../README.md#documentation)

How to work with every part of the fictional invoice workflow: employees and
tokens, the HTTP API, the domain rules, storage, documents, reconciliation,
decisions, the review agent, outbox delivery, the employee page and the
evaluations. Start the stack first with the [quickstart](quickstart.md).
Detailed explanations in Russian are in the stage guides
([STAGE_01](../STAGE_01.md) … [STAGE_10](../STAGE_10.md)).

## Contents

- [Roles, accounts and tokens](#roles-accounts-and-tokens)
- [HTTP API](#http-api)
- [Domain rules and workflow states](#domain-rules-and-workflow-states)
- [Storage, versions and audit](#storage-versions-and-audit)
- [Documents and field evidence](#documents-and-field-evidence)
- [Reconciliation](#reconciliation)
- [Approval and the outbox](#approval-and-the-outbox)
- [Review agent](#review-agent)
- [Outbox delivery](#outbox-delivery)
- [Employee page](#employee-page)
- [Evaluations](#evaluations)
- [Command-line tools](#command-line-tools)

## Roles, accounts and tokens

| Role | view | submit | reconcile | decide | assist |
| --- | --- | --- | --- | --- | --- |
| `viewer` | yes | — | — | — | — |
| `clerk` | yes | yes | yes | — | yes |
| `approver` | yes | — | yes | yes | yes |

`submit` covers document upload, start, check and revise; `assist` starts the
review agent. A user may hold several roles. Every workflow belongs to one
business unit, chosen at start from the caller's own units. A workflow in
another unit is reported as `not_found`; a missing permission in the caller's
unit is `missing_permission`. Reference data (orders, policies, rules) is
shared and needs only `view`. Workflows created before migration `0004` are in
unit `unassigned`.

Accounts are created only from the command line (needs `DATABASE_URL` and an
upgraded schema):

```bash
uv run --locked python -m app.users_cli create demo-approver --name "Fictional approver" --role approver --unit DEMO-UNIT-A
uv run --locked python -m app.users_cli token demo-approver --label laptop
uv run --locked python -m app.users_cli list
uv run --locked python -m app.users_cli deactivate demo-approver
uv run --locked python -m app.users_cli activate demo-approver
uv run --locked python -m app.users_cli revoke TOKEN_ID
```

`--role` and `--unit` can repeat. `token` prints `ewa_` plus 43 random
characters once; only its SHA-256 is stored. Deactivation and revocation apply
to the next request. Errors print `code: message` and exit with 1; a missing
`DATABASE_URL` exits with 2. For a ready set of four fictional employees with
fresh tokens, reference data and samples, run
`python -m app.ui_demo_setup` (see [Employee page](#employee-page)).

## HTTP API

`/health`, `/docs`, `/redoc` and `/openapi.json` need no credentials. Every
other endpoint needs `DATABASE_URL`, an upgraded schema and a bearer token;
without `DATABASE_URL` they answer `503 database_not_configured`.

```bash
curl -i http://127.0.0.1:8003/health
TOKEN=ewa_...   # printed once by users_cli token
curl -H "Authorization: Bearer $TOKEN" http://127.0.0.1:8003/me
```

`/health` returns `{"status": "ok", "service": "enterprise-workflow-agent"}`;
`service` is the instance's `APP_NAME`. HTTP `200` confirms only that the
application handled the request; it does not check the database. A missing,
malformed, unknown or revoked token, or a deactivated user, gets the same
`401 unauthenticated` with `WWW-Authenticate: Bearer`.

Commands that change a workflow (`POST /workflows`, `check`, `revise`,
`approve`, `reject`) need an `Idempotency-Key` header of 1–60 characters from
letters, digits and `. _ : -`. The server prefixes it with the caller's user
ID. Repeating the same request with the same key returns the stored result
with `"replayed": true`; the same key with other arguments is
`409 idempotency_key_reused`.

| Method and path | Permission | Purpose |
| --- | --- | --- |
| `GET /me` | any user | User ID, roles, units, permissions |
| `POST /documents?name=NAME` | `submit` | Upload raw bytes (`application/octet-stream`); returns document, extraction status, fields, problems and invoice version |
| `GET /reference/{kind}/{business_id}` | `view` | Latest version of `purchase-orders`, `policies` or `reconciliation-rules` |
| `GET /workflows?state=&limit=` | `view` | Recently changed workflows of the caller's units (limit 1–500, default 100) |
| `POST /workflows` | `submit` | Start: `workflow_id`, `business_unit`, `invoice_version_id`, `purchase_order_version_id` (or `null`), `policy_version_id` |
| `GET /workflows/{id}` | `view` | State, version, unit, creator, pinned versions, issue codes, decision |
| `GET /workflows/{id}/document` | `view` | Source document, extraction status, problems and fields with citations; 404 for invoices created in code |
| `POST /workflows/{id}/check` | `submit` | `{"expected_version": N}` |
| `POST /workflows/{id}/revise` | `submit` | Expected version and new version IDs |
| `POST /workflows/{id}/reconciliations` | `reconcile` | `{"rules_version_id": ...}`; returns the report |
| `GET /workflows/{id}/reconciliations/latest` | `view` | Newest report with `current` and `stale_reasons` |
| `GET /reconciliations/{snapshot_id}` | `view` | One report |
| `POST /workflows/{id}/approve` | `decide` | `expected_version`, `reconciliation_snapshot_id`, `reason` |
| `POST /workflows/{id}/reject` | `decide` | `expected_version`, `reason` |
| `GET /workflows/{id}/audit` | `view` | Audit events with actor, snapshot and outbox references |
| `GET /workflows/{id}/outbox` | `view` | Decision events with `delivery` (status, attempts, next attempt, last error) and every attempt |
| `POST /workflows/{id}/agent-runs` | `assist` | Run the bounded review agent (provider `demo-rules/1`); returns the explanation once, the checked draft and the trace |
| `GET /agent-runs/{run_id}` | `view` | A stored run and trace; the explanation is not stored, only its hash and length |

Request bodies reject unknown fields and use strict types (`"2"` is not a
version). Uploads are read only up to 1 MiB; a larger body is `413 too_large`.
Every other `POST`, `PUT` or `PATCH` body must declare a `Content-Length` of
at most 64 KiB (`413 too_large`; `411 length_required` without a length). An
assistant run is refused with `429 agent_run_limit` after 20 runs of the same
workflow in an hour.

```bash
curl -X POST http://127.0.0.1:8003/workflows/WF-1/approve \
  -H "Authorization: Bearer $TOKEN" \
  -H "Idempotency-Key: approve-wf-1" \
  -H "Content-Type: application/json" \
  -d '{"expected_version": 2, "reconciliation_snapshot_id": "SNAPSHOT_ID", "reason": "Fictional invoice matches the order"}'
```

Every error body is `{"error": {"code": "...", "message": "..."}}`, with
`field`, `reasons` or `problems` where relevant. Validation problems list
locations and messages, never the submitted values.

| Status | Codes |
| --- | --- |
| 401 | `unauthenticated` |
| 403 | `missing_permission`, `self_approval_forbidden` |
| 404 | `not_found`, also for another business unit's workflow |
| 409 | `version_conflict`, `invalid_transition`, `stale_validation`, `stale_reconciliation`, `reconciliation_not_matched`, `idempotency_key_reused`, `workflow_exists`, `concurrent_write` |
| 411 | `length_required` |
| 413 | `too_large` |
| 422 | `invalid_request`, document rejection codes, `reconciliation_not_for_workflow`, other domain validation codes |
| 429 | `agent_run_limit` |
| 503 | `database_not_configured`, `database_unavailable` |

[Swagger UI](http://127.0.0.1:8003/docs) lets you authorize with the token and
try each operation; [ReDoc](http://127.0.0.1:8003/redoc) and
[OpenAPI JSON](http://127.0.0.1:8003/openapi.json) describe the contract,
including the bearer scheme and the `Idempotency-Key` header.

## Domain rules and workflow states

Immutable Python domain objects, deterministic baseline checks and the
workflow state machine. `python -m app.demo` prints three fictional outcomes
without a server, `.env`, credentials or network.

**Input contract.**

- Use `Decimal` for money and quantity, built from strings
  (`Decimal("12.50")`), never from a `float`.
- Currencies are `USD`, `EUR` and `UZS`, each with two decimal places. Money
  must be exact in cents (`Decimal("1.005")` is rejected), finite,
  non-negative and at most `999999999999.99`; line and document totals must
  fit too. Price × quantity rounds once per line with `ROUND_HALF_UP`;
  document totals sum the rounded lines. There is no currency conversion.
- Quantity must be finite, positive, at most `999999.999999`, with at most six
  decimal places.
- Documents contain 1–100 line items with unique SKUs; line prices and
  declared totals use the document currency.

**Baseline checks.** `evaluate_invoice(invoice, purchase_order, policy=None)`
uses `ValidationPolicy("basic-demo-v1")` by default and returns the exact
inputs with ordered issues:

| Issue code | Meaning |
| --- | --- |
| `unsupported_currency` | Invoice currency is excluded by the supplied policy |
| `invoice_total_mismatch` | Declared total differs from calculated line totals |
| `missing_purchase_order` | No PO was supplied |
| `purchase_order_reference_mismatch` | Invoice references another PO |
| `supplier_mismatch` | Invoice and PO suppliers differ |
| `currency_mismatch` | Invoice and PO currencies differ; totals are not compared |
| `purchase_order_total_exceeded` | Declared invoice total exceeds the PO total |

**States.** A clean check moves the workflow to `awaiting_approval`; issues
move it to `needs_review`. `approved` and `rejected` need a separate human
decision and are terminal. Revision resets an open workflow to `received`.

| Function | Allowed source states |
| --- | --- |
| `check_workflow(workflow)` | `received`, `needs_review`, `awaiting_approval` |
| `approve_workflow(workflow, decision)` | `awaiting_approval` with a fresh matching clean result |
| `reject_workflow(workflow, decision)` | `received`, `needs_review`, `awaiting_approval` |
| `revise_workflow(workflow, invoice, purchase_order, *, policy=None)` | `received`, `needs_review`, `awaiting_approval` |

Revision keeps the invoice ID; passing `None` as the PO deliberately removes
it, and omitting the policy keeps the current one. The `Workflow` constructor
rejects contradictory state, check and decision combinations. In these
in-memory functions `HumanDecision` is caller-supplied data; stored decisions
come from an authenticated, authorized employee and also need a fresh matched
reconciliation (see [Approval and the outbox](#approval-and-the-outbox)).
Passing the baseline check is not a full reconciliation.

| Module | Responsibility |
| --- | --- |
| `app/domain/money.py` | Money bounds, currency and decimal arithmetic |
| `app/domain/models.py` | Line item, invoice and purchase-order invariants |
| `app/domain/rules.py` | Baseline validation policy, issues and result |
| `app/domain/workflow.py` | Workflow states, snapshots and commands |
| `app/domain/errors.py` | Explicit invalid-input and transition errors |

The domain imports no FastAPI, Pydantic, SQLAlchemy or model SDK.

## Storage, versions and audit

PostgreSQL 18 (verified with 18.3) with Alembic migrations. For a native
setup create a development database and a separate test database whose name
ends in `_test` (see the [quickstart](quickstart.md#run-on-the-host)), then:

```bash
uv run --locked alembic upgrade head
uv run --locked alembic current
uv run --locked alembic check            # must print: No new upgrade operations detected
uv run --locked alembic upgrade head --sql   # preview SQL without connecting
uv run --locked python -m app.persistence_demo
uv run --locked python -m app.persistence_demo show DEMO-WF-xxxxxxxxxxxx
```

`alembic downgrade base` drops every table and its data. Never edit an
applied revision; add a new one. The demo runs start, check, an idempotent
replay, a stale approval rejected with `version_conflict`, new order and
policy versions, revise, a re-check needing review and a rejection; `show`
reads a workflow and its audit trail in a new process. Engines use a 5 s
connect timeout, a 5 s lock timeout and a 30 s statement timeout.

| Rule | Behavior |
| --- | --- |
| Input versions | Identical to the latest version → reused; different → next number; A → B → A creates version 3 |
| Content hash | SHA-256 of canonical JSON; recomputed on every load; a mismatch raises `StoredDataIntegrityError` |
| Pinned inputs | `start`/`revise` pin exact version IDs; `check` evaluates only the pinned versions |
| Transaction | Idempotency record, snapshot, workflow update, outbox event and audit event commit together |
| Optimistic lock | `expected_version` must equal the stored version; also enforced by `UPDATE ... WHERE version = ...` |
| Idempotency | Same key and request → stored result with `replayed=True`; same key, other request → `idempotency_key_reused`; failed commands store nothing |
| Audit | IDs, command, states, versions, issue codes, `actor_id`, `source`, and for decisions the reconciliation snapshot and outbox event; no document text |
| History tables | Versions, snapshots, audit, idempotency, documents, reconciliation, outbox events, agent traces and delivery attempts reject `UPDATE`/`DELETE` via trigger |

```python
from app.application.ports import CommandContext
from app.infrastructure.db.session import build_workflow_service, create_database_engine

service = build_workflow_service(create_database_engine(database_url))
invoice_v = service.record_invoice(invoice)
order_v = service.record_purchase_order(purchase_order)
policy_v = service.record_policy(policy)
service.start(
    "WF-1",
    invoice_version_id=invoice_v.version_id,
    purchase_order_version_id=order_v.version_id,
    policy_version_id=policy_v.version_id,
    business_unit="DEMO-UNIT-A",
    context=CommandContext("a-new-uuid-per-intent", "my-script"),
)
```

Commands accept an optional `actor`; without one (internal tools) nothing is
authorized and `actor_id` stays empty. A newer policy version does not mark a
pinned workflow stale; newer order and rule versions make reconciliation
snapshots stale and block approval. Triggers stop accidental edits; a
database owner can still disable them.

| Revision | Adds |
| --- | --- |
| `0001` | Versions, workflows, validation snapshots, audit, idempotency, append-only triggers |
| `0002` | Source documents, pages, extractions and fields |
| `0003` | Reconciliation rule versions and snapshots |
| `0004` | Users, API token hashes, the outbox and business units |
| `0005` | Agent runs and steps |
| `0006` | Outbox delivery state and the attempt log |

## Documents and field evidence

UTF-8 text and PDFs with a text layer; a deterministic parser extracts invoice
fields and records the exact source span of every value. Upload over HTTP
(`POST /documents`, `submit`) or from the command line:

```bash
uv run --locked python -m app.documents_cli write-samples var/samples
uv run --locked python -m app.documents_cli ingest var/samples/clean_invoice.pdf
uv run --locked python -m app.documents_cli show DOCUMENT_ID
uv run --locked python -m app.documents_cli evidence DOCUMENT_ID declared_total
uv run --locked python -m app.documents_cli demo
```

`write-samples` needs no database and never overwrites files. `ingest` checks
and extracts before connecting, so a refused file is reported without
`DATABASE_URL`; `show`, `evidence`, `demo` and storing need it. Exit codes:
`0` success, `1` rejected file or command error, `2` missing or invalid
`DATABASE_URL`. `var/` is ignored by Git.

**Intake.** Format is detected from bytes: `%PDF-` means PDF; otherwise strict
UTF-8 text without NUL or other control characters (tab, LF, FF and CR are
allowed). The file name is only a sanitized label of at most 255 characters.

| `DocumentLimits` field | Default |
| --- | --- |
| `max_bytes` | 1 048 576 |
| `max_pages` | 20 |
| `max_text_chars` | 200 000 |
| `timeout_seconds` | 5.0 (PDF parsing) |
| `worker_memory_bytes` | 536 870 912 (enforced where the OS supports `RLIMIT_AS`) |

Rejection codes: `empty_document`, `too_large`, `unsupported_type`,
`invalid_text_encoding`, `too_many_pages`, `text_too_long`, `encrypted_pdf`,
`malformed_pdf`, `no_text_layer`, `processing_timeout`, `resource_limit`.
Rejected files are not stored. PDFs are parsed by
`app/infrastructure/documents/pdf_worker.py` in a separate `python -I` process
(bytes on stdin, JSON on stdout) that is killed at the timeout, limits its own
CPU time and, on Linux, its memory, and stops when the text grows too long.

**Fields and assessment.** Fields: `invoice_id`, `supplier_id`,
`purchase_order_id`, `currency`, `declared_total` and, per line `n`,
`lines[n].sku`, `lines[n].description`, `lines[n].quantity`,
`lines[n].unit_price`. Each field is `found`, `missing`, `ambiguous` or
`invalid` and cites spans `Evidence(page, start, end)` into the stored page
text (1-based pages, 0-based offsets, `end` exclusive). `assess_extraction()`
treats every extractor as a proposer:

- each found value must equal the text at every cited span, otherwise
  `unverified_evidence`;
- unknown names give `unexpected_field`; no lines give `no_line_items`;
- `missing`/`ambiguous`/`invalid` give `missing_field`/`ambiguous_field`/`invalid_value`;
- money must be `digits.dd`, quantity a plain decimal, currency `USD`/`EUR`/`UZS`;
  `30,00`, `$30.00`, `1e3`, `12.5` and `GBP` are `invalid_value`;
- domain rule failures (zero quantity, repeated SKU) give `invalid_invoice`.

`complete` yields an invoice version that can start a workflow;
`needs_review` yields none.

**Parser.** `LabeledTextInvoiceParser` (`labeled-text-parser/1`) reads
`Label: value` rows. Labels are case- and space-insensitive with synonyms
(`Invoice number/no/id`, `Supplier ID`/`Supplier`, `Purchase order`/`PO number`,
`Total due`/`Invoice total`/`Total`, `Line`/`Item`). A line row is
`SKU | description | quantity | unit price`. A repeated identical value is
found with several citations; different values are ambiguous. Unknown labels
and free text, including instruction-like sentences, are ignored.

**Storage.** Revision `0002` adds append-only `source_documents`,
`document_pages`, `document_extractions` and `extracted_fields`. Identical
bytes are stored once and return the existing extraction. Reads verify the
byte hash; opening evidence re-checks the quote against the value.

```python
from app.infrastructure.db.session import build_document_service, create_database_engine

service = build_document_service(create_database_engine(database_url))
result = service.ingest(content_bytes, "invoice.pdf")
evidence = service.get_evidence(result.document.document_id, "declared_total")
```

`service.prepare()` (or `prepare_document()`) runs every check without a
database; `service.store(prepared)` writes it.

## Reconciliation

Compares a workflow's pinned invoice and purchase order line by line under a
versioned rule set, stores an immutable snapshot of every input and reports
when that snapshot becomes stale. All arithmetic is deterministic `Decimal`
code.

```bash
uv run --locked python -m app.reconciliation_cli demo
uv run --locked python -m app.reconciliation_cli show SNAPSHOT_ID
```

`demo` uses run-specific invoice, order and rule IDs. `show` prints status,
totals, discrepancies with quoted sources, freshness and whether the snapshot
reproduces. Exit codes: `0` success, `1` command error, `2` missing or invalid
`DATABASE_URL`.

```python
from decimal import Decimal

from app.domain.money import Currency, Money
from app.domain.reconciliation import CurrencyRule, ReconciliationRules
from app.infrastructure.db.session import build_reconciliation_service

usd = CurrencyRule(
    Currency.USD,
    unit_price_tolerance=Money(Decimal("0.05"), Currency.USD),
    total_tolerance=Money(Decimal("0.10"), Currency.USD),
)
service = build_reconciliation_service(engine)
rules_v = service.record_rules(ReconciliationRules("my-rules", (usd,)))
snapshot = service.reconcile("WF-1", rules_version_id=rules_v.version_id)
service.freshness(snapshot.snapshot_id)  # reasons, empty when current
service.reproduce(snapshot.snapshot_id)  # equals snapshot.result
service.report(snapshot.snapshot_id)  # freshness, reproducible, citations
```

Tolerances are absolute and inclusive. `decimal_places` must be `2` and
`rounding` `ROUND_HALF_UP`. `require_source_document` (default `True`) demands
that the invoice came from a complete extraction. Rule sets are versioned like
orders and policies.

Discrepancy codes: `missing_source_document`, `duplicate_invoice`,
`no_rule_for_currency`, `missing_purchase_order`,
`purchase_order_reference_mismatch`, `supplier_mismatch`, `currency_mismatch`,
`unknown_line`, `quantity_exceeds_order`, `quantity_below_order`,
`unit_price_mismatch`, `declared_total_mismatch`, `expected_total_mismatch`.
Each has `field` (e.g. `lines[2].unit_price`), `expected`, `actual`, the applied
`rule` (e.g. `USD.unit_price_tolerance=0.05`) and `sources` (evidence spans).
The expected line total is order price × invoiced quantity, rounded once per
line; `expected_total_mismatch` compares only lines that matched an order SKU.
No discrepancy means `matched`; it is not an approval.

A snapshot records invoice, order and rule versions with hashes, the source
document and extraction hashes, duplicate workflow IDs and the engine
`reconciliation.v1`. Equal inputs return the stored snapshot.

| Stale reason | Meaning |
| --- | --- |
| `workflow_inputs_changed` | The workflow was revised to other versions |
| `purchase_order_changed` | A newer order version exists |
| `purchase_order_available` | An order now exists where none did |
| `rules_changed` | A newer rule version exists |
| `duplicates_changed` | Other workflows for the same invoice changed |

`reproduce` reloads the snapshot's own versions, verifies their hashes and
recomputes; edited rules fail with `stored_data_integrity`. Duplicates are
other non-rejected workflows with the same supplier and invoice number.
Reconciliation does not change workflow state or write audit events.

## Approval and the outbox

Approval runs in one transaction, in this order:

1. idempotency key (a repeat returns the stored result);
2. expected workflow version (`version_conflict`);
3. the user is active and has `decide` in the workflow's unit, re-read with
   `FOR SHARE`;
4. four eyes: the employee who started the workflow cannot approve it
   (`self_approval_forbidden`);
5. the snapshot exists and belongs to this workflow
   (`reconciliation_not_for_workflow`);
6. advisory locks on the purchase order, the rules and the invoice number;
7. freshness against current versions and duplicates
   (`stale_reconciliation` with `reasons`);
8. snapshot status `matched` (`reconciliation_not_matched`);
9. the domain transition from `awaiting_approval`;
10. workflow version, `invoice.approved` outbox event, a `pending` delivery
    and the audit event.

Rejection checks the role, unit and state and writes `invoice.rejected`; it
needs no snapshot. Recording a new order or rule version and starting a
workflow for the same invoice number take the same advisory locks, so they
wait for an approval in progress instead of invalidating it unseen.

`outbox_events` is append-only. Each event has an ID, type, workflow ID and
version, and a JSON payload with IDs, hashes, the business unit and the
deciding user; never document text. The decision's audit event references
the outbox event and, for approvals, the reconciliation snapshot.

```bash
uv run --locked python -m app.decisions_demo
uv run --locked python -m app.decisions_demo show WORKFLOW_ID
```

The demo signs in four fictional employees with tokens and shows: missing
role, other unit, self-approval, stale snapshot after a rule change, stale
workflow version, approval, idempotent replay, invalid transition after
approval, refusal of a snapshot with discrepancies, rejection, and refused
sign-in after revocation and deactivation. Tokens are not printed.

From Python, `WorkflowService.approve(workflow_id, expected_version=...,
reconciliation_snapshot_id=..., reason=..., actor=principal, context=...)` and
`reject(..., reason=..., actor=principal, context=...)` require a `Principal`
from `UserService.authenticate(token)` or `create_user(...).principal()`.

## Review agent

A model calls read-only tools and returns an explanation and a draft next
step. Code validates every call, enforces limits, checks the draft against
stored facts and keeps a trace without document text. The draft never changes
the workflow. Only deterministic offline providers run; no real model is
connected.

```bash
uv run --locked python -m app.agent_demo
uv run --locked python -m app.agent_demo show RUN_ID
curl -X POST -H "Authorization: Bearer $TOKEN" http://127.0.0.1:8003/workflows/WF-1/agent-runs
```

Starting a run needs `assist` in the workflow's unit; reading a stored run
needs `view`. The HTTP API always uses `demo-rules/1`.

`ModelProvider.complete(ModelRequest) -> ModelTurn`: a request has fixed
instructions, tool specs with strict JSON Schemas, the history and the answer
schema; a turn is `tool_calls`, `final` or `refusal`. Results of one turn go
back together, linked by call ID and flagged when refused.

| Provider | Use |
| --- | --- |
| `DemoRulesProvider` (`demo-rules/1`) | Rule-based stand-in: reads workflow and reconciliation, fields for discrepancies, then drafts |
| `ScriptedProvider` | Replays fixed turns or functions of the request; simulates misbehaving models |

| Tool | Returns |
| --- | --- |
| `get_workflow` | State, version, unit, pinned versions, basic-check codes, decider |
| `get_reconciliation` | Latest snapshot: status, freshness and stale reasons, discrepancies; lines with `include_lines` |
| `get_extracted_fields` | Field names, statuses and values (marked as document data, cut to 200 characters); optional `names` filter |
| `get_audit_trail` | Commands, states, versions and actors of the last 20 events |

Every schema requires `workflow_id`, forbids extra keys and bounds strings;
each call opens its own read-only transaction and re-checks `view`.

| Limit (`AgentLimits`) | Default | Outcome |
| --- | --- | --- |
| `max_provider_calls` | 6 | stop `provider_call_limit` |
| `max_tool_calls` | 10 | stop `tool_call_limit` |
| `max_refused_calls` | 3 | stop `too_many_refusals` |
| `provider_timeout_seconds` | 20 | stop `provider_timeout` |
| `tool_timeout_seconds` | 5 | stop `tool_timeout` |
| `max_result_bytes` | 8192 | call refused `result_too_large` |
| `max_runs_per_hour` | 20 per workflow | `429 agent_run_limit` |

Refused calls (`invalid_call`, `unknown_tool`, `invalid_arguments`,
`out_of_scope`, `not_found`, `missing_permission`, `unauthenticated`,
`result_too_large`, `tool_failed`) go back to the model as errors. Other stops
without a draft: `provider_error`, `provider_refusal`, `invalid_final_answer`.

The final answer must match `{explanation (1-2000 chars), next_step,
cited_codes?}` with `next_step` one of `ready_for_approval`,
`request_new_reconciliation`, `recommend_rejection`, `needs_human_review`.
`review_draft` replaces it with `needs_human_review` when the workflow is
closed (`workflow_closed`), a cited code is unknown (`unsupported_citation`)
or `ready_for_approval` lacks support (`workflow_not_awaiting_approval`,
`no_reconciliation`, `reconciliation_stale`, `reconciliation_not_matched`).
`agent_runs` and `agent_steps` (append-only) keep provider, stop reason,
counters, tool names, error codes, sizes, durations and SHA-256 of arguments,
results and the explanation; never prompts, document text or the explanation.

`ModelFieldExtractor(provider)` lets a model propose invoice fields through
the same assessment, so a value that does not match its cited text gives
`unverified_evidence`; invalid output, refusals, timeouts and exceptions give
an all-missing result that needs review.

## Outbox delivery

Decision events are delivered at least once to one fixed, local, fictional
receiver. Nothing is paid, sent or booked anywhere.

```bash
export SANDBOX_WEBHOOK_SECRET="$(openssl rand -hex 24)"
uv run --locked python -m sandbox.receiver                # terminal 1
uv run --locked python -m app.outbox_worker run           # terminal 2, needs DATABASE_URL
```

| Command | Effect |
| --- | --- |
| `python -m app.outbox_worker run [--once] [--interval 2]` | Deliver due events; print one JSON line per attempt |
| `python -m app.outbox_worker status [--limit 20]` | Counts per status and the exhausted events |
| `python -m app.outbox_worker requeue EVENT_ID` | New round of attempts for an exhausted event |
| `python -m sandbox.receiver [--mode accept\|fail\|lose_ack\|slow] [--failures N] [--port 8010] [--database PATH]` | Serve the receiver on 127.0.0.1 |
| `python -m app.outbox_demo` | Every failure mode in one process, with a shifted clock |

Both processes need the same `SANDBOX_WEBHOOK_SECRET` (32+ characters); never
commit it. Exit codes: 0 success, 1 database or command error, 2 missing
`DATABASE_URL`/secret or an invalid URL.

**Destination.** `SANDBOX_WEBHOOK_URL` (default
`http://127.0.0.1:8010/webhook`) must be `http(s)` on the IP `127.0.0.1` or
`[::1]`, without credentials, query or fragment; `localhost` is refused
because name resolution can be changed. Events, documents and API requests
carry no destination. The sender ignores proxy variables and refuses
redirects (`redirect_refused`).

| Status | Meaning |
| --- | --- |
| `pending` | No attempt in this round |
| `retry_scheduled` | Last attempt failed; next one at `next_attempt_at` |
| `delivered` | The receiver answered 2xx |
| `exhausted` | Five consecutive failures; an operator must requeue |

Each attempt runs in one transaction: claim the oldest due row in
`outbox_deliveries` with `FOR UPDATE SKIP LOCKED`, sign and send (5 s
timeout), append to `outbox_attempts` and update the row, commit. A crash
before commit leaves the event due, so it is sent again. Backoff is 2, 4, 8,
16 seconds (cap 60), without jitter. Attempts record the HTTP status or an
error code (`http_503`, `timeout`, `connection_error`, `redirect_refused`) and
the receiver's `duplicate` flag, never the body or secret.

**Signature.** Body: canonical JSON with `event_id`, `event_type`,
`workflow_id`, `workflow_version`, `created_at` and `payload`. Headers:
`X-EWA-Event-Id`, `X-EWA-Timestamp` (Unix seconds) and
`X-EWA-Signature: v1=<hex HMAC-SHA256 of "timestamp.body">`.

**Receiver.** `sandbox/receiver.py` imports nothing from `app` and checks the
body size (≤ 64 KiB, `413`), headers (`400`), timestamp within ±300 s
(`401 stale_timestamp`), signature (`401 bad_signature`) and body ID (`400
invalid_body`). It records one action per event ID in SQLite
(`var/sandbox/receiver.sqlite3`): `accepted` the first time, `duplicate`
afterwards, `409 event_id_reused` for the same ID with another body.
`GET /actions` lists what it did. Modes `fail`, `lose_ack` and `slow` simulate
failures. `GET /workflows/{id}/outbox` and the employee page show each
event's delivery state.

## Employee page

A Streamlit page that is a plain client of the HTTP API: it never reads the
database, imports no `app` code and checks no business rules.

```bash
uv sync --locked --group ui
uv run --locked python -m app.ui_demo_setup
uv run --locked uvicorn app.main:app --host 127.0.0.1 --port 8003
uv run --locked --group ui python -m streamlit run ui/app.py
```

Open `http://127.0.0.1:8501`, keep the API URL and paste a token printed by
`app.ui_demo_setup` (employees `ui-clerk`, `ui-approver`, `ui-viewer`,
`ui-outsider`). The setup also records order `DEMO-PO-UI-001`, policy
`demo-ui-policy` and rules `demo-ui-rules` (the page's defaults) and writes
`clean.txt`, `price_difference.txt` and `instruction_in_text.txt` to
`var/ui-samples/`; running it again reuses the employees and issues new
tokens. Optional variables: `EWA_API_URL`, `EWA_UI_POLICY_ID`,
`EWA_UI_RULES_ID`.

- **Workflows**: list by state; the selected workflow shows state, version,
  unit, starter, decision, document fields with source lines, the latest
  reconciliation with stale reasons and quoted discrepancies, actions (check,
  reconcile, pin the latest order, approve, reject), the assistant's draft and
  trace, the audit trail and outbox events with delivery status. A retry is a
  warning; an exhausted delivery is an error naming the operator's
  `requeue` command.
- **New invoice**: upload a `.txt`/`.pdf` or paste text; see extraction
  status, problems and fields; start the workflow and run the basic check.

Commands send an `Idempotency-Key` built from the intent (action, workflow,
version and relevant inputs) in `ui/keys.py`, so a second submit, another tab
or a reload gets the server's stored result; a click from an outdated page
carries the new version and is refused (`invalid_transition`). The token lives
only in the page session. Errors show the server's status, code and message
plus a hint from `ui/presenters.py`. `ui/client.py` uses only the standard
library, accepts only `http`/`https` URLs and never puts the token into
messages.

## Evaluations

31 named cases with explicit expected outcomes run the real services on
PostgreSQL and, for delivery, a real sandbox receiver on a free loopback port.
Providers are deterministic; no network or real LLM is used.

```bash
uv run --locked python -m app.evals list                   # no database needed
uv run --locked python -m app.evals run                    # needs DATABASE_URL
uv run --locked python -m app.evals run --case timeout-webhook --reports var/evals
```

`run` writes `eval-<UTC time>-<run>.json` and `.md` to `var/evals/` and prints
one line per case. Exit codes: 0 all passed, 1 a case failed or the database
failed, 2 missing `DATABASE_URL` or an unknown case ID. Before delivery cases
the suite delivers every pending event of the database to its own temporary
receiver (reported as `earlier_events_sent_to_eval_receiver`); use a
development database, not one whose events matter.

| Category | Cases | Examples |
| --- | --- | --- |
| `injection` | 3 | Instruction text is data; a model obeying a field is refused; a fabricated total fails the evidence check |
| `wrong_fields` | 3 | Wrong declared total; unit price 0.10 below the order; comma decimal |
| `ambiguous_data` | 3 | Two totals; missing currency; duplicate invoice in two workflows |
| `provider_failure` | 3 | Provider error in the agent and in extraction; provider refusal |
| `timeout` | 3 | Slow model; PDF worker that never answers; receiver slower than the worker |
| `retry` | 4 | Replayed approval; lost receiver answer; exhaustion and requeue; wrong signing secret |
| `concurrency` | 2 | Two approvals at once; two delivery workers at once |
| `malicious_document` | 6 | Renamed PNG; control characters; oversized; encrypted, malformed and text-expanding PDFs |
| `access` | 4 | Another unit; viewer approval; agent quota; request body limit |

A case passes only if its observation equals the expected dictionary exactly;
an exception is a failed case, not a crash. Reports list per-category totals,
every case and a fixed list of limitations, and state that the results are not
evidence of production readiness.

## Command-line tools

All tools run as `uv run --locked python -m <module>` from the repository
root. Exit code `2` always means missing configuration.

| Module | Commands | Needs a database |
| --- | --- | --- |
| `app.demo` | Domain demonstration | no |
| `app.persistence_demo` | Demo, `show WORKFLOW_ID` | yes |
| `app.documents_cli` | `write-samples`, `ingest`, `show`, `evidence`, `demo` | for storing |
| `app.reconciliation_cli` | `demo`, `show SNAPSHOT_ID` | yes |
| `app.users_cli` | `create`, `token`, `revoke`, `deactivate`, `activate`, `list` | yes |
| `app.decisions_demo` | Demo, `show WORKFLOW_ID` | yes |
| `app.agent_demo` | Demo, `show RUN_ID` | yes |
| `app.ui_demo_setup` | Employees, tokens, reference data, samples | yes |
| `app.outbox_worker` | `run`, `status`, `requeue` | yes |
| `app.outbox_demo` | Delivery failure modes | yes |
| `app.evals` | `list`, `run` | for `run` |
| `app.smoke` | HTTP check of a running deployment | yes |
| `app.backup` | `export`, `restore`, `verify`, `check` | yes; see [backup](backup.md) |
| `sandbox.receiver` | Sandbox webhook receiver | no |

What the system does not do is listed in
[LIMITATIONS.md](../LIMITATIONS.md) (Russian).
