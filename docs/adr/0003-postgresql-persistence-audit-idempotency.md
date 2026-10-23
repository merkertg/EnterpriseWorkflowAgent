# ADR 0003: PostgreSQL persistence, audit, optimistic locking and idempotency

- Status: Accepted
- Date: 2026-10-23
- Scope: Stage 03 storage, transactions, audit trail and input versions

## Context

Stage 02 established immutable domain objects and transitions in memory.
Stage 03 must persist them without moving business decisions into the
database layer. The roadmap requires exact money storage, atomic state changes
with audit, protection against lost concurrent updates and repeated commands,
an isolated test database, and immutable versions of the invoice, purchase
order and policy that later reconciliation and approval can reference by ID
and content hash. Document upload does not exist yet, so the stored "source"
is the structured invoice content; raw files arrive in Stage 04.

## Decision

1. Use PostgreSQL 18 with SQLAlchemy 2.1 (ORM mappings, explicit queries),
   psycopg 3 and Alembic. Accept only `postgresql+psycopg://` URLs. Store money
   as `NUMERIC(14, 2)` and quantities as `NUMERIC(12, 6)`, matching the domain
   bounds. Repeat key domain bounds as named `CHECK` constraints.
2. Add `app/application` with a `WorkflowService` and protocol ports
   (`WorkflowRepository`, `UnitOfWork`). The application imports no SQLAlchemy.
   `app/infrastructure/db` implements the ports. Domain functions still decide
   every transition; a test enforces these import boundaries.
3. Store inputs as append-only versions in typed tables (headers and ordered
   lines). A version's SHA-256 hash is computed from a canonical JSON form of
   the domain object (`app/domain/content_hash.py`), not from storage layout.
   Identical content reuses the latest version; any change appends the next
   version number. Loads rebuild domain objects and re-verify the hash.
4. A workflow row pins explicit invoice, PO and policy version IDs. `start` and
   `revise` choose versions; `check` evaluates only pinned versions and stores
   an immutable validation snapshot with each version ID and hash.
5. Each command is one transaction, writing in this order: idempotency record,
   snapshot (for `check`), workflow insert or update, audit event. Failure at
   any step rolls back all of them.
6. Use optimistic locking: commands take `expected_version`; the service
   compares it after reading and the repository updates with
   `WHERE version = expected_version`, treating zero rows as a conflict.
7. Require a caller-chosen idempotency key on every command. Store the result
   with a SHA-256 fingerprint of the command and its arguments (excluding the
   `source` label). Same key and fingerprint replays the original result; a
   different fingerprint is rejected. The key is inserted before other writes
   so a concurrent duplicate waits on it and then replays. After a version
   conflict or duplicate-start error, the service looks the key up again,
   because a duplicate may commit between the key lookup and the workflow read.
   Failed commands store no key.
8. Audit events contain IDs, command, previous and new state, resulting
   version, pinned version IDs, snapshot ID, issue codes, caller-declared
   actor and source, idempotency key and database transaction time. They omit
   document text and decision reasons.
9. Make history tables append-only with a PostgreSQL trigger rejecting
   `UPDATE` and `DELETE`. Only `workflows` is mutable.
10. Integration tests read `TEST_DATABASE_URL` from the process environment
    only, refuse database names not ending in `_test`, rebuild the schema with
    the real migrations each session, and truncate tables before each test.
    Without the URL they are skipped; CI sets `REQUIRE_TEST_DATABASE=1` so a
    missing database fails the run.

## Alternatives considered

- **JSONB documents only:** simpler, but money would not be stored as
  `NUMERIC` and constraints could not protect individual fields.
- **SQLAlchemy `version_id_col`:** works, but hides the compare-and-set that
  learners should see; an explicit conditional `UPDATE` is clearer.
- **Pessimistic `SELECT ... FOR UPDATE`:** blocks rows while a client decides;
  optimistic locking reports conflicts without long locks.
- **Content-addressed deduplication across all versions:** A → B → A would
  return version 1 and lose the edit history; appending keeps "latest" meaningful.
- **Pending-then-complete idempotency rows:** would require updating an
  append-only table; inserting the final result before other writes avoids it.
- **In-memory fake repository for service tests:** would test the fake rather
  than PostgreSQL behavior for races, constraints and triggers.
- **SQLite for tests:** lacks the PostgreSQL types, triggers and locking under test.

## Consequences

Stored history is reproducible: a snapshot keeps loading its original PO and
policy after new versions are recorded. Retries and concurrent duplicates do
not repeat effects, and stale writers fail instead of overwriting.

Full integration coverage needs a running PostgreSQL; default local runs skip
those tests unless `TEST_DATABASE_URL` is set. Pinned versions are not yet
checked against newer versions, so a stale result is not flagged and approval
is not blocked; Stages 05 and 06 add that. `actor_id` and `source` are not
authenticated. Policy hashes cover policy data, not rule code. Triggers do not
stop a database owner who disables them. No backup, retention or HTTP command
API exists at this stage.
