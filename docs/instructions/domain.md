# Domain model and local workflow

[README](../../README.md) · [Detailed Russian guide](../STAGE_02.md) ·
[Verification](../VERIFICATION.md)

Stage 02 adds immutable Python domain objects, deterministic baseline invoice
checks and an in-memory workflow. No invoice or approval HTTP endpoint is added.

## Run the fictional demo

From the repository root:

```bash
uv sync --locked
uv run --locked python -m app.demo
```

The command prints JSON for a clean invoice and a discrepancy requiring review.
It does not need a running server, `.env`, credentials or network access. Each
run starts with new fictional objects; no data is persisted or sent externally.

## Input contract

- Use `Decimal` explicitly for money and quantity. Construct decimal input from
  strings, for example `Decimal("12.50")`, rather than from a `float`.
- Use `Currency.USD`, `Currency.EUR` or `Currency.UZS`. The learning policy uses
  two decimal places for every supported currency. Input money must be exactly
  representable in cents; `Decimal("1.005")` is rejected. Multiplying a price
  by quantity rounds the line total once with `ROUND_HALF_UP`; document totals
  sum those rounded line totals. There is no exchange-rate conversion.
- Money must be finite, nonnegative and at most `999999999999.99`. Line and
  document totals must also fit this bound.
- Quantity must be finite, positive, at most `999999.999999` and use at most six
  decimal places.
- Documents contain 1–100 immutable line items with unique SKU values; line
  prices and declared totals must use the document currency.
- Model construction rejects malformed input. A well-formed invoice can still
  have discrepancies against its PO; `evaluate_invoice()` reports those as
  structured issues.

## Baseline checks and state

`evaluate_invoice(invoice, purchase_order, policy=None)` uses
`ValidationPolicy(policy_id="basic-demo-v1")` by default. Its allowlist contains
USD, EUR and UZS; a caller can supply a smaller currency tuple. Results retain
the exact immutable invoice, PO and policy values plus ordered issues.

The rules check the policy currency allowlist, a missing PO, a wrong PO
reference, supplier or currency differences, a declared invoice total
inconsistent with its lines, and a declared invoice total exceeding the PO
total in the same currency.

| Issue code | Meaning |
| --- | --- |
| `unsupported_currency` | Invoice currency is excluded by the supplied policy |
| `invoice_total_mismatch` | Declared total differs from calculated line totals |
| `missing_purchase_order` | No PO was supplied |
| `purchase_order_reference_mismatch` | Invoice references another PO |
| `supplier_mismatch` | Invoice and PO suppliers differ |
| `currency_mismatch` | Invoice and PO currencies differ; totals are not compared |
| `purchase_order_total_exceeded` | Declared invoice total exceeds the PO total |

Clean checks move the workflow to `awaiting_approval`; issues move it to
`needs_review`. A separate explicit human decision is required for `approved`
or `rejected`. Approval checks the current inputs again. Revision resets a
nonterminal workflow to `received`, clearing the previous check. Terminal
workflows reject further commands.

| Function | Allowed source states |
| --- | --- |
| `check_workflow(workflow)` | `received`, `needs_review`, `awaiting_approval` |
| `approve_workflow(workflow, decision)` | `awaiting_approval` with a fresh matching clean result |
| `reject_workflow(workflow, decision)` | `received`, `needs_review`, `awaiting_approval` |
| `revise_workflow(workflow, invoice, purchase_order, *, policy=None)` | `received`, `needs_review`, `awaiting_approval` |

Revision retains the invoice ID. Its PO argument is required: `None` deliberately
removes the PO. Omitting policy preserves the existing policy. The `Workflow`
constructor also rejects state, check and decision combinations that contradict
each other, including stale or fabricated findings.

In these in-memory functions `HumanDecision.actor_id` and `reason` are
caller-supplied data. Since Stage 06 the stored commands fill them from an
authenticated employee whose role and business unit were checked, and approval
also requires a fresh matched reconciliation; see [decisions](decisions.md).

The baseline result does not establish full line-by-line reconciliation;
Stage 05 adds item matching, tolerances and versioned reconciliation
snapshots. The domain functions here remain in-memory; Stage 03 stores their
results through `WorkflowService` with transactions, audit, optimistic locking
and idempotency. See [persistence](persistence.md).

## Read the implementation

| Module | Responsibility |
| --- | --- |
| `app/domain/errors.py` | Explicit invalid-input and transition errors |
| `app/domain/money.py` | Money bounds, currency and decimal arithmetic |
| `app/domain/models.py` | Line item, invoice and purchase-order invariants |
| `app/domain/rules.py` | Baseline validation policy, issues and result |
| `app/domain/workflow.py` | Workflow states, snapshots and commands |
| `app/demo.py` | Fictional end-to-end calls to domain functions |

The domain imports no FastAPI, Pydantic, SQLAlchemy or model SDK. All domain
operations are offline and independent of application settings.

For reproducible checks, use the commands in [testing](testing.md). Actual
results and evidence limits are recorded in [verification](../VERIFICATION.md).
