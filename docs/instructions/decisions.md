# Employee decisions, roles and the outbox

[README](../../README.md) · [HTTP API](usage.md) ·
[Stage 06 guide (RU)](../STAGE_06.md) ·
[ADR 0006](../adr/0006-employee-decisions-and-outbox.md)

Stage 06 lets an authenticated employee with the right role in the right
business unit approve or reject a workflow. Approval must cite a fresh
reconciliation snapshot without discrepancies. The decision, an outbox event
and the audit event commit together. All accounts and data are fictional.

## Roles and business units

| Role | view | submit | reconcile | decide |
| --- | --- | --- | --- | --- |
| `viewer` | yes | — | — | — |
| `clerk` | yes | yes | yes | — |
| `approver` | yes | — | yes | yes |

`submit` covers document upload, start, check and revise. A user may hold
several roles. Every workflow belongs to one business unit, chosen at start
from the caller's own units. A workflow in another unit is reported as
`not_found`; a missing permission in the caller's unit is
`missing_permission`. Reference data (orders, policies, rules) is shared and
needs only `view`. Workflows created before migration `0004` are in unit
`unassigned`.

## Managing accounts

Requires `DATABASE_URL` and `alembic upgrade head`. Accounts are created only
from the command line:

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
`DATABASE_URL` exits with 2.

## What approval checks

In one transaction, in this order:

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
10. workflow version, `invoice.approved` outbox event and audit event.

Rejection checks the role, unit and state and writes `invoice.rejected`; it
needs no snapshot. Recording a new order or rule version and starting a
workflow for the same invoice number take the same advisory locks, so they
wait for an approval in progress instead of invalidating it unseen.

## Outbox

`outbox_events` is append-only. Each event has an ID, type, workflow ID and
version, and a JSON payload with IDs, hashes, the business unit and the
deciding user; it never contains document text. The decision's audit event
references the outbox event and, for approvals, the reconciliation snapshot.
Nothing delivers events yet; delivery with retries is Stage 09.

## Demonstration

```bash
uv run --locked python -m app.decisions_demo
uv run --locked python -m app.decisions_demo show WORKFLOW_ID
```

The demo creates four fictional employees with tokens, signs in with those
tokens and shows: missing role, other unit, self-approval, stale snapshot
after a rule change, stale workflow version, approval, idempotent replay,
invalid transition after approval, refusal of a snapshot with discrepancies,
rejection, and refused sign-in after revocation and deactivation. Tokens are
not printed.

## Using the services from Python

`WorkflowService.approve(workflow_id, expected_version=..., reconciliation_snapshot_id=..., reason=..., actor=principal, context=...)`
and `reject(..., reason=..., actor=principal, context=...)` require a
`Principal`; obtain one with `UserService.authenticate(token)` or
`UserService.create_user(...).principal()`. `start`, `check`, `revise`,
`reconcile`, `ingest` and the queries accept an optional `actor`; without it
they run as an internal tool with no access check.

## Limits

No token expiry, rotation, SSO or rate limiting; no command to change a
user's roles; documents are not tied to a unit; the starter may reject; no
outbox delivery. See [verification](../VERIFICATION.md).
