"""Who may do what: roles, permissions, business units and API tokens.

Checks live here, in the application layer, so the HTTP API and any other
caller enforce the same rules. A user outside a workflow's business unit gets
"not found", so the existence of other units' work is not revealed.
"""

import hashlib
import re
import secrets
from dataclasses import dataclass
from enum import StrEnum

from app.application.errors import MissingPermissionError, NotFoundError
from app.domain.errors import DomainValidationError
from app.domain.models import validate_text

TOKEN_PREFIX = "ewa_"
_TOKEN_SHAPE = re.compile(r"ewa_[A-Za-z0-9_-]{43}")


class Role(StrEnum):
    """Job functions; one user may hold several."""

    VIEWER = "viewer"
    CLERK = "clerk"
    APPROVER = "approver"


class Permission(StrEnum):
    """Actions the application protects."""

    VIEW = "view"
    SUBMIT = "submit"
    RECONCILE = "reconcile"
    DECIDE = "decide"


ROLE_PERMISSIONS: dict[Role, frozenset[Permission]] = {
    Role.VIEWER: frozenset({Permission.VIEW}),
    Role.CLERK: frozenset({Permission.VIEW, Permission.SUBMIT, Permission.RECONCILE}),
    Role.APPROVER: frozenset(
        {Permission.VIEW, Permission.RECONCILE, Permission.DECIDE}
    ),
}


def validate_unit(value: str) -> str:
    """Business units are short identifiers such as DEMO-UNIT-A."""
    return validate_text(value, "business_unit", 64)


@dataclass(frozen=True, slots=True)
class Principal:
    """An authenticated employee as stored now: roles and units."""

    user_id: str
    roles: frozenset[Role]
    units: frozenset[str]

    def __post_init__(self) -> None:
        """Require an ID, at least one known role and at least one unit."""
        object.__setattr__(self, "user_id", validate_text(self.user_id, "user_id", 64))
        if (
            type(self.roles) is not frozenset
            or not self.roles
            or any(type(role) is not Role for role in self.roles)
        ):
            raise DomainValidationError("invalid_roles", "roles", "Use known roles.")
        if type(self.units) is not frozenset or not self.units:
            raise DomainValidationError("invalid_units", "units", "Use units.")
        object.__setattr__(
            self, "units", frozenset(validate_unit(unit) for unit in self.units)
        )

    @property
    def permissions(self) -> frozenset[Permission]:
        """Union of the permissions of every role the user holds."""
        return frozenset().union(*(ROLE_PERMISSIONS[role] for role in self.roles))


def authorize(principal: Principal, permission: Permission, unit: str | None) -> None:
    """Check the unit first, so another unit's work looks like it does not exist."""
    if unit is not None and unit not in principal.units:
        raise NotFoundError("Workflow was not found.")
    if permission not in principal.permissions:
        raise MissingPermissionError(f"Your roles do not allow '{permission.value}'.")


def new_token() -> str:
    """Create a random bearer token; only its hash is ever stored."""
    return TOKEN_PREFIX + secrets.token_urlsafe(32)


def token_hash(token: str) -> str:
    """SHA-256 of a token; a malformed token cannot match any stored hash."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def looks_like_token(token: str) -> bool:
    """Reject obviously wrong input before touching the database."""
    return bool(_TOKEN_SHAPE.fullmatch(token))
