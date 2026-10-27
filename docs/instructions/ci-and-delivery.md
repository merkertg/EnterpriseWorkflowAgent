# CI and container delivery

[← README](../../README.md) · [All documentation](../../README.md#documentation)

How the release images are built, how a running stack is checked, and what
GitHub Actions runs. Release `0.10.0` is delivered as source plus locally
built images: nothing is published to a registry, there is no TLS, and every
port listens on `127.0.0.1` only. The detailed Russian explanation of the
pipeline is [CI_CD.md](../CI_CD.md).

## Images

`Dockerfile` builds two targets from one source tree:

| Target | Contents |
| --- | --- |
| `app` | Runtime dependencies (`uv sync --locked --no-dev`): API, migrations, worker, receiver and command-line tools |
| `ui` | Runtime dependencies plus the `ui` group (Streamlit) |

- **Pinning:** the Python 3.12.15 slim and uv 0.12.23 base images are pinned
  by digest; Python packages come only from `uv.lock`.
- **User:** processes run as `app` (uid 10001).
- **Build context:** `.dockerignore` keeps `.git`, `.env`, `var/`, `.venv`,
  docs and tests out of it.
- **Secrets:** images contain none; settings come from the environment at
  run time.

```bash
docker compose build
docker images --format '{{.Repository}}:{{.Tag}} {{.Size}}' | grep enterprise
```

How to start, update and stop the stack is in the
[quickstart](quickstart.md#start-the-stack-with-docker-compose).

## Smoke check

`python -m app.smoke --api URL [--wait 60]` prepares fictional employees and
reference data through the database settings. It then runs the scenario over
HTTP: health, sign-in, upload, start, check, reconcile, approve, and waiting
for delivery. It prints a JSON report and exits 0 only if every step matched;
`--wait 0` skips the delivery step.

```bash
docker compose run --rm --no-deps api python -m app.smoke --api http://api:8003
uv run --locked python -m app.smoke --api http://127.0.0.1:8003    # native setup
```

## GitHub Actions

`.github/workflows/ci.yml` runs on pushes and pull requests to `main` and on
manual dispatch:

| Job | What it runs |
| --- | --- |
| `checks` | Lint, formatting, strict types, migrations, `alembic check`, all tests with a PostgreSQL service, documentation links |
| `evaluations` | Runs `python -m app.evals` on its own database and keeps the report as an artifact for 14 days |
| `compose` | Builds images, waits for API and UI health, runs the smoke check, then exports, replaces only the PostgreSQL volume, restores and verifies the database |

The Compose job uses its own project name and random per-run secrets, masked
in the log. During the restore cycle it keeps the receiver volume, whose
SQLite ledger is outside the PostgreSQL backup. Only the one `pgdata` volume
matching that project's Compose labels is removed; an unexpected number of
matches stops the cycle. Final CI cleanup removes the job's remaining
disposable volumes. Actions are pinned by commit SHA.

A workflow file is not evidence of a successful run. Remote results are
recorded only after they have been read; see
[VERIFICATION.md](../VERIFICATION.md).

## Limits

Locally verified on macOS (Docker Desktop, arm64) only. There is no image
registry, TLS, rolling update, monitoring or secret manager. The `ui` image
is large (about 880 MB) because of Streamlit's dependencies.
