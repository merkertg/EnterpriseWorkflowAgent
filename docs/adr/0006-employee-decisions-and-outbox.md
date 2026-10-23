# ADR 0006: Token identity, unit-scoped roles, fresh-snapshot approval and outbox

- Status: Accepted
- Date: 2026-10-23
- Scope: Stage 06 employee decisions and the business HTTP API

## Context

Until Stage 05, `actor_id` was a caller-supplied string and approval ignored
reconciliation. Stage 06 must identify users, check roles for viewing,
submitting and deciding, reject another unit's access, stale versions and
forbidden transitions, make approval cite a reconciliation snapshot whose
inputs are still applicable inside the approval transaction, write an outbox
record atomically with the decision, show it in the audit trail, and expose
document upload over HTTP only together with these checks.

## Decision

1. **Identity:** opaque bearer tokens (`ewa_` + 32 random bytes, URL-safe),
   stored only as SHA-256 in `api_tokens`, with revocation. Users live in
   `users` with roles, business units and an active flag. Accounts and
   tokens are created by a command-line tool, not over HTTP. All
   authentication failures return the same `unauthenticated` error.
2. **Authorization in the application layer:** three roles map to four
   permissions (`view`, `submit`, `reconcile`, `decide`). Services receive a
   `Principal` and, inside the command's transaction, re-read the user with
   `FOR SHARE`, check that it is active, check the workflow's business unit
   first (another unit is `not_found`) and then the permission
   (`missing_permission`). Starting in a unit the caller does not belong to is
   `missing_permission`, because the caller named the unit. Calls without an
   actor are internal tools; decisions always require one.
3. **Approval:** requires `reconciliation_snapshot_id` and a reason. After the
   version, rights and four-eyes checks (the starter may not approve), the
   service takes transaction-scoped PostgreSQL advisory locks on the order,
   rules and invoice-number keys in sorted order, recomputes freshness with
   the Stage 05 function, requires `matched`, and lets the domain perform the
   transition. Writers of new order or rule versions and workflow starts take
   the same keys, so freshness cannot change between the check and commit.
4. **Outbox:** `invoice.approved` and `invoice.rejected` events with IDs and
   hashes only, in append-only `outbox_events`, written in the same
   transaction as the workflow version and audit event. Audit events
   reference the outbox event and the reconciliation snapshot; the workflow
   stores the decision's snapshot.
5. **HTTP API:** FastAPI routes translate requests to services; strict Pydantic
   bodies; a required `Idempotency-Key` header, prefixed with the user ID;
   one error shape with stable codes; streaming upload limited to the
   document size limit; 503 without `DATABASE_URL`.
6. **Migration 0004:** adds the three tables and the new columns. Existing
   workflows receive the unit `unassigned` through a temporary server
   default that is dropped in the same revision.

## Consequences

Revocation and deactivation take effect on the next request, and a
concurrent deactivation waits for a running command. Approval latency includes
up to three lock waits, bounded by `lock_timeout`. The outbox is ready for a
Stage 09 delivery worker but delivers nothing now. Reference data and
uploaded documents are shared across units. Role changes need a new account;
there is no token expiry, rotation, SSO or rate limiting.

## Alternatives considered

- **Signed JWTs:** stateless verification, but revocation needs extra state;
  each request reads the database anyway.
- **Checks in HTTP dependencies only:** would let other callers bypass them
  and could not run inside the command transaction.
- **`SELECT ... FOR UPDATE` on the latest version rows:** cannot lock a version
  that does not exist yet; advisory locks cover "no new version may appear".
- **SERIALIZABLE isolation for approvals:** also correct, but adds retry
  handling for every command and is harder to explain step by step.
- **Sending the event directly from the command:** a crash after sending
  would announce a decision that rolled back.
