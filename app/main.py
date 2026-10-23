"""Compose the FastAPI application and expose the Uvicorn entry point."""

from fastapi import FastAPI

from app import __version__
from app.api.routes.health import router as health_router
from app.config import Settings, get_settings


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build an app with explicit test settings or the cached process settings."""
    resolved_settings = settings if settings is not None else get_settings()
    application = FastAPI(
        title=resolved_settings.app_name,
        version=__version__,
        debug=resolved_settings.app_debug,
        description="Learning foundation for document checks and human approvals.",
    )
    # Each app owns its settings, so constructing another app cannot overwrite them.
    application.state.settings = resolved_settings
    application.include_router(health_router)
    return application


app = create_app()
