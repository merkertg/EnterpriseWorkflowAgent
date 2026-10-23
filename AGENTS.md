# Enterprise Workflow Agent development rules

- Build one stage at a time. The current scope and completion criteria are in
  [the roadmap](docs/ROADMAP.md); implemented behaviour must be distinguished
  from planned behaviour in all documentation.
- Use Python 3.12, uv and `uv.lock`. Keep commands reproducible with `--locked`.
- Keep business rules and workflow transitions in pure Python domain modules.
  Use cases in `app/application` depend on the domain and on ports, never on
  SQLAlchemy. HTTP, database and model adapters depend on the domain; the
  domain must not import their frameworks.
- LLM output is an untrusted proposal. Deterministic validation and authorized
  human decisions control approvals. No autonomous payments or real external
  business actions are part of this learning project.
- Use only fictional fixtures. Never commit secrets, actual invoices, personal
  data, local `.env`, uploaded documents or model request logs.
- Use English for code, function docstrings, README, short instructions,
  APPROACH and ADRs. Explain every new function's purpose and nontrivial choice.
  Use accessible Russian for stage guides, the roadmap, development log and
  verification report. Commit messages, when requested, are in English.
- Update affected instructions with each change: behaviour, usage and checks.
  Record actual work in [the log](docs/DEVELOPMENT_LOG.md), checks and limits in
  [verification](docs/VERIFICATION.md), and the matching machine-readable
  [summary](docs/guide/verification.json).
- Detailed guides for later stages are added when those stages are implemented.
  Future directories and unavailable files must not be presented as shipped.
- Keep the Russian and English PDF guides in `docs/guide/` current. After any
  change to the generator, `verification.json`, `app/`, migrations or
  `uv.lock`, rebuild both with
  `uv run --locked --group guide python scripts/build_guide.py`, inspect every
  page of both, and update their sections when a stage changes.
  `tests/test_guide_pdfs.py` fails if a PDF is older than its sources.
- Run Ruff, strict mypy, pytest and the local documentation link checker for
  relevant code changes. Tests must not depend on a developer's `.env`, network
  or external credentials. PostgreSQL integration tests read only
  `TEST_DATABASE_URL`, whose database name must end in `_test`; without it they
  are skipped, so report whether they actually ran.
- Change the schema only through a new Alembic revision; never edit an applied
  one. `alembic check` must report no differences from the models.
- Separate local checks, deterministic demo runs, real model experiments and
  remote CI evidence. A workflow file does not demonstrate a successful CI run.
