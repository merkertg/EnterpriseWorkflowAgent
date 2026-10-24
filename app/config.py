"""Validated application settings loaded from the environment and optional .env."""

from functools import lru_cache
from typing import Annotated, Literal
from urllib.parse import urlsplit

from pydantic import AfterValidator, SecretStr, StringConstraints, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# IP literals only: a name such as "localhost" can be pointed elsewhere.
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1"})
MIN_SECRET_LENGTH = 32


def require_loopback_url(url: str) -> str:
    """Accept only an http(s) URL on a loopback IP, without credentials.

    The outbox worker may deliver nowhere else, so neither a document nor an
    API request can choose a destination, and a typo cannot reach the network.
    """
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https"):
        raise ValueError("The sandbox webhook URL must use http or https.")
    if parts.hostname not in LOOPBACK_HOSTS:
        raise ValueError("The sandbox webhook must run on 127.0.0.1 or [::1].")
    if parts.username or parts.password or parts.query or parts.fragment:
        raise ValueError("The sandbox webhook URL takes no credentials or query.")
    return url


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
    # Stage 09: the only destination of outbox events, a local sandbox receiver.
    sandbox_webhook_url: Annotated[str, AfterValidator(require_loopback_url)] = (
        "http://127.0.0.1:8010/webhook"
    )
    # Shared with the receiver to sign deliveries; only the worker needs it.
    sandbox_webhook_secret: SecretStr | None = None

    @field_validator("sandbox_webhook_secret")
    @classmethod
    def _long_secret(cls, value: SecretStr | None) -> SecretStr | None:
        """A short secret could be guessed; the message never shows the value."""
        if value is not None and len(value.get_secret_value()) < MIN_SECRET_LENGTH:
            raise ValueError(
                f"SANDBOX_WEBHOOK_SECRET must have at least {MIN_SECRET_LENGTH} "
                "characters."
            )
        return value


@lru_cache
def get_settings() -> Settings:
    """Load settings once per process; restart the server after editing .env."""
    return Settings()
