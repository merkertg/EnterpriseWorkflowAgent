# Outbox delivery and the sandbox receiver

[README](../../README.md) · [Stage 09 guide (RU)](../STAGE_09.md) ·
[ADR 0009](../adr/0009-outbox-delivery-and-evaluations.md) ·
[Decisions](decisions.md) · [Evaluations](evaluations.md)

Decision events (`invoice.approved`, `invoice.rejected`) are delivered at
least once to one fixed, local, fictional receiver. Nothing is paid, sent or
booked anywhere.

## Run

```bash
uv run --locked alembic upgrade head
export SANDBOX_WEBHOOK_SECRET="$(python -c 'import secrets; print(secrets.token_urlsafe(32))')"
uv run --locked python -m sandbox.receiver                # terminal 1
uv run --locked python -m app.outbox_worker run           # terminal 2, needs DATABASE_URL
```

Both processes need the same `SANDBOX_WEBHOOK_SECRET` (32+ characters). Never
commit it. The worker also reads `SANDBOX_WEBHOOK_URL` (default
`http://127.0.0.1:8010/webhook`).

| Command | Effect |
| --- | --- |
| `python -m app.outbox_worker run [--once] [--interval 2]` | Deliver due events; print one JSON line per attempt |
| `python -m app.outbox_worker status [--limit 20]` | Counts per status and the exhausted events |
| `python -m app.outbox_worker requeue EVENT_ID` | New round of attempts for an exhausted event |
| `python -m sandbox.receiver [--mode accept\|fail\|lose_ack\|slow] [--failures N] [--port 8010] [--database PATH]` | Serve the receiver on 127.0.0.1 |
| `python -m app.outbox_demo` | Every failure mode in one process, with a shifted clock |

Exit codes: 0 success, 1 database or command error, 2 missing
`DATABASE_URL`/secret or an invalid URL.

## Destination

`SANDBOX_WEBHOOK_URL` must be `http(s)` on the IP `127.0.0.1` or `[::1]`,
without credentials, query or fragment; `localhost` is refused because name
resolution can be changed. Settings validation rejects anything else at
startup. Events, documents and API requests carry no destination. The sender
ignores proxy variables and refuses redirects (`redirect_refused`).

## Delivery state

`outbox_deliveries` holds one mutable row per event; `outbox_attempts` is an
append-only log. The decision transaction inserts the event and a `pending`
delivery together; migration `0006` back-fills older events.

| Status | Meaning |
| --- | --- |
| `pending` | No attempt in this round |
| `retry_scheduled` | Last attempt failed; next one at `next_attempt_at` |
| `delivered` | The receiver answered 2xx |
| `exhausted` | Five consecutive failures; an operator must requeue |

Each attempt runs in one transaction: claim the oldest due row with
`FOR UPDATE SKIP LOCKED`, sign and send (5 s timeout), insert the attempt and
update the row, commit. A crash before commit leaves the event due, so it is
sent again. Backoff is 2, 4, 8, 16 seconds (cap 60), without jitter.
Attempts record the HTTP status or an error code (`http_503`, `timeout`,
`connection_error`, `redirect_refused`) and the receiver's `duplicate` flag,
never the body or secret.

## Signature

Body: canonical JSON (sorted keys, no spaces) with `event_id`, `event_type`,
`workflow_id`, `workflow_version`, `created_at` and `payload`. Headers:
`X-EWA-Event-Id`, `X-EWA-Timestamp` (Unix seconds) and
`X-EWA-Signature: v1=<hex HMAC-SHA256 of "timestamp.body">`.

## Receiver

`sandbox/receiver.py` imports nothing from `app` and verifies on its own:
body ≤ 64 KiB (`413`), parseable headers (`400`), timestamp within ±300 s
(`401 stale_timestamp`), signature (`401 bad_signature`), body ID equals
header ID (`400 invalid_body`). It then records one action per event ID in
SQLite (`var/sandbox/receiver.sqlite3`): `accepted` the first time,
`duplicate` afterwards, `409 event_id_reused` for the same ID with another
body. `GET /actions` lists what it did. Modes `fail` (503 before acting),
`lose_ack` (act, then 503) and `slow` (answer late) simulate failures.

## Visibility

`GET /workflows/{id}/outbox` returns `delivery` (status, attempts, next
attempt, last error, delivered time) and `attempts` for each event; the
Streamlit page shows a warning for retries and an error with the requeue
command for exhausted events.

## Limits

At-least-once only; the receiver deduplicates. The row lock is held while
sending (up to the timeout). `requeue` is an operator command without a
workflow audit entry. Local SQLite receiver, loopback only.
