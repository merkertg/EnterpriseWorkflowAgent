# Reproducible release with Docker Compose

[README](../../README.md) · [Stage 10 guide (RU)](../STAGE_10.md) ·
[ADR 0010](../adr/0010-containers-and-logical-backups.md) ·
[Backup and restore](backup.md) · [Limitations (RU)](../LIMITATIONS.md)

Release `0.10.0` runs the whole learning stack locally from pinned images.
It is not a production deployment: nothing is published, there is no TLS,
and every port listens on `127.0.0.1` only.

## Requirements

Docker Engine or Docker Desktop with Compose v2. No Python or uv is needed
on the host for the stack itself. The Bash snippet below uses OpenSSL to
generate secrets; it is normally available on macOS and Ubuntu. Alternatively,
set your own nonempty database password and random webhook secret of at least
32 characters in the shell or local `.env`.

## Start

```bash
export POSTGRES_PASSWORD="$(openssl rand -hex 24)"
export SANDBOX_WEBHOOK_SECRET="$(openssl rand -hex 24)"
docker compose up -d --build --wait
docker compose run --rm --no-deps api python -m app.smoke --api http://api:8003
docker compose run --rm --no-deps api python -m app.ui_demo_setup
```

Compose refuses to start without both variables. They may also live in a
local `.env` next to `compose.yaml` (ignored by Git); never commit them.

| URL | Service |
| --- | --- |
| `http://127.0.0.1:8003/docs` | API and Swagger UI |
| `http://127.0.0.1:8501` | Streamlit page (paste a token from `ui_demo_setup`) |

## Services

| Service | Image | Role |
| --- | --- | --- |
| `postgres` | `postgres:18.3@sha256:…` (same digest as CI) | Database, volume `pgdata` |
| `migrate` | `app` | `alembic upgrade head`, then exits |
| `api` | `app` | Uvicorn on 8003 with a `/health` health check |
| `receiver` | `app` | Sandbox webhook receiver on `127.0.0.1:8010` in its own network namespace, volume `receiver` |
| `worker` | `app` | Outbox worker sharing the receiver's network namespace, so the destination stays loopback |
| `ui` | `ui` | Streamlit on 8501 with `/_stcore/health`, calling the API at `http://api:8003` |

## Images

`Dockerfile` builds two targets from one source tree:

| Target | Contents |
| --- | --- |
| `app` | Runtime dependencies (`uv sync --locked --no-dev`) |
| `ui` | Runtime dependencies plus the `ui` group (Streamlit) |

- **Pinning:** the Python 3.12.15 slim and uv 0.12.23 base images are pinned
  by digest.
- **User:** processes run as `app` (uid 10001).
- **Build context:** `.dockerignore` keeps `.git`, `.env`, `var/`, `.venv`,
  docs and tests out of it.
- **Secrets:** images contain none; settings come from the environment at
  run time.

## Smoke check

`python -m app.smoke --api URL [--wait 60]` prepares fictional employees and
reference data through `DATABASE_URL`. It then runs the scenario over HTTP:
health, sign-in, upload, start, check, reconcile, approve, and waiting for
delivery. It prints a JSON report and exits 0 only if every step matched.
`--wait 0` skips the delivery step. It also works against a native setup:
`uv run --locked python -m app.smoke --api http://127.0.0.1:8003`.

## Stop and clean up

```bash
docker compose down          # keep the data volumes
docker compose down -v       # also delete the database and receiver volumes
```

## CI

`.github/workflows/ci.yml` has three jobs:

| Job | What it runs |
| --- | --- |
| `checks` | Lint, types, migrations and all tests with PostgreSQL |
| `evaluations` | Runs `python -m app.evals` and keeps the report as an artifact |
| `compose` | Builds images, waits for API and UI health, runs the smoke check, then exports, replaces only the PostgreSQL volume, restores and verifies the database |

The Compose job uses its own project name. During the restore cycle it keeps
the receiver volume, whose SQLite ledger is outside the PostgreSQL backup.
Only the one `pgdata` volume matching that project's Compose labels is removed;
an unexpected number of matches stops the cycle. Final CI cleanup removes the
job's remaining disposable volumes.

A workflow file is not evidence of a successful run. See
[verification](../VERIFICATION.md) for what was actually checked.

## Limits

Locally verified on macOS (Docker Desktop, arm64) only. There is no image
registry, TLS, rolling update, monitoring or secret manager. The `ui` image
is large (about 880 MB) because of Streamlit's dependencies.
