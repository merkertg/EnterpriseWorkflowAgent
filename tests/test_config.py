"""Check configuration precedence and invalid values without external services."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from app.config import Settings, get_settings


def test_settings_have_local_defaults() -> None:
    """An optional .env is not required to start the learning service."""
    settings = Settings()
    assert settings.app_name == "enterprise-workflow-agent"
    assert settings.app_env == "development"
    assert settings.app_debug is False
    assert settings.database_url is None


def test_environment_overrides_dotenv(monkeypatch: pytest.MonkeyPatch) -> None:
    """Process settings take precedence over a value read from the local file."""
    Path(".env").write_text("APP_NAME=from-file\nAPP_ENV=test\n", encoding="utf-8")
    monkeypatch.setenv("APP_NAME", "from-environment")
    settings = Settings()
    assert settings.app_name == "from-environment"
    assert settings.app_env == "test"


def test_explicit_settings_override_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """App factories can inject known settings independently of process values."""
    monkeypatch.setenv("APP_NAME", "from-environment")
    assert Settings(app_name="explicit").app_name == "explicit"


@pytest.mark.parametrize("name", ["", " ", "\t\n"])
def test_empty_service_name_is_rejected(name: str) -> None:
    """A whitespace-only service name cannot pass startup validation."""
    with pytest.raises(ValidationError):
        Settings(app_name=name)


def test_service_name_is_trimmed() -> None:
    """Surrounding whitespace must not leak into API and OpenAPI metadata."""
    assert Settings(app_name="  demo  ").app_name == "demo"


def test_invalid_environment_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    """A typo in a named environment fails early rather than silently changing mode."""
    monkeypatch.setenv("APP_ENV", "prodution")
    with pytest.raises(ValidationError):
        Settings()


def test_invalid_debug_value_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    """An invalid boolean cannot accidentally enable or disable traceback output."""
    monkeypatch.setenv("APP_DEBUG", "perhaps")
    with pytest.raises(ValidationError):
        Settings()


def test_settings_are_cached_until_clear(monkeypatch: pytest.MonkeyPatch) -> None:
    """The running process keeps one snapshot until its settings cache is reset."""
    first = get_settings()
    monkeypatch.setenv("APP_NAME", "updated")
    assert get_settings() is first
    get_settings.cache_clear()
    assert get_settings().app_name == "updated"


def test_database_url_is_read_but_hidden_from_representations(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A connection password must not appear when settings are printed or logged."""
    url = "postgresql+psycopg://demo:fictional-password@127.0.0.1:5432/demo_db"
    monkeypatch.setenv("DATABASE_URL", url)
    settings = Settings()
    assert settings.database_url is not None
    assert settings.database_url.get_secret_value() == url
    assert "fictional-password" not in repr(settings)
    assert "fictional-password" not in str(settings.model_dump())
