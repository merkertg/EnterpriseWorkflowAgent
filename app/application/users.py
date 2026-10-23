"""Employee accounts, API tokens and the in-transaction rights check.

Accounts are created by an administrator through the command line; the HTTP
API only authenticates. A token is shown once, when issued, and stored only as
a SHA-256 hash, so a database copy does not reveal usable credentials.
"""

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from uuid import UUID, uuid4

from app.application.access import (
    Permission,
    Principal,
    Role,
    authorize,
    looks_like_token,
    new_token,
    token_hash,
    validate_unit,
)
from app.application.errors import NotFoundError, UnauthenticatedError
from app.application.ports import UnitOfWork, UserRecord
from app.domain.errors import DomainValidationError
from app.domain.models import validate_text


@dataclass(frozen=True, slots=True)
class IssuedToken:
    """A new token: the secret is returned once and never stored."""

    token_id: UUID
    user_id: str
    label: str
    secret: str


def current_principal(
    unit: UnitOfWork,
    actor: Principal | None,
    permission: Permission,
    business_unit: str | None,
) -> Principal | None:
    """Reload an actor inside the transaction and authorize the current rights.

    The share lock on the user row makes a concurrent deactivation wait until
    this command commits. Without an actor (internal tools) nothing is checked.
    """
    if actor is None:
        return None
    if type(actor) is not Principal:
        raise DomainValidationError("invalid_type", "actor", "Use a Principal.")
    record = unit.users.get_user(actor.user_id, lock=True)
    if record is None or not record.active:
        raise UnauthenticatedError("The user is unknown or deactivated.")
    principal = record.principal()
    authorize(principal, permission, business_unit)
    return principal


def _roles(values: Iterable[Role | str]) -> frozenset[Role]:
    """Convert role names, rejecting unknown ones with a stable error."""
    try:
        return frozenset(Role(value) for value in values)
    except ValueError as error:
        known = ", ".join(role.value for role in Role)
        raise DomainValidationError(
            "invalid_roles", "roles", f"Known roles: {known}."
        ) from error


class UserService:
    """Manage employees and tokens; one transaction per call."""

    def __init__(self, unit_of_work: Callable[[], UnitOfWork]) -> None:
        """Receive a factory so every call gets its own transaction."""
        self._unit_of_work = unit_of_work

    def create_user(
        self,
        user_id: str,
        *,
        display_name: str,
        roles: Iterable[Role | str],
        units: Iterable[str],
    ) -> UserRecord:
        """Create an active employee; Principal validation runs first."""
        principal = Principal(
            user_id, _roles(roles), frozenset(validate_unit(u) for u in units)
        )
        record = UserRecord(
            user_id=principal.user_id,
            display_name=validate_text(display_name, "display_name", 200),
            roles=principal.roles,
            units=principal.units,
            active=True,
        )
        with self._unit_of_work() as unit:
            unit.users.add_user(record)
            unit.commit()
        return record

    def issue_token(self, user_id: str, *, label: str) -> IssuedToken:
        """Create a token for an existing user and return its secret once."""
        user_id = validate_text(user_id, "user_id", 64)
        label = validate_text(label, "label", 100)
        secret = new_token()
        token_id = uuid4()
        with self._unit_of_work() as unit:
            if unit.users.get_user(user_id) is None:
                raise NotFoundError("User was not found.")
            unit.users.add_token(token_id, user_id, token_hash(secret), label)
            unit.commit()
        return IssuedToken(token_id, user_id, label, secret)

    def revoke_token(self, token_id: UUID) -> None:
        """Revoke a token; an unknown or already revoked one is not found."""
        if type(token_id) is not UUID:
            raise DomainValidationError("invalid_type", "token_id", "Use a UUID.")
        with self._unit_of_work() as unit:
            if not unit.users.revoke_token(token_id):
                raise NotFoundError("Active token was not found.")
            unit.commit()

    def set_active(self, user_id: str, *, active: bool) -> None:
        """Deactivate or reactivate a user; existing tokens follow the flag."""
        user_id = validate_text(user_id, "user_id", 64)
        if type(active) is not bool:
            raise DomainValidationError("invalid_type", "active", "Use a bool.")
        with self._unit_of_work() as unit:
            if not unit.users.set_active(user_id, active):
                raise NotFoundError("User was not found.")
            unit.commit()

    def list_users(self) -> tuple[UserRecord, ...]:
        """Return every user ordered by ID."""
        with self._unit_of_work() as unit:
            return unit.users.list_users()

    def authenticate(self, token: str) -> Principal:
        """Resolve a bearer token to the user's current rights.

        Every failure gives the same error, so a caller cannot tell a wrong
        token from a revoked one or from a deactivated account.
        """
        if type(token) is not str or not looks_like_token(token):
            raise UnauthenticatedError("A valid API token is required.")
        with self._unit_of_work() as unit:
            found = unit.users.find_token(token_hash(token))
        if found is None:
            raise UnauthenticatedError("A valid API token is required.")
        record, user = found
        if record.revoked_at is not None or not user.active:
            raise UnauthenticatedError("A valid API token is required.")
        return user.principal()
