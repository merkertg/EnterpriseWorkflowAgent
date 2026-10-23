# ADR 0005: Deterministic line reconciliation with versioned rules and snapshots

- Status: Accepted
- Date: 2026-10-23
- Scope: Stage 05 invoice-to-purchase-order reconciliation

## Context

Basic checks compare totals and headers; equal totals do not prove equal
items. Stage 05 must compare supplier, order reference, lines, quantities,
prices, totals and currency under an explicit rule version, block automatic
progress when a currency rule or required input is missing, explain each
discrepancy with expected and actual values, its source and the rule applied,
detect repeated invoices, record an immutable snapshot of every input, mark
results stale when the applicable order or rules change, and keep earlier
snapshots reproducible. No model may compute totals.

## Decision

1. Implement reconciliation as a pure domain function in
   `app/domain/reconciliation.py`. Lines are matched by SKU. Rules per currency
   give absolute, inclusive unit-price and total tolerances. Rules also state
   rounding explicitly; only two decimals with `ROUND_HALF_UP` are accepted
   because that is what the money module implements. Partial quantities and
   the source-document requirement are rule options.
2. Report thirteen stable discrepancy codes in a fixed order, each with field,
   expected, actual, the applied rule and evidence spans from the Stage 04
   extraction. The expected line total is order price × invoiced quantity,
   rounded once per line. A separate total check over matched lines catches
   per-unit differences that add up, without counting unknown lines twice.
3. Version rule sets in `reconciliation_rule_versions` and
   `reconciliation_currency_rules`, with canonical, order-independent hashes,
   check constraints and append-only triggers.
4. Bind a reconciliation to a workflow's pinned invoice and order versions and
   an explicit rule version. Store an append-only snapshot with all version
   IDs and hashes, the source document hash, the extraction ID with a hash of
   its fields and evidence, duplicate workflow IDs and the engine version.
   An inputs hash, unique per workflow, makes repeated and concurrent
   reconciliations return one snapshot.
5. Compute freshness at read time against current applicable inputs: newer
   order or rule versions, a revised workflow, a newly available order or a
   changed duplicate set. Never mutate an old snapshot; a new reconciliation
   is required instead.
6. Reproduce a snapshot from its own versions after re-verifying their hashes,
   and compare with the stored result.
7. Treat other non-rejected workflows with the same supplier and invoice
   number as duplicates.
8. Do not change workflow state or write audit events during reconciliation.
   Stage 06 makes approval reference a fresh matched snapshot inside the
   approval transaction.

## Alternatives considered

- **Extend the Stage 02 check and workflow state:** would mix basic checks,
  versioned rules, document sources and duplicate context into the immutable
  `Workflow` invariants and break their reproducibility.
- **Store staleness as a flag:** a later order version would require updating
  history; computing freshness keeps snapshots append-only.
- **Percentage tolerances:** useful, but absolute tolerances suffice to teach
  boundaries and accumulation; they can be added as a new rule schema.
- **Compare calculated invoice total with expected total over all lines:**
  double-reports unknown lines; matched-line comparison isolates price drift.
- **Let a model judge discrepancies:** rejected; models may explain, never
  compute or decide.

## Consequences

Each discrepancy can be traced to a document quote and a named rule, and any
past result can be recomputed. Stale results are visible instead of silently
reused. Approval does not enforce reconciliation until Stage 06. Duplicate
detection is narrow, and taxes, discounts and cumulative billing across
invoices remain out of scope.
