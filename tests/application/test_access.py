"""Roles, units and tokens: pure checks that need no database."""

import re

import pytest

from app.application.access import (
    Permission,
    Principal,
    Role,
    authorize,
    looks_like_token,
    new_token,
    token_hash,
)
from app.application.errors import (
    MissingPermissionError,
    NotFoundError,
    UnauthenticatedError,
)
from app.application.ports import UnitOfWork
from app.application.users import UserService
from app.domain.errors import DomainValidationError
from app.users_cli import main as users_main


def _never_opened() -> UnitOfWork:
    """Fail loudly if a use case reaches persistence with invalid input."""
    raise AssertionError("A transaction must not be opened for invalid input.")


def _principal(
    *roles: Role, units: frozenset[str] = frozenset({"UNIT-A"})
) -> Principal:
    """Build an in-memory principal for one or more roles."""
    return Principal("user-1", frozenset(roles), units)


@pytest.mark.parametrize(
    ("role", "allowed"),
    [
        (Role.VIEWER, {Permission.VIEW}),
        (
            Role.CLERK,
            {
                Permission.VIEW,
                Permission.SUBMIT,
                Permission.RECONCILE,
                Permission.ASSIST,
            },
        ),
        (
            Role.APPROVER,
            {
                Permission.VIEW,
                Permission.RECONCILE,
                Permission.DECIDE,
                Permission.ASSIST,
            },
        ),
    ],
)
def test_role_permission_matrix(role: Role, allowed: set[Permission]) -> None:
    """Only approvers decide; approvers do not submit documents or workflows."""
    principal = _principal(role)
    assert principal.permissions == allowed
    for permission in Permission:
        if permission in allowed:
            authorize(principal, permission, "UNIT-A")
        else:
            with pytest.raises(MissingPermissionError):
                authorize(principal, permission, "UNIT-A")


def test_several_roles_combine_their_permissions() -> None:
    """A team lead with clerk and approver roles has all four permissions."""
    assert _principal(Role.CLERK, Role.APPROVER).permissions == set(Permission)


def test_other_unit_looks_like_a_missing_workflow() -> None:
    """The unit is checked first, so even a viewer learns nothing about it."""
    viewer = _principal(Role.VIEWER)
    with pytest.raises(NotFoundError):
        authorize(viewer, Permission.DECIDE, "UNIT-B")
    with pytest.raises(NotFoundError):
        authorize(_principal(Role.APPROVER), Permission.DECIDE, "UNIT-B")
    # Without a unit (shared reference data) only the permission is checked.
    authorize(viewer, Permission.VIEW, None)


@pytest.mark.parametrize(
    ("user_id", "roles", "units"),
    [
        (" ", frozenset({Role.VIEWER}), frozenset({"UNIT-A"})),
        ("user-1", frozenset(), frozenset({"UNIT-A"})),
        ("user-1", frozenset({"admin"}), frozenset({"UNIT-A"})),
        ("user-1", frozenset({Role.VIEWER}), frozenset()),
        ("user-1", frozenset({Role.VIEWER}), frozenset({" "})),
        ("user-1", {Role.VIEWER}, frozenset({"UNIT-A"})),
    ],
)
def test_principal_requires_id_known_roles_and_units(
    user_id: str, roles: object, units: object
) -> None:
    """An account without a role or a unit could never be authorized."""
    with pytest.raises(DomainValidationError):
        Principal(user_id, roles, units)  # type: ignore[arg-type]


def test_tokens_are_random_prefixed_and_hashed() -> None:
    """Tokens are 256-bit random values; only a 64-hex SHA-256 is stored."""
    first, second = new_token(), new_token()
    assert first != second
    assert first.startswith("ewa_") and looks_like_token(first)
    digest = token_hash(first)
    assert re.fullmatch(r"[0-9a-f]{64}", digest)
    assert token_hash(first) == digest != token_hash(second)


@pytest.mark.parametrize(
    "token",
    [
        "",
        "ewa_",
        "ewa_short",
        "abc_" + "a" * 43,
        "ewa_" + "a" * 42 + " ",
        "ewa_" + "a" * 44,
    ],
)
def test_malformed_tokens_are_refused_without_a_database(token: str) -> None:
    """A wrong shape fails before any lookup and with the generic message."""
    assert not looks_like_token(token)
    with pytest.raises(UnauthenticatedError) as error:
        UserService(_never_opened).authenticate(token)
    assert str(error.value) == "A valid API token is required."


@pytest.mark.parametrize(
    "call",
    [
        lambda s: s.create_user("u-1", display_name="U", roles=["admin"], units=["A"]),
        lambda s: s.create_user("u-1", display_name="U", roles=["viewer"], units=[]),
        lambda s: s.create_user("u-1", display_name=" ", roles=["viewer"], units=["A"]),
        lambda s: s.issue_token("u-1", label=""),
        lambda s: s.revoke_token("not-a-uuid"),
        lambda s: s.set_active("u-1", active="no"),
    ],
)
def test_user_commands_validate_before_a_transaction(call: object) -> None:
    """Invalid roles, units, names, labels and flags never reach storage."""
    with pytest.raises(DomainValidationError):
        call(UserService(_never_opened))  # type: ignore[operator]


def test_users_cli_needs_a_database(capsys: pytest.CaptureFixture[str]) -> None:
    """Without DATABASE_URL the CLI exits with code 2 and explains why."""
    assert users_main(["list"]) == 2
    assert "DATABASE_URL" in capsys.readouterr().err


def test_decisions_demo_needs_a_database(capsys: pytest.CaptureFixture[str]) -> None:
    """The Stage 06 demo also refuses to run without DATABASE_URL."""
    from app.decisions_demo import main as decisions_main

    assert decisions_main([]) == 2
    assert "DATABASE_URL" in capsys.readouterr().err


def test_agent_demo_needs_a_database(capsys: pytest.CaptureFixture[str]) -> None:
    """The Stage 07 demo refuses to run without DATABASE_URL."""
    from app.agent_demo import main as agent_main

    assert agent_main([]) == 2
    assert "DATABASE_URL" in capsys.readouterr().err


def test_ui_demo_setup_needs_a_database(capsys: pytest.CaptureFixture[str]) -> None:
    """The interface setup refuses to run without DATABASE_URL."""
    from app.ui_demo_setup import main as setup_main

    assert setup_main([]) == 2
    assert "DATABASE_URL" in capsys.readouterr().err
