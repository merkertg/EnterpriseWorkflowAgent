"""Compose the FastAPI application and expose the Uvicorn entry point."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy import Engine
from sqlalchemy.exc import OperationalError, ProgrammingError

from app import __version__
from app.api.dependencies import Services
from app.api.errors import install_error_handlers
from app.api.limits import MAX_JSON_BODY_BYTES, BodySizeLimit
from app.api.routes.business import router as business_router
from app.api.routes.health import router as health_router
from app.config import Settings, get_settings
from app.domain.documents import DocumentLimits
from app.infrastructure.db.session import (
    build_agent_service,
    build_document_service,
    build_reconciliation_service,
    build_user_service,
    build_workflow_service,
    create_database_engine,
)
from app.infrastructure.llm.demo import DemoRulesProvider


def build_services(settings: Settings) -> tuple[Services | None, Engine | None]:
    """Compose services from DATABASE_URL; without it only /health works.

    Creating an engine does not connect, so the API starts even if PostgreSQL
    is down; the first business request then reports the failure.
    """
    if settings.database_url is None:
        return None, None
    engine = create_database_engine(settings.database_url.get_secret_value())
    limits = DocumentLimits()
    services = Services(
        workflows=build_workflow_service(engine),
        documents=build_document_service(engine, limits),
        reconciliations=build_reconciliation_service(engine),
        users=build_user_service(engine),
        # Stage 07 offers only the deterministic offline provider over HTTP.
        agent=build_agent_service(engine, DemoRulesProvider()),
        max_document_bytes=limits.max_bytes,
    )
    return services, engine


async def _database_unavailable(_request: Request, _error: Exception) -> JSONResponse:
    """Report a down database or a missing schema without driver details."""
    return JSONResponse(
        {
            "error": {
                "code": "database_unavailable",
                "message": "The database is unreachable or not upgraded.",
            }
        },
        status_code=503,
    )


def create_app(
    settings: Settings | None = None, services: Services | None = None
) -> FastAPI:
    """Build an app with explicit test settings or the cached process settings.

    Tests may pass ready services; otherwise they are composed from settings.
    """
    resolved_settings = settings if settings is not None else get_settings()
    engine = None
    if services is None:
        services, engine = build_services(resolved_settings)

    @asynccontextmanager
    async def lifespan(_application: FastAPI) -> AsyncIterator[None]:
        """Close pooled database connections when the server stops."""
        yield
        if engine is not None:
            engine.dispose()

    application = FastAPI(
        title=resolved_settings.app_name,
        version=__version__,
        debug=resolved_settings.app_debug,
        description=(
            "Learning project: document intake, reconciliation and employee "
            "decisions on fictional invoices. Business endpoints need a bearer "
            "token and DATABASE_URL."
        ),
        lifespan=lifespan,
    )
    # Each app owns its settings, so constructing another app cannot overwrite them.
    application.state.settings = resolved_settings
    application.state.services = services
    install_error_handlers(application)
    # Uploads stream with their own limit; other bodies are small JSON commands.
    application.add_middleware(
        BodySizeLimit,
        limit=MAX_JSON_BODY_BYTES,
        exempt_paths=frozenset({"/documents"}),
    )
    application.add_exception_handler(OperationalError, _database_unavailable)
    application.add_exception_handler(ProgrammingError, _database_unavailable)
    application.include_router(health_router)
    application.include_router(business_router)
    return application


app = create_app()
