"""One SQLAlchemy session per command, shared by both repositories."""

from types import TracebackType
from typing import Self

from sqlalchemy.orm import Session, sessionmaker

from app.infrastructure.db.documents import SqlAlchemyDocumentRepository
from app.infrastructure.db.repository import SqlAlchemyWorkflowRepository


class SqlAlchemyUnitOfWork:
    """Open one session per command; leaving without commit rolls back."""

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        """Store the factory; the session is created when the block starts."""
        self._session_factory = session_factory
        self._session: Session | None = None
        self._repository: SqlAlchemyWorkflowRepository | None = None
        self._documents: SqlAlchemyDocumentRepository | None = None

    @property
    def repository(self) -> SqlAlchemyWorkflowRepository:
        """Return the repository for the open transaction."""
        if self._repository is None:
            raise RuntimeError("Use the unit of work inside a with block.")
        return self._repository

    @property
    def documents(self) -> SqlAlchemyDocumentRepository:
        """Return the document repository sharing this transaction."""
        if self._documents is None:
            raise RuntimeError("Use the unit of work inside a with block.")
        return self._documents

    def __enter__(self) -> Self:
        """Start a session; SQLAlchemy begins the transaction on first use."""
        self._session = self._session_factory()
        self._repository = SqlAlchemyWorkflowRepository(self._session)
        self._documents = SqlAlchemyDocumentRepository(self._session)
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Discard uncommitted work, including after an exception, and close."""
        if self._session is not None:
            self._session.rollback()
            self._session.close()
        self._session = None
        self._repository = None
        self._documents = None

    def commit(self) -> None:
        """Commit every write made through this unit's repository."""
        if self._session is None:
            raise RuntimeError("Use the unit of work inside a with block.")
        self._session.commit()
