# ADR 0009: Outbox delivery to a sandbox receiver, hardening and evaluations

- Status: Accepted
- Date: 2026-10-24
- Scope: Stage 09 resilience, security limits and evaluation suite

## Context

Stage 06 writes decision events to an append-only outbox but delivers
nothing. The roadmap requires a worker with bounded retries and observable
delivery state, delivering only to a fixed local sandbox webhook with a
signature that documents and requests cannot redirect; a delivery failure
must not lose the event, a repeat must not cause a second action at the
receiver, and exhaustion must be visible to employees. It also requires an
evaluation set with explicit expected outcomes and stronger limits on
uploads, access and tools.

## Decision

1. **Separate delivery state.** `outbox_events` stays append-only. A new
   mutable `outbox_deliveries` row (status, attempts in the round, total
   attempts, requeues, next attempt, last error) is inserted in the decision's
   transaction; `outbox_attempts` is an append-only log of codes and timing.
   Migration `0006` back-fills deliveries for older events.
2. **One event per transaction, lock held while sending.** The worker claims
   the oldest due row with `FOR UPDATE SKIP LOCKED`, sends with a 5 s timeout,
   then records the attempt and commits. Concurrent workers take different
   rows; a crash rolls back and the event is sent again.
3. **At-least-once delivery, deduplication at the receiver.** The receiver
   keeps one action per event ID and answers repeats with `duplicate`; the
   same ID with another body is a conflict. Exactly-once network delivery is
   not claimed.
4. **Bounded, deterministic retries.** Five attempts with 2, 4, 8, 16 s backoff
   (cap 60 s, no jitter), then `exhausted`. An operator command `requeue`
   starts a new round; attempt numbers continue and the log is kept.
5. **Fixed loopback destination.** `SANDBOX_WEBHOOK_URL` must be http(s) on the
   IP `127.0.0.1` or `::1` without credentials or query; `localhost` is
   refused. The sender ignores proxy variables and refuses redirects.
6. **HMAC signature with a timestamp.** `v1=HMAC-SHA256(secret, "ts.body")`
   over canonical JSON; the receiver rejects timestamps outside ±300 s. The
   secret (32+ characters) comes from the environment only.
7. **An independent receiver.** `sandbox/` imports nothing from `app`
   (architecture test) and verifies the signature with its own code. It stores
   actions in SQLite and has failure modes for demos and tests.
8. **Hardening.** The PDF worker sets `RLIMIT_CPU` and tries `RLIMIT_AS`
   (512 MiB), counts text while reading and reports which limits apply; a
   worker killed by a signal is `resource_limit`. Non-upload request bodies
   must declare a length ≤ 64 KiB (411/413). The agent has an hourly quota of
   20 runs per workflow (`429 agent_run_limit`).
9. **Evaluation suite in code.** `app/evals` registers cases with expected
   observations; the runner compares exactly, records exceptions as failures
   and writes JSON and Markdown reports with fixed limitations and a statement
   that results are not production evidence. A PostgreSQL test runs the suite.

## Consequences

Employees and operators can see every attempt and why it failed. Delivery
survives crashes and receiver outages up to the retry budget, and duplicates
are harmless at the receiver. Holding a row lock during the send is simple
and correct at this scale, but a slow receiver occupies a connection for up
to the timeout. Memory limits depend on the platform: Linux enforces
`RLIMIT_AS`, macOS refuses it. The quota is read without a lock and may
admit one extra run under concurrency. The evaluation suite measures this
code with deterministic providers, not a real model.

## Alternatives considered

- **Update `outbox_events` in place**: would break its append-only guarantee
  and mix history with state.
- **Lease rows and send outside the transaction**: scales better but needs
  lease expiry and recovery logic; not needed for a local learning worker.
- **Random jitter in backoff**: avoids thundering herds in real fleets but
  makes demos and tests non-reproducible.
- **Let the receiver import the signing code from `app`**: less code, but it
  would no longer demonstrate an independent check of the contract.
- **YAML or JSON case files**: hostile and concurrent scenarios need code
  (threads, scripted models, failure modes), so cases are Python with data
  expectations.
