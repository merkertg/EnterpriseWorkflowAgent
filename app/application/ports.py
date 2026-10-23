"""Records and persistence ports used by workflow use cases.

The application layer depends on these protocols, not on SQLAlchemy. The
PostgreSQL adapter in ``app.infrastructure.db`` implements them.
"""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from types import TracebackType
from typing import Protocol, Self
from uuid import UUID

from app.domain.errors import DomainValidationError
from app.domain.models import Invoice, PurchaseOrder, validate_text
from app.domain.rules import ValidationPolicy, ValidationResult
from app.domain.workflow import Workflow, WorkflowState


class CommandName(StrEnum):
    """Audited workflow commands; each one changes state in a single transaction."""

    START = "start"
    CHECK = "check"
    APPROVE = "approve"
    REJECT = "reject"
    REVISE = "revise"


@dataclass(frozen=True, slots=True)
class CommandContext:
    """Caller-declared request metadata; this is not authentication."""

    idempotency_key: str
    source: str

    def __post_init__(self) -> None:
        """Require a bounded key per user intent and a short origin label."""
        object.__setattr__(
            self,
            "idempotency_key",
            validate_text(self.idempotency_key, "idempotency_key"),
        )
        object.__setattr__(self, "source", validate_text(self.source, "source", 64))


@dataclass(frozen=True, slots=True)
class VersionRef:
    """Identify one immutable stored input by ID, business key, number and hash."""

    version_id: UUID
    business_id: str
    version_no: int
    content_hash: str


@dataclass(frozen=True, slots=True)
class Versioned[T]:
    """Pair an immutable domain value with the stored version that holds it."""

    ref: VersionRef
    value: T


@dataclass(frozen=True, slots=True)
class StoredWorkflow:
    """A domain workflow plus its optimistic-lock version and pinned inputs."""

    workflow: Workflow
    version: int
    invoice_version: VersionRef
    purchase_order_version: VersionRef | None
    policy_version: VersionRef
    last_snapshot_id: UUID | None


@dataclass(frozen=True, slots=True)
class ValidationSnapshot:
    """An immutable check result tied to exact input version IDs and hashes."""

    snapshot_id: UUID
    workflow_id: str
    workflow_version: int
    invoice_version: VersionRef
    purchase_order_version: VersionRef | None
    policy_version: VersionRef
    result: ValidationResult


@dataclass(frozen=True, slots=True)
class CommandResult:
    """The outcome of one command; a replay returns the original outcome."""

    workflow_id: str
    command: CommandName
    state: WorkflowState
    version: int
    snapshot_id: UUID | None
    replayed: bool = False


@dataclass(frozen=True, slots=True)
class IdempotencyRecord:
    """Remember a committed command result by key and request fingerprint."""

    key: str
    request_hash: str
    result: CommandResult


@dataclass(frozen=True, slots=True)
class AuditEvent:
    """One state change with its inputs and origin; it never holds document text."""

    workflow_id: str
    command: CommandName
    previous_state: WorkflowState | None
    new_state: WorkflowState
    workflow_version: int
    invoice_version_id: UUID
    purchase_order_version_id: UUID | None
    policy_version_id: UUID
    snapshot_id: UUID | None
    issue_codes: tuple[str, ...]
    actor_id: str | None
    source: str
    idempotency_key: str
    # Assigned by the database when the event is stored.
    event_id: int | None = None
    occurred_at: datetime | None = None


class WorkflowRepository(Protocol):
    """Read and write workflow data inside the caller's current transaction."""

    def latest_invoice_version(self, invoice_id: str) -> VersionRef | None:
        """Return the highest stored version number for an invoice ID."""
        ...

    def latest_purchase_order_version(
        self, purchase_order_id: str
    ) -> VersionRef | None:
        """Return the highest stored version number for a purchase order ID."""
        ...

    def latest_policy_version(self, policy_id: str) -> VersionRef | None:
        """Return the highest stored version number for a policy ID."""
        ...

    def add_invoice_version(self, version: Versioned[Invoice]) -> None:
        """Insert an invoice version; a number race raises ConcurrentWriteError."""
        ...

    def add_purchase_order_version(self, version: Versioned[PurchaseOrder]) -> None:
        """Insert a purchase order version with its typed lines."""
        ...

    def add_policy_version(self, version: Versioned[ValidationPolicy]) -> None:
        """Insert a validation policy version."""
        ...

    def get_invoice_version(self, version_id: UUID) -> Versioned[Invoice] | None:
        """Load and hash-verify one invoice version."""
        ...

    def get_purchase_order_version(
        self, version_id: UUID
    ) -> Versioned[PurchaseOrder] | None:
        """Load and hash-verify one purchase order version."""
        ...

    def get_policy_version(
        self, version_id: UUID
    ) -> Versioned[ValidationPolicy] | None:
        """Load and hash-verify one policy version."""
        ...

    def get_workflow(self, workflow_id: str) -> StoredWorkflow | None:
        """Rebuild a workflow from its pinned versions and latest snapshot."""
        ...

    def add_workflow(self, stored: StoredWorkflow) -> None:
        """Insert a new workflow; a duplicate ID raises WorkflowAlreadyExistsError."""
        ...

    def update_workflow(self, stored: StoredWorkflow, expected_version: int) -> None:
        """Update only if the stored version still equals expected_version."""
        ...

    def add_snapshot(self, snapshot: ValidationSnapshot) -> None:
        """Insert an immutable validation snapshot."""
        ...

    def get_snapshot(self, snapshot_id: UUID) -> ValidationSnapshot | None:
        """Load a snapshot with the exact inputs it references."""
        ...

    def add_audit_event(self, event: AuditEvent) -> None:
        """Append an audit event to the current transaction."""
        ...

    def list_audit_events(self, workflow_id: str) -> tuple[AuditEvent, ...]:
        """Return a workflow's audit events in insertion order."""
        ...

    def get_idempotency_record(self, key: str) -> IdempotencyRecord | None:
        """Return a committed or own-transaction result for an idempotency key."""
        ...

    def add_idempotency_record(self, record: IdempotencyRecord) -> None:
        """Claim a key immediately; a concurrent winner raises a claimed error."""
        ...


class UnitOfWork(Protocol):
    """One transaction: commit explicitly, otherwise leaving the block rolls back."""

    @property
    def repository(self) -> WorkflowRepository:
        """Return the repository bound to this transaction."""
        ...

    def __enter__(self) -> Self:
        """Open a transaction."""
        ...

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Roll back anything uncommitted and release the connection."""
        ...

    def commit(self) -> None:
        """Make every change in this unit visible atomically."""
        ...


def require_positive_version(value: int, field: str = "expected_version") -> int:
    """Accept only a real positive int, so True or "1" cannot pass as a version."""
    if type(value) is not int or value < 1:
        raise DomainValidationError(
            "invalid_version", field, f"{field} must be a positive integer."
        )
    return value
