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
