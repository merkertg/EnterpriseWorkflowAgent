# Employee interface (Streamlit)

[README](../../README.md) · [Stage 08 guide (RU)](../STAGE_08.md) ·
[ADR 0008](../adr/0008-streamlit-client-over-http.md) · [API usage](usage.md)

A Streamlit page for the fictional invoice workflow. It is a plain client of
the HTTP API: it never reads the database, imports no `app` code and checks
no business rules. Every decision and refusal comes from the server.

## Run

```bash
uv sync --locked --group ui
uv run --locked alembic upgrade head
uv run --locked python -m app.ui_demo_setup
uv run --locked uvicorn app.main:app --host 127.0.0.1 --port 8003
uv run --locked --group ui python -m streamlit run ui/app.py
```

`DATABASE_URL` is needed by the API and the setup command, not by the page.
Open `http://127.0.0.1:8501`, keep the API URL and paste a token printed by
`app.ui_demo_setup` (employees `ui-clerk`, `ui-approver`, `ui-viewer`,
`ui-outsider`). The setup also records order `DEMO-PO-UI-001`, policy
`demo-ui-policy` and rules `demo-ui-rules` (the page's defaults) and writes
three samples to `var/ui-samples/`: `clean.txt`, `price_difference.txt`
(passes the basic check, fails reconciliation) and `instruction_in_text.txt`.
Running it again reuses the employees and issues new tokens.

Optional environment variables: `EWA_API_URL`, `EWA_UI_POLICY_ID`,
`EWA_UI_RULES_ID`. `.streamlit/config.toml` disables usage statistics,
listens on `127.0.0.1:8501`, hides the Deploy button and caps uploads at
1 MB.

## Pages

- **Workflows**: a list filtered by state; the selected workflow shows state,
  version, unit, starter, decision, document fields with their source lines,
  the latest reconciliation with stale reasons and quoted discrepancies,
  actions (check, reconcile, pin the latest order version, approve, reject),
  the assistant's draft and trace, the audit trail and outbox events.
- **New invoice**: upload a `.txt`/`.pdf` or paste text; see the extraction
  status, problems and fields; start the workflow and run the basic check.

## Repeated clicks and reloads

Commands send an `Idempotency-Key` built from the intent (action, workflow,
version and relevant inputs such as the snapshot ID) in `ui/keys.py`. The
same intent always gives the same key, so a second submit, another tab or a
reloaded page gets the server's stored result ("repeated request: stored
result"). Because Streamlit reruns the page on every click with fresh data,
a click from an outdated page carries the new version and is refused
(`invalid_transition`). Either way nothing is decided twice. The decision
reason is not part of the key.

The token lives only in the page session; a reload asks for it again.

## Errors

The page shows the server's status, code and message, plus a hint from
`ui/presenters.py` (for example `stale_reconciliation` lists its reasons and
suggests reconciling again). Empty lists, documents created in code,
missing reconciliations, extractions that need review and replaced agent
drafts are stated explicitly.

## API endpoints added for the page

| Method and path | Permission | Returns |
| --- | --- | --- |
| `GET /workflows?state=&limit=` | `view` | Recently changed workflows of the caller's units (limit 1-500, default 100) |
| `GET /workflows/{id}/document` | `view` | Source document, extraction status, problems and fields with citations; 404 for invoices created in code |

## Client

`ui/client.py` uses only the standard library. It accepts only `http`/`https`
URLs (urllib could otherwise open `file://`), quotes path segments, turns
error bodies into `ApiError(status, code, message, details)`, reports an
unreachable server as `api_unreachable` without the token, and returns
`None` for a missing document or reconciliation. Its transport is a function,
so tests send the same requests through FastAPI's in-process client.

## Tests

Install the `ui` group to run the page tests; without it those test modules
are reported as skipped. Client tests need no extra dependency. See
[testing](testing.md).

## Limits

Manual token entry; English labels only; at most 500 workflows listed, no
search; no user or token administration; local HTTP only; checked in the
desktop app's built-in browser on macOS only.
