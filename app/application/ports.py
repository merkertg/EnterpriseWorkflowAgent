"""Records and persistence ports used by workflow use cases.

The application layer depends on these protocols, not on SQLAlchemy. The
PostgreSQL adapter in ``app.infrastructure.db`` implements them.
"""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from types import TracebackType
from typing import Any, Protocol, Self
from uuid import UUID

from app.application.access import Principal, Role
from app.domain.documents import DocumentLimits, MediaType
from app.domain.errors import DomainValidationError
from app.domain.extraction import (
    ExtractionProblem,
    ExtractionResult,
    ExtractionStatus,
)
from app.domain.models import Invoice, PurchaseOrder, validate_text
from app.domain.reconciliation import ReconciliationResult, ReconciliationRules
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
    business_unit: str
    created_by: str | None = None
    decision_reconciliation_id: UUID | None = None


@dataclass(frozen=True, slots=True)
class WorkflowSummary:
    """A workflow row for lists: no inputs are loaded or re-hashed."""

    workflow_id: str
    invoice_id: str
    state: WorkflowState
    version: int
    business_unit: str
    created_by: str | None
    updated_at: datetime | None


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
    reconciliation_snapshot_id: UUID | None = None
    outbox_event_id: UUID | None = None
    # Assigned by the database when the event is stored.
    event_id: int | None = None
    occurred_at: datetime | None = None


class OutboxEventType(StrEnum):
    """Facts announced to a later process step; nothing here pays or sends."""

    INVOICE_APPROVED = "invoice.approved"
    INVOICE_REJECTED = "invoice.rejected"


@dataclass(frozen=True, slots=True)
class OutboxEvent:
    """An event stored with the decision; the outbox worker delivers it later."""

    event_id: UUID
    event_type: OutboxEventType
    workflow_id: str
    workflow_version: int
    payload: dict[str, Any]
    created_at: datetime | None = None


class DeliveryStatus(StrEnum):
    """Where an outbox event is on its way to the sandbox receiver."""

    PENDING = "pending"
    RETRY_SCHEDULED = "retry_scheduled"
    DELIVERED = "delivered"
    EXHAUSTED = "exhausted"


class AttemptOutcome(StrEnum):
    """Result of one delivery attempt."""

    DELIVERED = "delivered"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class DeliveryAttempt:
    """One logged attempt: status code or error code, timing, duplicate flag."""

    seq: int
    started_at: datetime
    duration_ms: int
    outcome: AttemptOutcome
    http_status: int | None
    error_code: str | None
    duplicate: bool | None


@dataclass(frozen=True, slots=True)
class OutboxDelivery:
    """Mutable delivery state of one event.

    ``attempts`` counts the current round and decides exhaustion;
    ``total_attempts`` counts every attempt, including rounds before a requeue.
    """

    event_id: UUID
    status: DeliveryStatus
    attempts: int
    total_attempts: int
    requeues: int
    next_attempt_at: datetime
    last_error: str | None
    last_attempt_at: datetime | None
    delivered_at: datetime | None


@dataclass(frozen=True, slots=True)
class OutboxEntry:
    """An event with its delivery state and attempt log, for reading."""

    event: OutboxEvent
    delivery: OutboxDelivery
    attempts: tuple[DeliveryAttempt, ...]


@dataclass(frozen=True, slots=True)
class UserRecord:
    """An employee account; the token is checked elsewhere."""

    user_id: str
    display_name: str
    roles: frozenset[Role]
    units: frozenset[str]
    active: bool

    def principal(self) -> Principal:
        """Return the current rights of this user."""
        return Principal(self.user_id, self.roles, self.units)


@dataclass(frozen=True, slots=True)
class ApiTokenRecord:
    """Token metadata; the secret itself is never stored."""

    token_id: UUID
    user_id: str
    label: str
    created_at: datetime | None
    revoked_at: datetime | None


