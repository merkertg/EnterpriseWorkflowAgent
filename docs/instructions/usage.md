# API usage

[README](../../README.md) · [Quickstart](quickstart.md) ·
[Decisions and roles](decisions.md)

Start the server on `127.0.0.1:8003`:

```bash
uv run --locked uvicorn app.main:app --host 127.0.0.1 --port 8003
```

`/health`, `/docs`, `/redoc` and `/openapi.json` need no credentials. Every
other endpoint (Stage 06) needs `DATABASE_URL`, an upgraded schema and a
bearer token from `python -m app.users_cli token USER_ID`; without
`DATABASE_URL` they answer `503 database_not_configured`.

## Health

```bash
curl -i http://127.0.0.1:8003/health
```

```json
{"status": "ok", "service": "enterprise-workflow-agent"}
```

`service` is the instance's `APP_NAME`. HTTP `200` confirms only that the
application handled the request; it does not check the database.
`POST /health` returns `405`; an unknown route returns `404`.

## Authentication and commands

```bash
TOKEN=ewa_...   # printed once by users_cli token
curl -H "Authorization: Bearer $TOKEN" http://127.0.0.1:8003/me
```

A missing, malformed, unknown or revoked token, or a deactivated user, gets
the same `401 unauthenticated` with `WWW-Authenticate: Bearer`.

Commands that change a workflow (`POST /workflows`, `check`, `revise`,
`approve`, `reject`) need an `Idempotency-Key` header of 1–60 characters
from letters, digits and `. _ : -`. The server prefixes it with the caller's
user ID. Repeating the same request with the same key returns the stored
result with `"replayed": true`; the same key with other arguments is
`409 idempotency_key_reused`.

## Endpoints

| Method and path | Permission | Purpose |
| --- | --- | --- |
| `GET /me` | any user | User ID, roles, units, permissions |
| `POST /documents?name=NAME` | `submit` | Upload raw bytes (`application/octet-stream`); returns document, extraction status, fields, problems and invoice version |
| `GET /reference/{kind}/{business_id}` | `view` | Latest version of `purchase-orders`, `policies` or `reconciliation-rules` |
| `POST /workflows` | `submit` | Start: `workflow_id`, `business_unit`, `invoice_version_id`, `purchase_order_version_id` (or `null`), `policy_version_id` |
| `GET /workflows/{id}` | `view` | State, version, unit, creator, pinned versions, issue codes, decision |
| `POST /workflows/{id}/check` | `submit` | `{"expected_version": N}` |
| `POST /workflows/{id}/revise` | `submit` | Expected version and new version IDs |
| `POST /workflows/{id}/reconciliations` | `reconcile` | `{"rules_version_id": ...}`; returns the report |
| `GET /workflows/{id}/reconciliations/latest` | `view` | Newest report with `current` and `stale_reasons` |
| `GET /reconciliations/{snapshot_id}` | `view` | One report |
| `POST /workflows/{id}/approve` | `decide` | `expected_version`, `reconciliation_snapshot_id`, `reason` |
| `POST /workflows/{id}/reject` | `decide` | `expected_version`, `reason` |
| `GET /workflows/{id}/audit` | `view` | Audit events with actor, snapshot and outbox references |
| `GET /workflows/{id}/outbox` | `view` | Stored decision events |
| `POST /workflows/{id}/agent-runs` | `assist` | Run the bounded review agent (provider `demo-rules/1`); returns the explanation once, the checked draft and the trace |
| `GET /agent-runs/{run_id}` | `view` | A stored run and trace; the explanation is not stored, only its hash and length |

Request bodies reject unknown fields and use strict types (`"2"` is not a
version). Uploads are read only up to 1 MiB; a larger body is `413 too_large`.

## Example: approve a reconciled workflow

```bash
curl -X POST http://127.0.0.1:8003/workflows/WF-1/approve \
  -H "Authorization: Bearer $TOKEN" \
  -H "Idempotency-Key: approve-wf-1" \
  -H "Content-Type: application/json" \
  -d '{"expected_version": 2, "reconciliation_snapshot_id": "SNAPSHOT_ID", "reason": "Fictional invoice matches the order"}'
```

## Errors

Every error body is `{"error": {"code": "...", "message": "..."}}`, with
`field`, `reasons` or `problems` where relevant. Validation problems list
locations and messages, never the submitted values.

| Status | Codes |
| --- | --- |
| 401 | `unauthenticated` |
| 403 | `missing_permission`, `self_approval_forbidden` |
| 404 | `not_found`, also for another business unit's workflow |
| 409 | `version_conflict`, `invalid_transition`, `stale_validation`, `stale_reconciliation`, `reconciliation_not_matched`, `idempotency_key_reused`, `workflow_exists`, `concurrent_write` |
| 413 | `too_large` |
| 422 | `invalid_request`, document rejection codes, `reconciliation_not_for_workflow`, other domain validation codes |
| 503 | `database_not_configured`, `database_unavailable` |

## API documentation

[Swagger UI](http://127.0.0.1:8003/docs) lets you authorize with the token
and try each operation; [ReDoc](http://127.0.0.1:8003/redoc) and
[OpenAPI JSON](http://127.0.0.1:8003/openapi.json) describe the contract,
including the bearer scheme and the `Idempotency-Key` header.

Command-line tools remain available: [domain](domain.md),
[persistence](persistence.md), [documents](documents.md),
[reconciliation](reconciliation.md), [decisions](decisions.md) and
[agent](agent.md).
