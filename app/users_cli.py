"""Stage 06 administration: employees and API tokens.

``create`` adds a fictional employee with roles and business units, ``token``
issues a bearer token and prints it once, ``revoke`` revokes a token,
``deactivate``/``activate`` switch an account and ``list`` shows accounts.
All commands need DATABASE_URL and an upgraded schema.
"""

import argparse
import json
import sys
from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import Engine
from sqlalchemy.exc import OperationalError, ProgrammingError

from app.application.access import Role
from app.application.errors import ApplicationError
from app.application.ports import UserRecord
from app.application.users import UserService
from app.config import Settings
from app.domain.errors import DomainValidationError
from app.infrastructure.db.session import build_user_service, create_database_engine


def describe_user(user: UserRecord) -> dict[str, object]:
    """Show an account without any token information."""
    return {
        "user_id": user.user_id,
        "display_name": user.display_name,
        "roles": sorted(role.value for role in user.roles),
        "units": sorted(user.units),
        "active": user.active,
    }


def run_command(service: UserService, arguments: argparse.Namespace) -> object:
    """Execute one parsed command and return JSON-ready output."""
    if arguments.command == "create":
        return describe_user(
            service.create_user(
                arguments.user_id,
                display_name=arguments.name,
                roles=arguments.role,
                units=arguments.unit,
            )
        )
    if arguments.command == "token":
        issued = service.issue_token(arguments.user_id, label=arguments.label)
        return {
            "token_id": str(issued.token_id),
            "user_id": issued.user_id,
            "label": issued.label,
            "token": issued.secret,
            "note": "Shown once; only its SHA-256 hash is stored.",
        }
    if arguments.command == "revoke":
        service.revoke_token(arguments.token_id)
        return {"token_id": str(arguments.token_id), "revoked": True}
    if arguments.command in ("deactivate", "activate"):
        active = arguments.command == "activate"
        service.set_active(arguments.user_id, active=active)
        return {"user_id": arguments.user_id, "active": active}
    return [describe_user(user) for user in service.list_users()]


def _parser() -> argparse.ArgumentParser:
    """Describe the commands and their arguments."""
    parser = argparse.ArgumentParser(
        prog="python -m app.users_cli",
        description="Manage fictional employees and API tokens (Stage 06).",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    create = commands.add_parser("create", help="add an employee")
    create.add_argument("user_id")
    create.add_argument("--name", required=True, help="display name")
    create.add_argument(
        "--role",
        action="append",
        required=True,
        choices=[role.value for role in Role],
        help="repeat for several roles",
    )
    create.add_argument(
        "--unit", action="append", required=True, help="repeat for several units"
    )
    token = commands.add_parser("token", help="issue a bearer token")
    token.add_argument("user_id")
    token.add_argument("--label", default="cli", help="where the token is used")
    revoke = commands.add_parser("revoke", help="revoke a token by its ID")
    revoke.add_argument("token_id", type=UUID)
    for name in ("deactivate", "activate"):
        commands.add_parser(name, help=f"{name} an employee").add_argument("user_id")
    commands.add_parser("list", help="list employees")
    return parser


def _engine() -> Engine | None:
    """Create an engine from DATABASE_URL, or report why it cannot."""
    settings = Settings()
    if settings.database_url is None:
        print("Set DATABASE_URL to manage users.", file=sys.stderr)
        return None
    try:
        return create_database_engine(settings.database_url.get_secret_value())
    except ValueError as error:
        print(str(error), file=sys.stderr)
        return None


def main(argv: Sequence[str] | None = None) -> int:
    """Parse a command, run it and print JSON or a one-line error."""
    arguments = _parser().parse_args(argv)
    engine = _engine()
    if engine is None:
        return 2
    try:
        output = run_command(build_user_service(engine), arguments)
    except (ApplicationError, DomainValidationError) as error:
        print(f"{error.code}: {error}", file=sys.stderr)
        return 1
    except OperationalError:
        print("Cannot connect to the database in DATABASE_URL.", file=sys.stderr)
        return 1
    except ProgrammingError:
        print("Schema is missing: run `alembic upgrade head` first.", file=sys.stderr)
        return 1
    finally:
        engine.dispose()
    print(json.dumps(output, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
