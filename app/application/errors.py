"""Stable application errors that adapters can map without importing a database."""


class ApplicationError(Exception):
    """Carry a machine-readable code alongside a short human explanation."""

    code = "application_error"

    def __init__(self, message: str) -> None:
        """Keep the message free of document text, credentials and connection URLs."""
        super().__init__(message)


class NotFoundError(ApplicationError):
    """The requested workflow, input version or snapshot does not exist."""

    code = "not_found"


class WorkflowAlreadyExistsError(ApplicationError):
    """A start command reused an existing workflow ID."""

    code = "workflow_exists"


class VersionConflictError(ApplicationError):
    """The caller's expected workflow version is no longer current."""

    code = "version_conflict"


class IdempotencyKeyReusedError(ApplicationError):
    """One idempotency key was sent again with a different command or arguments."""

    code = "idempotency_key_reused"


class StoredDataIntegrityError(ApplicationError):
    """Stored content no longer matches its recorded hash or references."""

    code = "stored_data_integrity"


class ConcurrentWriteError(ApplicationError):
    """A concurrent writer won a uniqueness race; the use case may retry once."""

    code = "concurrent_write"


class IdempotencyKeyClaimedError(ConcurrentWriteError):
    """Another transaction committed the same idempotency key first."""

    code = "idempotency_key_claimed"


class UnauthenticatedError(ApplicationError):
    """No valid, unrevoked token for an active user was presented."""

    code = "unauthenticated"


class MissingPermissionError(ApplicationError):
    """The user is in scope but none of their roles grants this action."""

    code = "missing_permission"


class SelfApprovalError(ApplicationError):
    """The employee who started a workflow may not approve it (four eyes)."""

    code = "self_approval_forbidden"


class UserExistsError(ApplicationError):
    """A user with this ID already exists."""

    code = "user_exists"


class ReconciliationNotForWorkflowError(ApplicationError):
    """The cited reconciliation snapshot belongs to another workflow."""

    code = "reconciliation_not_for_workflow"


class ReconciliationNotMatchedError(ApplicationError):
    """Approval needs a snapshot without discrepancies."""

    code = "reconciliation_not_matched"


class StaleReconciliationError(ApplicationError):
    """The snapshot no longer describes the applicable inputs."""

    code = "stale_reconciliation"

    def __init__(self, message: str, reasons: tuple[str, ...]) -> None:
        """Keep the machine-readable stale reasons for clients."""
        self.reasons = reasons
        super().__init__(message)
