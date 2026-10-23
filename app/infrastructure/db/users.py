"""SQLAlchemy storage for employees and hashed API tokens."""

from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.application.access import Role
from app.application.ports import ApiTokenRecord, UserRecord
from app.infrastructure.db.inserts import insert_rows
from app.infrastructure.db.models import ApiTokenRow, UserRow
from app.infrastructure.db.repository import reconstructing


def _user(row: UserRow) -> UserRecord:
    """Map a row to a user record with typed roles."""
    with reconstructing("user"):
        roles = frozenset(Role(role) for role in row.roles)
    return UserRecord(
        user_id=row.user_id,
        display_name=row.display_name,
        roles=roles,
        units=frozenset(row.units),
        active=row.active,
    )


def _token(row: ApiTokenRow) -> ApiTokenRecord:
    """Map a token row to metadata without its hash."""
    return ApiTokenRecord(
        token_id=row.id,
        user_id=row.user_id,
        label=row.label,
        created_at=row.created_at,
        revoked_at=row.revoked_at,
    )


class SqlAlchemyUserRepository:
    """User storage bound to the unit of work's session."""

    def __init__(self, session: Session) -> None:
        """Use the shared session; this class never commits."""
        self._session = session

    def get_user(self, user_id: str, *, lock: bool = False) -> UserRecord | None:
        """With lock=True, a concurrent deactivation waits for this transaction."""
        statement = select(UserRow).where(UserRow.user_id == user_id)
        if lock:
            statement = statement.with_for_update(read=True)
        row = self._session.scalars(statement).first()
        return None if row is None else _user(row)

    def add_user(self, user: UserRecord) -> None:
        """Insert a user with sorted roles and units for stable output."""
        insert_rows(
            self._session,
            UserRow(
                user_id=user.user_id,
                display_name=user.display_name,
                roles=sorted(role.value for role in user.roles),
                units=sorted(user.units),
                active=user.active,
            ),
        )

    def set_active(self, user_id: str, active: bool) -> bool:
        """Change the active flag and the update time."""
        result = self._session.execute(
            update(UserRow)
            .where(UserRow.user_id == user_id)
            .values(active=active, updated_at=func.now())
            .execution_options(synchronize_session=False)
        )
        return bool(getattr(result, "rowcount", 0))

    def list_users(self) -> tuple[UserRecord, ...]:
        """Return every user ordered by ID."""
        rows = self._session.scalars(select(UserRow).order_by(UserRow.user_id)).all()
        return tuple(_user(row) for row in rows)

    def add_token(self, token_id: UUID, user_id: str, digest: str, label: str) -> None:
        """Store only the hash of a new token."""
        insert_rows(
            self._session,
            ApiTokenRow(id=token_id, user_id=user_id, token_hash=digest, label=label),
        )

    def find_token(self, digest: str) -> tuple[ApiTokenRecord, UserRecord] | None:
        """Look a token up by hash and return it with its user."""
        row = self._session.execute(
            select(ApiTokenRow, UserRow)
            .join(UserRow, ApiTokenRow.user_id == UserRow.user_id)
            .where(ApiTokenRow.token_hash == digest)
        ).first()
        if row is None:
            return None
        token_row, user_row = row
        return _token(token_row), _user(user_row)

    def revoke_token(self, token_id: UUID) -> bool:
        """Set revoked_at once; a second revocation changes nothing."""
        result = self._session.execute(
            update(ApiTokenRow)
            .where(ApiTokenRow.id == token_id, ApiTokenRow.revoked_at.is_(None))
            .values(revoked_at=func.now())
            .execution_options(synchronize_session=False)
        )
        return bool(getattr(result, "rowcount", 0))
