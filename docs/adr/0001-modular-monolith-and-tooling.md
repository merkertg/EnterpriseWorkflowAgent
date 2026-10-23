# ADR 0001: Modular monolith and initial Python tooling

- Status: Accepted
- Date: 2026-10-23
- Scope: Initial project foundation and direction for subsequent stages

## Context

Enterprise Workflow Agent is a learning project for invoice review against a
structured purchase order and employee approval. Goods receipt is outside the
two-document scenario. It needs a traceable implementation that can grow
in stages without claiming that planned integrations already exist. A distributed
system would add deployment and coordination work before the business workflow
has been established.

The initial deliverable is an HTTP health endpoint and validated configuration.
Later stages add deterministic rules, persistence, documents, human decisions
and a constrained AI assistant. These responsibilities need boundaries, but not
empty frameworks at the start.

## Decision

1. Build one deployable Python application as a modular monolith. Start with
   `app/main.py`, `app/config.py`, `app/api/routes/health.py` and
   `app/api/schemas/health.py`. Add domain, application and infrastructure modules
   when their first behavior is implemented.
2. Use Python 3.12, `uv`, FastAPI and Pydantic Settings. Use pytest, Ruff and strict
   mypy locally and configure the same checks in GitHub Actions. Reserve local
   port `8003` for the default development instructions.
3. Keep business rules and state transitions independent of HTTP, SQLAlchemy and
   model SDKs. Application use cases coordinate rules, authorization and ports;
   infrastructure adapters implement persistence and provider boundaries.
4. Keep financial calculations and transition authorization deterministic. An
   LLM may propose validated fields and explanations. It cannot approve an
   invoice or initiate a payment. Use fictional input throughout the tutorial.
5. Introduce PostgreSQL, SQLAlchemy and Alembic only in the persistence stage.
   Add transactional audit, optimistic concurrency and idempotency at that stage.
   Persist immutable source, purchase-order and policy versions with content
   hashes. Reconciliation and approval reference an exact snapshot; reject an
   approval if the applicable inputs have changed since calculation.
   Add an outbox with human approval. Stage 09 includes a delivery worker and a
   signed webhook to one fixed local demo receiver; untrusted input cannot select
   a delivery address.
6. Track implementation and evidence separately: the roadmap describes intended
   work, stage guides explain implemented work, and the verification report
   records actual commands, results and unverified integrations.

## Alternatives considered

- **Microservices from the first stage:** rejected for the initial scope because
  independent deployments, messaging and distributed failures would obscure the
  basic workflow. A module can be extracted later if concrete operational needs
  justify the cost.
- **A single handler containing parsing, rules and persistence:** rejected
  because it would couple business decisions to HTTP and make state transitions
  harder to test independently.
- **LLM-led approval:** rejected because generated output is not an authority for
  monetary calculations, permissions or business state changes.
- **Database and agent framework in Stage 01:** deferred because neither is
  needed to verify application startup and the first HTTP contract.

## Consequences

The first stage stays small and can be understood without a database or API key.
Later stages must preserve dependency direction and introduce interfaces only
where a real external boundary exists. A modular monolith does not provide
automatic process isolation or independent scaling; those needs require a new
decision if they arise.

Creating a CI workflow is part of the initial foundation. A passing remote CI
run, real-provider behavior and production suitability require separate evidence.
This decision does not authorize real external business actions or deployment.
