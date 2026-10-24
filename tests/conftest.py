"""Test fixtures use temporary settings and never read the developer's .env."""

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings


@pytest.fixture(autouse=True)
def isolated_settings(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> Iterator[None]:
    """Clear cached settings and remove application variables for every test."""
    monkeypatch.chdir(tmp_path)
    for name in (
        "APP_NAME",
        "APP_ENV",
        "APP_DEBUG",
        "DATABASE_URL",
        "DATABASE_HOST",
        "DATABASE_PORT",
        "DATABASE_NAME",
        "DATABASE_USER",
        "DATABASE_PASSWORD",
        "SANDBOX_WEBHOOK_URL",
        "SANDBOX_WEBHOOK_SECRET",
    ):
        monkeypatch.delenv(name, raising=False)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def client() -> Iterator[TestClient]:
    """Import and construct the app after settings isolation has taken effect."""
    from app.main import create_app

    with TestClient(create_app()) as test_client:
        yield test_client