class UserRepository(Protocol):
    """Store employees and hashed API tokens in the transaction."""

    def get_user(self, user_id: str, *, lock: bool = False) -> UserRecord | None:
        """Return a user; lock=True holds a share lock until commit."""
        ...

    def add_user(self, user: UserRecord) -> None:
        """Insert a user; a duplicate ID raises UserExistsError."""
        ...

    def set_active(self, user_id: str, active: bool) -> bool:
        """Activate or deactivate a user; return False if unknown."""
        ...

    def list_users(self) -> tuple[UserRecord, ...]:
        """Return all users ordered by ID."""
        ...

    def add_token(self, token_id: UUID, user_id: str, digest: str, label: str) -> None:
        """Store a token hash for a user."""
        ...

    def find_token(self, digest: str) -> tuple[ApiTokenRecord, UserRecord] | None:
        """Return a token and its user by hash, revoked or not."""
        ...

    def revoke_token(self, token_id: UUID) -> bool:
        """Mark a token revoked; return False if unknown or already revoked."""
        ...


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

    def list_workflows(
        self,
        units: frozenset[str] | None,
        state: WorkflowState | None,
        limit: int,
    ) -> tuple[WorkflowSummary, ...]:
        """Return the most recently changed workflows, optionally filtered."""
        ...

    def get_idempotency_record(self, key: str) -> IdempotencyRecord | None:
        """Return a committed or own-transaction result for an idempotency key."""
        ...

    def add_idempotency_record(self, record: IdempotencyRecord) -> None:
        """Claim a key immediately; a concurrent winner raises a claimed error."""
        ...

    def lock_keys(self, keys: tuple[str, ...]) -> None:
        """Hold transaction-scoped locks on business keys, in sorted order."""
        ...

    def add_outbox_event(self, event: OutboxEvent) -> None:
        """Append an outbox event and its pending delivery in this transaction."""
        ...


@dataclass(frozen=True, slots=True)
class StoredDocument:
    """Metadata of an accepted document; the bytes are stored separately."""

    document_id: UUID
    content_hash: str
    media_type: MediaType
    size_bytes: int
    page_count: int
    declared_name: str | None


@dataclass(frozen=True, slots=True)
class StoredExtraction:
    """One extractor's fields for a document and the decision made about them."""

    extraction_id: UUID
    document_id: UUID
    result: ExtractionResult
    status: ExtractionStatus
    problems: tuple[ExtractionProblem, ...]
    invoice_version: VersionRef | None


class TextExtractor(Protocol):
    """Turn accepted bytes into page texts within limits, or reject them."""

    def extract_pages(
        self, content: bytes, media_type: MediaType, limits: DocumentLimits
    ) -> tuple[str, ...]:
        """Return one normalized text per page or raise DocumentRejectedError."""
        ...


class FieldExtractor(Protocol):
    """Propose invoice fields with evidence; a model provider can implement it."""

    @property
    def name(self) -> str:
        """Return a stable name and version, for example labeled-text-parser/1."""
        ...

    def extract(self, pages: tuple[str, ...]) -> ExtractionResult:
        """Propose fields; the domain verifies and assesses them afterwards."""
        ...


class DocumentRepository(Protocol):
    """Store documents, their page texts and extractions in the transaction."""

    def find_document_by_hash(self, content_hash: str) -> StoredDocument | None:
        """Return an already stored document with identical bytes."""
        ...

    def get_document(self, document_id: UUID) -> StoredDocument | None:
        """Return document metadata by server-generated ID."""
        ...

    def add_document(
        self, document: StoredDocument, content: bytes, pages: tuple[str, ...]
    ) -> None:
        """Insert bytes and page texts; a hash race raises ConcurrentWriteError."""
        ...

    def get_content(self, document_id: UUID) -> bytes | None:
        """Return the stored original bytes."""
        ...

    def get_pages(self, document_id: UUID) -> tuple[str, ...]:
        """Return stored page texts in page order; evidence refers to these."""
        ...

    def find_extraction(
        self, document_id: UUID, extractor: str
    ) -> StoredExtraction | None:
        """Return the extraction one extractor already made for a document."""
        ...

    def latest_extraction(self, document_id: UUID) -> StoredExtraction | None:
        """Return the most recently stored extraction for a document."""
        ...

    def add_extraction(self, extraction: StoredExtraction) -> None:
        """Insert an extraction with its fields and problems."""
        ...

    def get_extraction(self, extraction_id: UUID) -> StoredExtraction | None:
        """Return one stored extraction by its ID."""
        ...

    def first_extraction_for_invoice(
        self, invoice_version_id: UUID
    ) -> StoredExtraction | None:
        """Return the earliest complete extraction that produced an invoice."""
        ...


