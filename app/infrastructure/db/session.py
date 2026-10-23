"""Engine construction, the test-database guard and service composition."""

from functools import partial

from sqlalchemy import URL, Engine, create_engine, make_url
from sqlalchemy.exc import ArgumentError
from sqlalchemy.orm import Session, sessionmaker

from app.application.agent import AgentLimits, AgentService
from app.application.agent_protocol import ModelProvider
from app.application.agent_tools import WorkflowToolBox
from app.application.documents import DocumentService
from app.application.ports import FieldExtractor
from app.application.reconciliation import ReconciliationService
from app.application.users import UserService
from app.application.workflows import WorkflowService
from app.domain.documents import DocumentLimits
from app.infrastructure.db.unit_of_work import SqlAlchemyUnitOfWork
from app.infrastructure.documents.text_extraction import LimitedTextExtractor
from app.infrastructure.extraction.labeled_text import LabeledTextInvoiceParser

_DRIVER = "postgresql+psycopg"
# Fail instead of waiting indefinitely on a lock or an unexpectedly slow query.
_SESSION_OPTIONS = "-c lock_timeout=5000 -c statement_timeout=30000"


def parse_database_url(url: str) -> URL:
    """Accept only a PostgreSQL psycopg URL; errors never echo the password."""
    try:
        parsed = make_url(url)
    except ArgumentError as error:
        raise ValueError("Database URL is not a valid SQLAlchemy URL.") from error
    if parsed.drivername != _DRIVER:
        raise ValueError(f"Database URL must use the {_DRIVER}:// driver.")
    if not parsed.database:
        raise ValueError("Database URL must name a database.")
    return parsed


def require_test_database_url(url: str) -> URL:
    """Protect developer data: destructive tests need a database ending in _test."""
    parsed = parse_database_url(url)
    database = parsed.database or ""
    if not database.endswith("_test"):
        raise ValueError("The test database name must end with _test.")
    return parsed


def create_database_engine(url: str | URL) -> Engine:
    """Create a pooled engine with short connect, lock and statement timeouts."""
    parsed = parse_database_url(url) if isinstance(url, str) else url
    return create_engine(
        parsed,
        pool_pre_ping=True,
        connect_args={"connect_timeout": 5, "options": _SESSION_OPTIONS},
    )


def create_session_factory(engine: Engine) -> sessionmaker[Session]:
    """Keep loaded values readable after commit; sessions are short-lived."""
    return sessionmaker(engine, expire_on_commit=False)


def build_workflow_service(engine: Engine) -> WorkflowService:
    """Compose the application service with one new unit of work per call."""
    return WorkflowService(
        partial(SqlAlchemyUnitOfWork, create_session_factory(engine))
    )


def build_document_service(
    engine: Engine,
    limits: DocumentLimits | None = None,
    field_extractor: FieldExtractor | None = None,
) -> DocumentService:
    """Compose document intake; the deterministic parser is the default."""
    return DocumentService(
        partial(SqlAlchemyUnitOfWork, create_session_factory(engine)),
        LimitedTextExtractor(),
        LabeledTextInvoiceParser() if field_extractor is None else field_extractor,
        limits,
    )


def build_reconciliation_service(engine: Engine) -> ReconciliationService:
    """Compose reconciliation with one new unit of work per call."""
    return ReconciliationService(
        partial(SqlAlchemyUnitOfWork, create_session_factory(engine))
    )


def build_user_service(engine: Engine) -> UserService:
    """Compose employee and token management with one unit of work per call."""
    return UserService(partial(SqlAlchemyUnitOfWork, create_session_factory(engine)))


def build_agent_service(
    engine: Engine, provider: ModelProvider, limits: AgentLimits | None = None
) -> AgentService:
    """Compose the review agent with read-only tools over this database."""
    unit_of_work = partial(SqlAlchemyUnitOfWork, create_session_factory(engine))
    return AgentService(provider, WorkflowToolBox(unit_of_work), unit_of_work, limits)
