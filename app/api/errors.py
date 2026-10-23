"""Map application and domain errors to HTTP responses with stable codes.

Every error body has the same shape: ``{"error": {"code", "message", ...}}``.
Messages come from the application and never contain document text, tokens or
connection URLs. Validation errors list locations, not the submitted values.
"""

from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.application.errors import (
    ApplicationError,
    ConcurrentWriteError,
    IdempotencyKeyReusedError,
    MissingPermissionError,
    NotFoundError,
    ReconciliationNotForWorkflowError,
    ReconciliationNotMatchedError,
    SelfApprovalError,
    StaleReconciliationError,
    StoredDataIntegrityError,
    UnauthenticatedError,
    UserExistsError,
    VersionConflictError,
    WorkflowAlreadyExistsError,
)
from app.domain.documents import DocumentRejectedError, RejectionCode
from app.domain.errors import DomainValidationError


class DatabaseNotConfiguredError(ApplicationError):
    """The API was started without DATABASE_URL; only /health works."""

    code = "database_not_configured"


class DocumentTooLargeError(ApplicationError):
    """The request body exceeded the document limit while it was being read."""

    code = "too_large"


# Order matters: subclasses are listed before their bases.
ERROR_STATUS: tuple[tuple[type[ApplicationError], int], ...] = (
    (UnauthenticatedError, 401),
    (MissingPermissionError, 403),
    (SelfApprovalError, 403),
    (NotFoundError, 404),
    (WorkflowAlreadyExistsError, 409),
    (VersionConflictError, 409),
    (IdempotencyKeyReusedError, 409),
    (ConcurrentWriteError, 409),
    (UserExistsError, 409),
    (StaleReconciliationError, 409),
    (ReconciliationNotMatchedError, 409),
    (ReconciliationNotForWorkflowError, 422),
    (DocumentTooLargeError, 413),
    (DatabaseNotConfiguredError, 503),
    (StoredDataIntegrityError, 500),
)
# Domain codes that describe the workflow's current state, not malformed input.
_STATE_CODES = frozenset({"invalid_transition", "stale_validation"})


def _body(code: str, message: str, **extra: Any) -> dict[str, Any]:
    """Build the common error body."""
    return {"error": {"code": code, "message": message, **extra}}


def application_error_response(error: ApplicationError) -> JSONResponse:
    """Choose the status by error class; unknown subclasses become 400."""
    status = next(
        (code for kind, code in ERROR_STATUS if isinstance(error, kind)),
        400,
    )
    extra: dict[str, Any] = {}
    if isinstance(error, StaleReconciliationError):
        extra["reasons"] = list(error.reasons)
    headers = {"WWW-Authenticate": "Bearer"} if status == 401 else None
    return JSONResponse(
        _body(error.code, str(error), **extra), status_code=status, headers=headers
    )


def install_error_handlers(application: FastAPI) -> None:
    """Register handlers so routes can let typed errors propagate."""

    @application.exception_handler(ApplicationError)
    async def on_application_error(
        _request: Request, error: ApplicationError
    ) -> JSONResponse:
        """Translate an application error by its class."""
        return application_error_response(error)

    @application.exception_handler(DomainValidationError)
    async def on_domain_error(
        _request: Request, error: DomainValidationError
    ) -> JSONResponse:
        """A forbidden transition is a conflict; other domain errors are 422."""
        status = 409 if error.code in _STATE_CODES else 422
        return JSONResponse(
            _body(error.code, str(error), field=error.field), status_code=status
        )

    @application.exception_handler(DocumentRejectedError)
    async def on_document_rejected(
        _request: Request, error: DocumentRejectedError
    ) -> JSONResponse:
        """Refused documents keep their intake code; size has its own status."""
        status = 413 if error.code is RejectionCode.TOO_LARGE else 422
        return JSONResponse(_body(error.code.value, str(error)), status_code=status)

    @application.exception_handler(RequestValidationError)
    async def on_request_invalid(
        _request: Request, error: RequestValidationError
    ) -> JSONResponse:
        """Report where the request is wrong without echoing submitted values."""
        problems = [
            {"location": [str(part) for part in item["loc"]], "message": item["msg"]}
            for item in error.errors()
        ]
        return JSONResponse(
            _body("invalid_request", "The request is invalid.", problems=problems),
            status_code=422,
        )