@dataclass(frozen=True, slots=True)
class ReconciliationInputs:
    """Exactly which versions and hashes a reconciliation was computed from."""

    invoice_version: VersionRef
    purchase_order_version: VersionRef | None
    rules_version: VersionRef
    source_document_id: UUID | None
    document_content_hash: str | None
    extraction_id: UUID | None
    fields_hash: str | None
    duplicate_workflow_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class StoredReconciliation:
    """An immutable reconciliation snapshot tied to a workflow version."""

    snapshot_id: UUID
    workflow_id: str
    workflow_version: int
    engine: str
    inputs_hash: str
    inputs: ReconciliationInputs
    result: ReconciliationResult
    created_at: datetime | None = None


class ReconciliationRepository(Protocol):
    """Store rule versions and reconciliation snapshots in the transaction."""

    def latest_rules_version(self, rules_id: str) -> VersionRef | None:
        """Return the highest stored version of a rule set."""
        ...

    def add_rules_version(self, version: Versioned[ReconciliationRules]) -> None:
        """Insert a rule set with its currency rules."""
        ...

    def get_rules_version(
        self, version_id: UUID
    ) -> Versioned[ReconciliationRules] | None:
        """Load and hash-verify one rule set version."""
        ...

    def duplicate_workflow_ids(
        self, supplier_id: str, invoice_id: str, exclude_workflow_id: str
    ) -> tuple[str, ...]:
        """Return other non-rejected workflows for the same supplier invoice."""
        ...

    def find_snapshot(
        self, workflow_id: str, inputs_hash: str
    ) -> StoredReconciliation | None:
        """Return a snapshot already computed from identical inputs."""
        ...

    def add_snapshot(self, snapshot: StoredReconciliation) -> None:
        """Insert a snapshot; an identical concurrent one raises a race error."""
        ...

    def get_snapshot(self, snapshot_id: UUID) -> StoredReconciliation | None:
        """Load a snapshot as it was stored."""
        ...

    def latest_snapshot(self, workflow_id: str) -> StoredReconciliation | None:
        """Return the most recent snapshot of a workflow."""
        ...


class AgentStopReason(StrEnum):
    """Why an agent run ended; only final_answer means the model finished."""

    FINAL_ANSWER = "final_answer"
    TOOL_CALL_LIMIT = "tool_call_limit"
    PROVIDER_CALL_LIMIT = "provider_call_limit"
    TOO_MANY_REFUSALS = "too_many_refusals"
    PROVIDER_TIMEOUT = "provider_timeout"
    PROVIDER_ERROR = "provider_error"
    PROVIDER_REFUSAL = "provider_refusal"
    TOOL_TIMEOUT = "tool_timeout"
    INVALID_FINAL_ANSWER = "invalid_final_answer"


class AgentStepKind(StrEnum):
    """One entry of an agent trace."""

    PROVIDER_CALL = "provider_call"
    TOOL_CALL = "tool_call"
    REFUSED_CALL = "refused_call"
    FINAL_ANSWER = "final_answer"
    STOP = "stop"


class NextStep(StrEnum):
    """Drafts the agent may suggest; none of them changes the workflow."""

    READY_FOR_APPROVAL = "ready_for_approval"
    REQUEST_NEW_RECONCILIATION = "request_new_reconciliation"
    RECOMMEND_REJECTION = "recommend_rejection"
    NEEDS_HUMAN_REVIEW = "needs_human_review"


