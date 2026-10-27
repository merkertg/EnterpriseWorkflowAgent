# Enterprise Workflow Agent

A staged Python learning project for reviewing a fictional supplier invoice
against a purchase order: receive a document, extract fields with source
evidence, reconcile amounts, obtain an employee's decision and record delivery
to a local sandbox. **Release 0.10.0 implements all ten stages.**

The FastAPI service uses PostgreSQL, immutable input versions, reproducible
reconciliation snapshots, employee roles, audit records and idempotent commands.
A bounded review agent reads facts through four tools and proposes a next step;
it currently runs deterministic offline providers. An authorized employee makes
the approval decision. A Streamlit page uses the HTTP API, while a signed outbox
delivers events to a loopback sandbox with retries and deduplication. Docker
Compose, a smoke scenario and PostgreSQL backup/restore support the local demo.

Deterministic Python controls money, rules, permissions and state changes. The
project uses fictional data and initiates no payments. A real model adapter is
not implemented. Local checks and unverified remote CI are recorded separately
in [verification](docs/VERIFICATION.md) and [limitations](docs/LIMITATIONS.md).

**Начни с [быстрого запуска](docs/instructions/quickstart.md).** После запуска
Compose страница сотрудника доступна на <http://127.0.0.1:8501>, Swagger UI —
на <http://127.0.0.1:8003/docs>. Как получить токены вымышленных сотрудников,
показано в [инструкции запуска](docs/instructions/quickstart.md#get-tokens-and-open-the-page).

## Roadmap

| Stage | Deliverable | Status |
| --- | --- | --- |
| 01 | API foundation, settings, tests, checks and documentation | Implemented; see verification |
| 02 | Domain objects, workflow state machine and basic rules | Implemented; in-memory demo only |
| 03 | PostgreSQL, migrations, transactional audit and idempotency | Implemented; PostgreSQL verified locally |
| 04 | Safe text/PDF ingestion and fields with provenance | Implemented; verified locally |
| 05 | Deterministic invoice reconciliation and discrepancy reports | Implemented; verified locally |
| 06 | Employee approvals, roles and transactional outbox | Implemented; verified locally |
| 07 | Bounded AI tools, demo provider and optional real model | Implemented with offline providers; no real model |
| 08 | Streamlit interface through the HTTP API | Implemented; verified locally and in a browser |
| 09 | Security, failure scenarios, evals and local sandbox event delivery | Implemented; verified locally and in a browser |
| 10 | Docker, expanded CI and reproducible release | Implemented; compose stack verified locally; remote CI not verified |

The [roadmap](docs/ROADMAP.md) defines the scope and acceptance criteria for each
stage; all ten are implemented. [Limitations](docs/LIMITATIONS.md) lists what
the release does not do and what was not verified.

## Documentation

Instructions ([docs/instructions/](docs/instructions/)):

- [Quickstart](docs/instructions/quickstart.md): requirements, Compose, employee tokens, host run and settings
- [Using the system](docs/instructions/usage.md): roles, HTTP API, domain, storage, documents, reconciliation, decisions, review agent, delivery, employee page, evaluations and command-line tools
- [Testing and verification](docs/instructions/testing.md): lint, strict types, offline and PostgreSQL tests, PDF freshness
- [Backup and restore](docs/instructions/backup.md): PostgreSQL export, empty-database restore, integrity checks and scope
- [CI and container delivery](docs/instructions/ci-and-delivery.md): images, HTTP smoke and GitHub Actions jobs
- [Project structure](docs/instructions/project-structure.md): application layers, tools, tests and documentation

Stage learning guides in Russian:

- [Stage 01: FastAPI foundation](docs/STAGE_01.md)
- [Stage 02: domain, rules and workflow](docs/STAGE_02.md)
- [Stage 03: PostgreSQL, transactions and audit](docs/STAGE_03.md)
- [Stage 04: document intake and field evidence](docs/STAGE_04.md)
- [Stage 05: invoice reconciliation](docs/STAGE_05.md)
- [Stage 06: employee decisions, roles and outbox](docs/STAGE_06.md)
- [Stage 07: bounded review agent](docs/STAGE_07.md)
- [Stage 08: Streamlit employee page](docs/STAGE_08.md)
- [Stage 09: delivery, hardening and evaluations](docs/STAGE_09.md)
- [Stage 10: containers and verified backups](docs/STAGE_10.md)
- [Stage plan and completion criteria](docs/ROADMAP.md)
- [CI/CD walkthrough](docs/CI_CD.md)
- [Russian PDF guide](docs/guide/enterprise-workflow-agent-guide.pdf), [English PDF guide](docs/guide/enterprise-workflow-agent-guide-en.pdf) and [rebuild instructions](docs/guide/README.md)
- [Verification report](docs/VERIFICATION.md), [machine-readable evidence](docs/guide/verification.json) and [limitations](docs/LIMITATIONS.md)

Design and history:

- [Solution approach](docs/APPROACH.md)
- [Architecture decisions](docs/adr/)
- [Development log](docs/DEVELOPMENT_LOG.md)
- [Contributor rules](AGENTS.md)
