# Quickstart

[← README](../../README.md) · [All documentation](../../README.md#documentation)

Start the whole stack with Docker Compose, get fictional employee tokens and
open the page, or run the API and its tools directly on the host.

## Requirements

- **Compose stack:** Docker Engine or Docker Desktop with Compose v2. No Python
  or uv is needed on the host. The snippets use OpenSSL to generate secrets;
  it is normally available on macOS and Ubuntu.
- **Host run:** [uv](https://docs.astral.sh/uv/getting-started/installation/)
  and Python 3.12 (`>=3.12,<3.13`; uv can provision it), plus PostgreSQL 18 for
  everything beyond `/health` and the offline domain demo.

Verified on macOS (Apple Silicon) only; see
[LIMITATIONS.md](../LIMITATIONS.md).

## Start the stack with Docker Compose

```bash
export POSTGRES_PASSWORD="$(openssl rand -hex 24)"
export SANDBOX_WEBHOOK_SECRET="$(openssl rand -hex 24)"
docker compose up -d --build --wait
docker compose run --rm --no-deps api python -m app.smoke --api http://api:8003
```

Compose refuses to start without both variables. They may also live in a
local `.env` next to `compose.yaml` (ignored by Git); never commit them. The
smoke check drives the whole scenario over HTTP and waits until the decision
event is delivered; it exits 0 only if every step matched.

| Service | Image | Role |
| --- | --- | --- |
| `postgres` | `postgres:18.3@sha256:…` (same digest as CI) | Database, volume `pgdata` |
| `migrate` | `app` | `alembic upgrade head`, then exits |
| `api` | `app` | Uvicorn on `127.0.0.1:8003` with a `/health` health check |
| `receiver` | `app` | Sandbox webhook receiver on `127.0.0.1:8010` in its own network namespace, volume `receiver` |
| `worker` | `app` | Outbox worker sharing the receiver's network namespace, so the destination stays loopback |
| `ui` | `ui` | Streamlit on `127.0.0.1:8501` with `/_stcore/health`, calling the API at `http://api:8003` |

## Get tokens and open the page

```bash
docker compose run --rm --no-deps api python -m app.ui_demo_setup
```

The command prints fresh tokens for four fictional employees (`ui-clerk`,
`ui-approver`, `ui-viewer`, `ui-outsider`) once and records the reference data
the page uses by default. Open:

| URL | What |
| --- | --- |
| `http://127.0.0.1:8501` | Employee page: paste a token |
| `http://127.0.0.1:8003/docs` | Swagger UI: authorize with a token |

How to work with the page, the API and every tool is described in
[usage](usage.md).

## Stop or update the stack

```bash
docker compose down                     # stop, keep the data volumes
docker compose up -d --build --wait     # rebuild after pulling new code
docker compose down -v                  # also delete the database and receiver data
```

Migrations run automatically on every start through the `migrate` service.
Before deleting volumes, make a [backup](backup.md) if the data matters.

## Run on the host

Install the locked dependencies (add `--group ui` for the page):

```bash
uv sync --locked
uv run --locked python -m app.demo                # offline domain demo
uv run --locked uvicorn app.main:app --reload --host 127.0.0.1 --port 8003
curl -i http://127.0.0.1:8003/health
```

`/health` answers `{"status":"ok","service":"enterprise-workflow-agent"}`
without a database. For everything else create PostgreSQL databases (the test
database name must end in `_test`):

```bash
createuser --pwprompt enterprise_workflow
createdb --owner enterprise_workflow enterprise_workflow
createdb --owner enterprise_workflow enterprise_workflow_test
```

Put `DATABASE_URL` in the environment or an untracked `.env`, migrate, then
start what you need:

```bash
export DATABASE_URL=postgresql+psycopg://enterprise_workflow:YOUR_PASSWORD@127.0.0.1:5432/enterprise_workflow
uv run --locked alembic upgrade head
uv run --locked python -m app.ui_demo_setup
uv run --locked uvicorn app.main:app --host 127.0.0.1 --port 8003
uv run --locked --group ui python -m streamlit run ui/app.py
uv run --locked python -m sandbox.receiver                # needs SANDBOX_WEBHOOK_SECRET
uv run --locked python -m app.outbox_worker run           # same secret and DATABASE_URL
```

Keep each long-running command in its own terminal. If port 8003 is busy,
choose another port and use it consistently.

## Settings

| Variable | Default | Meaning |
| --- | --- | --- |
| `APP_NAME` | `enterprise-workflow-agent` | Service name; surrounding whitespace is removed and an empty name is rejected |
| `APP_ENV` | `development` | One of `development`, `test`, `production` (a label, not a deployment mode) |
| `APP_DEBUG` | `false` | Local debugging; it does not grant access permissions |
| `DATABASE_URL` | unset | `postgresql+psycopg://...` URL for migrations, command-line tools and the business API; hidden in settings output |
| `DATABASE_HOST`, `DATABASE_NAME`, `DATABASE_USER`, `DATABASE_PASSWORD` | unset | Alternative raw connection components; all four are required together |
| `DATABASE_PORT` | `5432` | Component connection port, 1–65535 |
| `SANDBOX_WEBHOOK_URL` | `http://127.0.0.1:8010/webhook` | The only outbox destination; must be http(s) on `127.0.0.1` or `[::1]` |
| `SANDBOX_WEBHOOK_SECRET` | unset | Signing secret for the outbox worker and receiver (32+ characters); hidden in settings output |
| `POSTGRES_PASSWORD` | unset | Compose only: the database password, required |
| `EWA_API_URL`, `EWA_UI_POLICY_ID`, `EWA_UI_RULES_ID` | see [usage](usage.md#employee-page) | Page defaults |

Environment variables override `.env`, which overrides defaults; explicit
`Settings(...)` arguments have the highest priority. Unknown `.env` keys are
ignored. An explicit `DATABASE_URL` takes priority over the components.
Compose supplies raw components so reserved characters in a password stay
literal. Settings are cached; restart a process after changing them.
`TEST_DATABASE_URL` is read only by the tests, never from `.env`; see
[testing](testing.md).
