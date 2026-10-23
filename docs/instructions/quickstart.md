# Quickstart

[README](../../README.md) · [Detailed Stage 01 guide in Russian](../STAGE_01.md)

Stages 01–06 provide a local FastAPI service, an in-memory domain
demonstration, PostgreSQL persistence, document intake, reconciliation and
employee decisions. `/health` works without a database; the business endpoints
need `DATABASE_URL`, an upgraded schema and a bearer token.

## Prepare

Requirements: uv and Python 3.12. uv can provision the interpreter if needed.
Run commands from the directory containing `pyproject.toml`:

```bash
uv --version
uv sync --locked
uv run --locked python --version
```

The supported Python range is `>=3.12,<3.13`. Use `uv run --locked` rather than
the system Python. `uv sync --locked` installs the resolved dependencies and
fails if the lock file needs an update.

All current settings have defaults, so `.env` is optional. To customize them,
inspect `.env.example` and create `.env` only if it does not already exist.
Keep an existing `.env`; do not overwrite local settings.

## Run

```bash
uv run --locked uvicorn app.main:app --reload --host 127.0.0.1 --port 8003
```

Keep this terminal open. In a second terminal:

```bash
curl -i http://127.0.0.1:8003/health
```

Expected default response: HTTP `200` and
`{"status":"ok","service":"enterprise-workflow-agent"}`.

Open [Swagger UI](http://127.0.0.1:8003/docs),
[ReDoc](http://127.0.0.1:8003/redoc), or
[OpenAPI JSON](http://127.0.0.1:8003/openapi.json).
Stop the server with `Ctrl+C`. If port 8003 is busy, select another port and
use it consistently in the URLs.

## Settings

| Variable | Default | Meaning |
| --- | --- | --- |
| `APP_NAME` | `enterprise-workflow-agent` | Service name; surrounding whitespace is removed and an empty name is rejected |
| `APP_ENV` | `development` | One of `development`, `test`, `production` |
| `APP_DEBUG` | `false` | Local debugging; it does not grant access permissions |
| `DATABASE_URL` | unset | `postgresql+psycopg://...` URL for migrations, command-line tools and the business API; hidden in settings output |

Environment variables override `.env`, which overrides defaults. Explicit
`Settings(...)` constructor arguments have higher priority. Unknown `.env`
keys are ignored. Settings are cached; restart the server after changes.

Continue with [API usage](usage.md) or [testing](testing.md).

For the offline Stage 02 workflow, run `uv run --locked python -m app.demo`.
No Uvicorn or credentials are needed; see [domain usage](domain.md).

For Stage 03 storage, create PostgreSQL databases, set `DATABASE_URL`, then run
`uv run --locked alembic upgrade head` and
`uv run --locked python -m app.persistence_demo`; see [persistence](persistence.md).

For Stage 04 documents, run
`uv run --locked python -m app.documents_cli write-samples var/samples` (no
database needed), then `ingest`, `evidence` or `demo`; see
[documents](documents.md).

For Stage 05 reconciliation, run
`uv run --locked python -m app.reconciliation_cli demo`; see
[reconciliation](reconciliation.md).

For Stage 06, create a fictional employee and token with
`uv run --locked python -m app.users_cli create ...` and `token ...`, then call
the API with `Authorization: Bearer ...`, or run
`uv run --locked python -m app.decisions_demo`; see [decisions](decisions.md)
and [API usage](usage.md).
