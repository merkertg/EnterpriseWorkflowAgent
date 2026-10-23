"""Alembic environment: apply migrations to DATABASE_URL or an explicit test URL."""

from logging.config import fileConfig

from alembic import context

from app.config import Settings
from app.infrastructure.db.models import Base
from app.infrastructure.db.session import create_database_engine, parse_database_url

config = context.config
if config.config_file_name is not None and config.attributes.get(
    "configure_logger", True
):
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _database_url() -> str:
    """Prefer a URL passed by tests; otherwise read DATABASE_URL from settings."""
    explicit = config.attributes.get("database_url")
    if isinstance(explicit, str):
        return explicit
    settings = Settings()
    if settings.database_url is None:
        raise RuntimeError("Set DATABASE_URL before running database migrations.")
    return settings.database_url.get_secret_value()


def run_migrations_offline() -> None:
    """Render SQL for review with ``alembic upgrade head --sql``; nothing connects."""
    context.configure(
        url=parse_database_url(_database_url()),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Apply pending revisions in one transaction; PostgreSQL DDL is transactional."""
    engine = create_database_engine(_database_url())
    try:
        with engine.connect() as connection:
            context.configure(
                connection=connection,
                target_metadata=target_metadata,
                compare_type=True,
            )
            with context.begin_transaction():
                context.run_migrations()
    finally:
        engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
