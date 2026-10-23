"""Request dependencies: application services, the caller and command context.

Authentication resolves a bearer token to the employee's current roles and
units. Authorization itself happens in the application services, inside the
command's transaction, so the API cannot skip it.
"""

from dataclasses import dataclass
from typing import Annotated, cast

from fastapi import Depends, Header, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.api.errors import DatabaseNotConfiguredError
from app.application.access import Principal
from app.application.documents import DocumentService
from app.application.errors import UnauthenticatedError
from app.application.ports import CommandContext
from app.application.reconciliation import ReconciliationService
from app.application.users import UserService
from app.application.workflows import WorkflowService

API_SOURCE = "api"

_bearer = HTTPBearer(
    auto_error=False,
    description="API token from `python -m app.users_cli token USER_ID`.",
)


@dataclass(frozen=True, slots=True)
class Services:
    """Everything the routes call; tests may pass their own instances."""

    workflows: WorkflowService
    documents: DocumentService
    reconciliations: ReconciliationService
    users: UserService
    max_document_bytes: int


def get_services(request: Request) -> Services:
    """Return the app's services or report that no database is configured."""
    services = cast(Services | None, request.app.state.services)
    if services is None:
        raise DatabaseNotConfiguredError(
            "Set DATABASE_URL and restart the API to use business endpoints."
        )
    return services


ServicesDep = Annotated[Services, Depends(get_services)]


def get_principal(
    services: ServicesDep,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> Principal:
    """Authenticate the bearer token; every failure is the same 401."""
    if credentials is None:
        raise UnauthenticatedError("A valid API token is required.")
    return services.users.authenticate(credentials.credentials)


PrincipalDep = Annotated[Principal, Depends(get_principal)]


def command_context(
    principal: PrincipalDep,
    idempotency_key: Annotated[
        str,
        Header(
            alias="Idempotency-Key",
            min_length=1,
            max_length=60,
            pattern=r"^[A-Za-z0-9._:-]+$",
            description="One value per user intent; a retry with it is replayed.",
        ),
    ],
) -> CommandContext:
    """Scope the key to the caller, so users cannot collide with each other."""
    return CommandContext(f"{principal.user_id}:{idempotency_key}", API_SOURCE)


ContextDep = Annotated[CommandContext, Depends(command_context)]
