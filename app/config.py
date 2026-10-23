"""Validated application settings loaded from the environment and optional .env."""

from functools import lru_cache
from typing import Annotated, Literal

from pydantic import SecretStr, StringConstraints
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Read explicit values, environment variables, .env, then defaults in order."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        hide_input_in_errors=True,
    )

    app_name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)] = (
        "enterprise-workflow-agent"
    )
    app_env: Literal["development", "test", "production"] = "development"
    # Keep debug tracebacks off unless the local developer explicitly enables them.
    app_debug: bool = False
    # Optional until a database command runs; SecretStr keeps passwords out of reprs.
    database_url: SecretStr | None = None


@lru_cache
def get_settings() -> Settings:
    """Load settings once per process; restart the server after editing .env."""
    return Settings()