@dataclass(frozen=True, slots=True)
class AgentStep:
    """A trace entry: names, codes, sizes and hashes, never document text."""

    seq: int
    kind: AgentStepKind
    tool_name: str | None = None
    arguments_sha256: str | None = None
    result_sha256: str | None = None
    result_bytes: int | None = None
    error_code: str | None = None
    duration_ms: int = 0


@dataclass(frozen=True, slots=True)
class AgentRunRecord:
    """A finished run: limits used, outcome, policy override and trace.

    The explanation itself is not stored: it may quote document text. Only
    its SHA-256 and length are kept, so a shown text can be matched later.
    """

    run_id: UUID
    workflow_id: str
    actor_id: str
    provider: str
    stop_reason: AgentStopReason
    provider_calls: int
    tool_calls: int
    refused_calls: int
    proposed_next_step: NextStep | None
    next_step: NextStep | None
    override_reason: str | None
    explanation_sha256: str | None
    explanation_chars: int | None
    steps: tuple[AgentStep, ...]
    started_at: datetime | None = None
    finished_at: datetime | None = None


class AgentRunRepository(Protocol):
    """Store finished agent runs and read them back."""

    def add_run(self, run: AgentRunRecord) -> None:
        """Insert a run with its steps; both are append-only."""
        ...

    def get_run(self, run_id: UUID) -> AgentRunRecord | None:
        """Return a run and its ordered steps."""
        ...

    def count_runs_since(self, workflow_id: str, since: datetime) -> int:
        """Count a workflow's runs that started at or after ``since``."""
        ...


@dataclass(frozen=True, slots=True)
class DeliveryUpdate:
    """New delivery state written together with one attempt."""

    status: DeliveryStatus
    next_attempt_at: datetime
    error_code: str | None


class OutboxRepository(Protocol):
    """Delivery state and attempt log of outbox events."""

    def claim_due(self, now: datetime) -> tuple[OutboxEvent, OutboxDelivery] | None:
        """Lock the oldest due event; rows locked by other workers are skipped."""
        ...

    def record_attempt(
        self, event_id: UUID, attempt: DeliveryAttempt, change: DeliveryUpdate
    ) -> None:
        """Append the attempt and move the delivery to its new state."""
        ...

    def list_entries(self, workflow_id: str) -> tuple[OutboxEntry, ...]:
        """Return a workflow's events with delivery state and attempts."""
        ...

    def get_entry(self, event_id: UUID) -> OutboxEntry | None:
        """Return one event with its delivery state and attempts."""
        ...

    def requeue(self, event_id: UUID, now: datetime) -> bool:
        """Start a new round for an exhausted event; False if not exhausted."""
        ...

    def count_by_status(self) -> dict[DeliveryStatus, int]:
        """Count deliveries per status."""
        ...

    def list_by_status(
        self, status: DeliveryStatus, limit: int
    ) -> tuple[OutboxEntry, ...]:
        """Return the oldest entries in one status."""
        ...


class UnitOfWork(Protocol):
    """One transaction: commit explicitly, otherwise leaving the block rolls back."""

    @property
    def repository(self) -> WorkflowRepository:
        """Return the workflow repository bound to this transaction."""
        ...

    @property
    def documents(self) -> DocumentRepository:
        """Return the document repository bound to the same transaction."""
        ...

    @property
    def reconciliations(self) -> ReconciliationRepository:
        """Return the reconciliation repository bound to the same transaction."""
        ...

    @property
    def users(self) -> UserRepository:
        """Return the user repository bound to the same transaction."""
        ...

    @property
    def agent_runs(self) -> AgentRunRepository:
        """Return the agent-run repository bound to the same transaction."""
        ...

    @property
    def outbox(self) -> OutboxRepository:
        """Return the outbox delivery repository bound to the same transaction."""
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
