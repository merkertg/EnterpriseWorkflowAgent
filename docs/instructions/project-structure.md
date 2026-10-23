# Project structure

[README](../../README.md) · [Detailed Russian guide](../STAGE_01.md)

Keep the initial application small and add a layer when it owns real behavior.

## Implemented in Stages 01–05

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
| `app/domain/documents.py` | Intake limits, format detection, text decoding and rejection codes |
| `app/domain/extraction.py` | Field schema, evidence verification and completeness assessment |
| `app/domain/reconciliation.py` | Reconciliation rules, discrepancy codes and the pure `reconcile` function |
| `app/demo.py` | Offline fictional examples printed as JSON |
| `app/application/ports.py` | Stored records, command context and repository/unit-of-work protocols |
| `app/application/errors.py` | Stable application error codes |
| `app/application/workflows.py` | `WorkflowService`: versions, commands, transactions, idempotency |
| `app/application/documents.py` | `prepare_document`, `DocumentService`: intake, storage and evidence |
| `app/application/reconciliation.py` | `ReconciliationService`: snapshots, inputs hash, freshness, reproduction, reports |
| `app/infrastructure/db/models.py` | SQLAlchemy tables, `NUMERIC` columns and check constraints |
| `app/infrastructure/db/repository.py` | PostgreSQL workflow repository and hash verification |
| `app/infrastructure/db/documents.py` | PostgreSQL document repository |
| `app/infrastructure/db/reconciliations.py` | PostgreSQL storage for rule versions and reconciliation snapshots |
| `app/infrastructure/db/inserts.py` | Immediate inserts and constraint-race translation |
| `app/infrastructure/db/unit_of_work.py` | One session per command, shared by both repositories |
| `app/infrastructure/documents/` | Text decoding and the isolated `python -I` PDF worker |
| `app/infrastructure/extraction/labeled_text.py` | Deterministic `Label: value` field extractor |
| `app/infrastructure/db/session.py` | URL validation, test-database guard, engine and service composition |
| `app/persistence_demo.py` | Fictional PostgreSQL scenario and `show` command |
| `app/samples.py` | Fictional text/PDF samples and a deterministic minimal PDF writer |
| `app/documents_cli.py` | Document `write-samples`, `ingest`, `show`, `evidence` and `demo` |
| `app/reconciliation_cli.py` | Reconciliation `demo` and `show` |
| `alembic.ini`, `migrations/` | Alembic configuration, environment and revisions |
| `tests/` | Automated checks; `tests/persistence/` needs `TEST_DATABASE_URL` |
| `scripts/check_docs.py` | Local Markdown link validation |
| `scripts/build_guide.py`, `scripts/guide/` | Bilingual PDF guide generator (ReportLab, `guide` dependency group) |
| `docs/guide/*.pdf` | Russian and English PDF guides with an embedded source fingerprint |
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
| `docs/STAGE_04.md` | Document intake and field provenance walkthrough in Russian |
| `docs/instructions/documents.md` | Intake limits, field schema, parser, storage and commands |
| `docs/STAGE_05.md` | Reconciliation walkthrough in Russian |
| `docs/instructions/reconciliation.md` | Rules, codes, snapshots, freshness and commands |
| `docs/instructions/` | Short operational instructions in English |
| `docs/CI_CD.md` | CI configuration and limits of remote verification |
| `docs/VERIFICATION.md` | Recorded command outcomes and limitations |
| `docs/guide/verification.json` | Machine-readable verification summary |
| `docs/guide/README.md` | How the RU/EN PDF guides are built, checked and updated |

## Planned locations

These are future responsibilities, not implemented components or empty
directories to create now:

| Location | Add when needed for |
| --- | --- |
| `app/infrastructure/` (other adapters) | Model providers and event delivery |
| `ui/` | A client that calls the API |
| `evals/` | Versioned evaluation cases and independent expected results |

The persistence flow is `WorkflowService → UnitOfWork → SqlAlchemyWorkflowRepository
→ PostgreSQL`; domain functions compute every transition before anything is
written. HTTP routes should remain thin. Corporate decisions should be explicit and
testable in ordinary Python before an agent orchestrates them.
