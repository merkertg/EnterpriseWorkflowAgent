"""Liveness endpoint: no database or external services are checked here."""

from typing import cast

from fastapi import APIRouter, Request

from app.api.schemas.health import HealthResponse
from app.config import Settings

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse)
def health(request: Request) -> HealthResponse:
    """Return the name from this app instance, keeping test instances isolated."""
    settings = cast(Settings, request.app.state.settings)
    return HealthResponse(status="ok", service=settings.app_name)
