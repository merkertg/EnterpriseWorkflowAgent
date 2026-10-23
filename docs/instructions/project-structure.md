# Project structure

[README](../../README.md) · [Detailed Russian guide](../STAGE_01.md)

Keep the initial application small and add a layer when it owns real behavior.

## Implemented in Stages 01–03

| Location | Responsibility |
| --- | --- |
| `app/__init__.py` | Application package and version |
| `app/config.py` | Validated environment settings and cached loading |
| `app/main.py` | Application factory, instance settings and router registration |
| `app/api/routes/health.py` | HTTP `GET /health` handler |
| `app/api/schemas/health.py` | Pydantic response contract |
| `app/domain/errors.py` | Stable domain errors with code and field |
| `app/domain/money.py` | Exact two-decimal money, supported currencies and quantity validation |
| `app/domain/models.py` | Immutable invoice, purchase order and line items |
| `app/domain/rules.py` | Basic policy, findings and deterministic validation snapshots |
| `app/domain/workflow.py` | Guarded in-memory transitions and explicit demo decision metadata |
| `app/domain/content_hash.py` | Canonical JSON and SHA-256 hashes of input versions |
| `app/demo.py` | Offline fictional examples printed as JSON |
| `app/application/ports.py` | Stored records, command context and repository/unit-of-work protocols |
| `app/application/errors.py` | Stable application error codes |
| `app/application/workflows.py` | `WorkflowService`: versions, commands, transactions, idempotency |
| `app/infrastructure/db/models.py` | SQLAlchemy tables, `NUMERIC` columns and check constraints |
| `app/infrastructure/db/repository.py` | PostgreSQL repository, hash verification and unit of work |
| `app/infrastructure/db/session.py` | URL validation, test-database guard, engine and service composition |
| `app/persistence_demo.py` | Fictional PostgreSQL scenario and `show` command |
| `alembic.ini`, `migrations/` | Alembic configuration, environment and revisions |
| `tests/` | Automated checks; `tests/persistence/` needs `TEST_DATABASE_URL` |
| `scripts/check_docs.py` | Local Markdown link validation |
| `.github/workflows/ci.yml` | Automated check configuration |
| `pyproject.toml`, `uv.lock` | Dependencies, tooling and reproducible resolution |
| `.python-version` | Python 3.12 interpreter selection |
| `.env.example` | Non-secret configuration example, including a commented `DATABASE_URL` |

The request flow is `client → Uvicorn → FastAPI → health() → HealthResponse
→ JSON`. `create_app()` can accept explicit settings; the route reads settings
from its own application instance.

## Documentation

| Location | Purpose |
| --- | --- |
| `README.md` | Entry point and current project status |
| `docs/STAGE_01.md` | Detailed learning walkthrough in Russian |
| `docs/STAGE_02.md` | Domain, rules, money and workflow walkthrough in Russian |
| `docs/STAGE_03.md` | PostgreSQL, transactions, audit and idempotency walkthrough in Russian |
| `docs/instructions/domain.md` | Concise Python domain usage and demo commands |
| `docs/instructions/persistence.md` | Database setup, migrations, demo and service rules |
| `docs/instructions/` | Short operational instructions in English |
| `docs/CI_CD.md` | CI configuration and limits of remote verification |
| `docs/VERIFICATION.md` | Recorded command outcomes and limitations |
| `docs/guide/verification.json` | Machine-readable verification summary |
| `docs/guide/README.md` | Status of the planned RU/EN PDF guides |

## Planned locations

These are future responsibilities, not implemented components or empty
directories to create now:

| Location | Add when needed for |
| --- | --- |
| `app/infrastructure/` (other adapters) | Document parsers, model providers and event delivery |
| `ui/` | A client that calls the API |
| `data/` | Clearly labelled fictional demonstration inputs |
| `evals/` | Versioned evaluation cases and independent expected results |

The persistence flow is `WorkflowService → UnitOfWork → SqlAlchemyWorkflowRepository
→ PostgreSQL`; domain functions compute every transition before anything is
written. HTTP routes should remain thin. Corporate decisions should be explicit and
testable in ordinary Python before an agent orchestrates them.
