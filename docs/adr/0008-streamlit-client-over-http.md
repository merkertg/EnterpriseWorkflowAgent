# ADR 0008: A Streamlit client that uses only the HTTP API

- Status: Accepted
- Date: 2026-10-23
- Scope: Stage 08 employee interface

## Context

The roadmap asks for a Streamlit page for upload, extracted fields and
sources, discrepancies, history and the employee's decision. The page must
use only the HTTP API, must not read the database or duplicate domain rules,
must show errors and edge states, and a page refresh or repeated click must
not duplicate a command. Roles and rights stay with the server.

## Decision

1. **Separate client package** `ui/` with an architecture test forbidding
   imports of `app`, SQLAlchemy, psycopg, Alembic, pypdf and FastAPI. Streamlit
   lives in its own dependency group `ui`, so the API does not depend on it.
2. **Standard-library HTTP client** with a replaceable transport. It accepts
   only `http`/`https` URLs, quotes path segments, maps error bodies to
   `ApiError` and never puts the token into messages. Tests use FastAPI's
   in-process client as the transport.
3. **Intent-derived idempotency keys**: the key is a hash of the action,
   workflow, version and relevant inputs. A repeated submit, another tab or a
   reload sends the same key and gets the stored result; a rerun after a
   successful command sees the new version and is refused by the server.
4. **Two read-only API additions** the page needs: a unit-scoped workflow
   list and the workflow's source document with cited fields.
5. **No rules in the page**: buttons are not hidden by state or role; the
   server's answer and a hint keyed by its error code are shown instead. The
   one local check is that approval needs a reconciliation snapshot ID.
6. **Tests**: client tests without a server, headless `AppTest` page tests
   with a fake client, and end-to-end `AppTest` runs against the real API and
   PostgreSQL; plus a manual check in a real browser.
7. **Setup command** `app.ui_demo_setup` creates fictional employees, tokens,
   reference data and samples; it is an administrator tool outside `ui/`.

## Consequences

The page can be replaced by any other HTTP client without touching the
server. CI installs the `ui` group so the page tests run; without it they are
skipped as whole modules, which pytest reports. The token must be pasted
again after a reload. Streamlit's rerun-per-click model makes stale-page
clicks safe but means each interaction re-fetches data.

## Alternatives considered

- **Import the services directly** (faster, no token): would bypass the API's
  authentication and could drift from server behaviour.
- **Random idempotency keys per click stored in session state**: would not
  survive a reload; intent-derived keys do.
- **Hide buttons by role and state**: duplicates server rules and hides the
  refusals the stage must show.
- **httpx/requests**: an extra dependency for a few JSON calls.
