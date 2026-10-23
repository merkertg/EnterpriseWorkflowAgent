# Invoice-to-PO reconciliation

[README](../../README.md) · [Detailed Russian guide](../STAGE_05.md) ·
[ADR 0005](../adr/0005-deterministic-reconciliation.md) ·
[Verification](../VERIFICATION.md)

Stage 05 compares a workflow's pinned invoice and purchase order line by line
under a versioned rule set, stores an immutable snapshot of every input, and
reports when that snapshot becomes stale. All arithmetic is deterministic
`Decimal` code; no model computes totals. No HTTP endpoint is added.

## Commands

```bash
uv run --locked alembic upgrade head
uv run --locked python -m app.reconciliation_cli demo
uv run --locked python -m app.reconciliation_cli show SNAPSHOT_ID
```

`demo` uses run-specific invoice, order and rule IDs, so repeated runs do not
see each other as duplicates. `show` prints status, totals, discrepancies with
quoted sources, freshness and whether the snapshot reproduces. Exit codes:
`0` success, `1` command error, `2` missing or invalid `DATABASE_URL`.

## Rules

```python
from decimal import Decimal

from app.domain.money import Currency, Money
from app.domain.reconciliation import CurrencyRule, ReconciliationRules

usd = CurrencyRule(
    Currency.USD,
    unit_price_tolerance=Money(Decimal("0.05"), Currency.USD),
    total_tolerance=Money(Decimal("0.10"), Currency.USD),
)
rules = ReconciliationRules("my-rules", (usd,), allow_partial_quantities=True)
```

Tolerances are absolute and inclusive. `decimal_places` must be `2` and
`rounding` `ROUND_HALF_UP`, the only behaviour the money module implements.
`require_source_document` (default `True`) demands that the invoice came from a
complete document extraction. `ReconciliationService.record_rules()` versions
rule sets like orders and policies; currency order does not affect the hash.

## Discrepancy codes

`missing_source_document`, `duplicate_invoice`, `no_rule_for_currency`,
`missing_purchase_order`, `purchase_order_reference_mismatch`,
`supplier_mismatch`, `currency_mismatch`, `unknown_line`,
`quantity_exceeds_order`, `quantity_below_order`, `unit_price_mismatch`,
`declared_total_mismatch`, `expected_total_mismatch`.

Each discrepancy has `field` (Stage 04 schema name, e.g.
`lines[2].unit_price`), `expected`, `actual`, the applied `rule` (e.g.
`USD.unit_price_tolerance=0.05`) and `sources` (evidence spans). The expected
line total is order price × invoiced quantity, rounded once per line.
`expected_total_mismatch` compares only lines that matched an order SKU, so
per-unit differences inside tolerance that add up are caught without counting
unknown lines twice. No discrepancy means `matched`; it is not an approval.

## Snapshots, freshness and reproduction

```python
from app.infrastructure.db.session import build_reconciliation_service

service = build_reconciliation_service(engine)
rules_v = service.record_rules(rules)
snapshot = service.reconcile("WF-1", rules_version_id=rules_v.version_id)
service.freshness(snapshot.snapshot_id)  # reasons, empty when current
service.reproduce(snapshot.snapshot_id)  # equals snapshot.result
service.report(snapshot.snapshot_id)  # freshness, reproducible, citations
```

A snapshot records invoice, order and rule versions with hashes, the source
document and its hash, the extraction and a hash of its fields and evidence,
duplicate workflow IDs and the engine version `reconciliation.v1`. An inputs
hash makes reconciliation idempotent: equal inputs return the stored snapshot.

| Stale reason | Meaning |
| --- | --- |
| `workflow_inputs_changed` | The workflow was revised to other versions |
| `purchase_order_changed` | A newer order version exists |
| `purchase_order_available` | An order now exists where none did |
| `rules_changed` | A newer rule version exists |
| `duplicates_changed` | Other workflows for the same invoice changed |

`reproduce` reloads the snapshot's own versions, verifies their hashes and
recomputes; edited rules fail with `stored_data_integrity`.

Duplicates are other non-rejected workflows with the same supplier and invoice
number. Reconciliation does not change workflow state or write audit events.

## Limits

`approve` does not yet require a fresh matched snapshot (Stage 06). No
percentage tolerances, taxes, discounts or tracking of quantities billed by
earlier invoices; duplicates only by supplier and invoice number.
