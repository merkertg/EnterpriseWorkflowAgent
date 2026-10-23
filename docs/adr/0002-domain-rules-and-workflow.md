# ADR 0002: Pure domain rules and immutable in-memory workflow

- Status: Accepted
- Date: 2026-10-23
- Scope: Stage 02 baseline domain model and state transitions

## Context

The initial HTTP service establishes liveness and settings. The next stage must
make invoice rules and workflow transitions explicit before persistence,
document extraction or model orchestration introduces external dependencies.
The project needs reproducible fictional examples and rejection of invalid
inputs, without implying that a local decision has authenticated an employee or
performed a real business action.

## Decision

1. Implement the domain in `app/domain/` using only the Python standard library.
   Money, models, validation rules and workflow transitions do not import
   FastAPI, Pydantic, SQLAlchemy, settings or model SDKs. Future adapters depend
   on the domain rather than making domain rules depend on their schemas.
2. Use frozen dataclasses and tuples for immutable value objects and snapshots.
   Every command returns a new workflow value instead of mutating an object.
   Revisions invalidate the previous check and return the nonterminal workflow
   to `received`.
3. Use explicit `Decimal` input for prices, totals and quantities. Reject
   implicit float/string conversion, nonfinite values and money input that is
   not exactly representable in cents. Money is nonnegative and bounded by
   `999999999999.99`; quantities are positive,
   bounded by `999999.999999` and have at most six decimal places.
4. Support explicit `USD`, `EUR` and `UZS` currency enum values for fictional
   examples. For this stage, all supported currencies use two decimal places
   and `ROUND_HALF_UP` when calculating each line total. Sum rounded line
   totals for the document; do not silently round input prices or declared
   totals. Use a private decimal context so caller precision, rounding and traps
   do not alter a result. Reject mixed-currency operations and perform no
   conversion. This policy is a deliberately limited learning rule rather than
   a comprehensive currency registry or accounting policy.
5. Require 1–100 line items with unique SKU values and matching document
   currency. Keep the declared invoice total separate from its calculated
   line total so the discrepancy remains detectable.
6. Distinguish invalid construction input from business discrepancies. Invalid
   values raise explicit domain errors. Well-formed documents are evaluated by
   deterministic rules returning structured issue codes and input snapshots.
   Baseline checks cover the PO reference, supplier, currency and totals. Full
   item reconciliation, tolerances and complete reconciliation snapshots remain
   Stage 05 work; durable source and policy versions start in Stage 03.
7. Use `received`, `needs_review`, `awaiting_approval`, `approved` and `rejected`.
   A clean check permits awaiting approval; it never approves automatically.
   Approval requires an explicit human decision and a new check of the current
   inputs. Both approved and rejected workflows are terminal.
8. Treat `HumanDecision.actor_id` and `reason` as explicit caller-provided
   values, not authenticated identity or authorization evidence. Expose no
   invoice approval HTTP endpoint before Stage 06 server-side access checks.
9. Provide a deterministic offline demonstration through
   `uv run --locked python -m app.demo`, using fictional data and printing JSON.
   It performs no persistence, model calls, payments or external side effects.

## Alternatives considered

- **Pydantic domain models:** retain Pydantic at transport/configuration
  boundaries; ordinary dataclasses keep domain behavior independent of a
  framework and avoid implicit input coercion.
- **Float money:** rejected because binary approximation obscures explicit
  decimal precision, rounding and exact comparisons.
- **Mutable workflow objects:** rejected because previously checked values
  could silently change, making a result harder to interpret and test.
- **Approve on a clean check:** rejected because deterministic validation and
  the employee decision are distinct steps.
- **Implement a full reconciliation engine now:** deferred to Stage 05, where
  immutable persisted inputs, evidence and versioned policy can support the
  wider acceptance criteria.

## Consequences

Rules and transitions can be tested without network access, credentials or a
database, and their dependency boundary remains clear. Strict constructors make
adapters responsible for deliberate external-input conversion. A clean baseline
result confirms only the checks implemented at Stage 02; matching totals do not
prove matching items.

Frozen snapshots are not durable audit records, database transactions,
concurrency control or command idempotency. Stage 03 must introduce those
guarantees explicitly. Rechecking in memory before approval is useful but does
not provide an atomic check against simultaneous external changes. Stage 05
adds durable reconciliation references, and Stage 06 must check applicable
versions and roles in the same transaction as the employee decision.

The offline demo is local evidence only. Real-model behavior, remote CI and
production suitability need separate verification.
