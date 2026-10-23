"""SQLAlchemy implementation of the workflow repository and unit of work.

Rows are converted to domain objects on load and every input version is
re-hashed, so silently edited content is reported instead of trusted.
"""

from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from decimal import Decimal
from types import TracebackType
from typing import Any, Self, cast
from uuid import UUID

from sqlalchemy import CursorResult, select, update
from sqlalchemy import func as sql_func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.application.errors import (
    ConcurrentWriteError,
    IdempotencyKeyClaimedError,
    StoredDataIntegrityError,
    VersionConflictError,
    WorkflowAlreadyExistsError,
)
from app.application.ports import (
    AuditEvent,
    CommandName,
    CommandResult,
    IdempotencyRecord,
    StoredWorkflow,
    ValidationSnapshot,
    Versioned,
    VersionRef,
)
from app.domain.content_hash import content_hash
from app.domain.errors import DomainValidationError
from app.domain.models import Invoice, LineItem, PurchaseOrder
from app.domain.money import Currency, Money
from app.domain.rules import IssueCode, RuleIssue, ValidationPolicy, ValidationResult
from app.domain.workflow import HumanDecision, Workflow, WorkflowState
from app.infrastructure.db.models import (
    AuditEventRow,
    IdempotencyRecordRow,
    InvoiceLineRow,
    InvoiceVersionRow,
    PolicyVersionRow,
    PurchaseOrderLineRow,
    PurchaseOrderVersionRow,
    ValidationSnapshotRow,
    WorkflowRow,
)

# Unique constraints whose violation means "another writer was first".
_VERSION_RACES = {
    "uq_invoice_versions_invoice_id_version_no",
    "uq_purchase_order_versions_purchase_order_id_version_no",
    "uq_policy_versions_policy_id_version_no",
}


def _constraint_name(error: IntegrityError) -> str | None:
    """Read the violated constraint reported by PostgreSQL through psycopg."""
    diagnostics = getattr(error.orig, "diag", None)
    name = getattr(diagnostics, "constraint_name", None)
    return name if isinstance(name, str) else None


@contextmanager
def _reconstructing(label: str) -> Iterator[None]:
    """Report stored data the domain rejects as an integrity problem."""
    try:
        yield
    except (DomainValidationError, ValueError, KeyError, TypeError) as error:
        raise StoredDataIntegrityError(
            f"Stored {label} does not satisfy domain rules."
        ) from error


def _verified[T: (Invoice, PurchaseOrder, ValidationPolicy)](
    ref: VersionRef, value: T, label: str
) -> Versioned[T]:
    """Recompute the content hash so an edited row cannot pass as the original."""
    if content_hash(value) != ref.content_hash:
        raise StoredDataIntegrityError(f"Stored {label} does not match its hash.")
    return Versioned(ref, value)


def _lines(
    rows: Sequence[InvoiceLineRow | PurchaseOrderLineRow], currency: Currency
) -> tuple[LineItem, ...]:
    """Rebuild ordered domain lines from exact NUMERIC values."""
    return tuple(
        LineItem(
            sku=row.sku,
            description=row.description,
            quantity=row.quantity,
            unit_price=Money(row.unit_price, currency),
        )
        for row in rows
    )


def _issue_data(issue: RuleIssue) -> dict[str, Any]:
    """Store a finding's code, field and printable values as JSON."""
    return {
        "code": issue.code.value,
        "field": issue.field,
        "message": issue.message,
        "expected": issue.expected,
        "actual": issue.actual,
    }


def _issue_from_data(data: dict[str, Any]) -> RuleIssue:
    """Rebuild a stored finding; unknown codes fail as invalid stored data."""
    return RuleIssue(
        IssueCode(data["code"]),
        data["field"],
        data["message"],
        data.get("expected"),
        data.get("actual"),
    )


class SqlAlchemyWorkflowRepository:
    """Repository bound to one session and therefore to one transaction."""

    def __init__(self, session: Session) -> None:
        """Use the unit of work's session; this class never commits."""
        self._session = session

    def _insert(self, *rows: object) -> None:
        """Flush immediately so constraint races surface at a known point."""
        self._session.add_all(rows)
        try:
            self._session.flush()
        except IntegrityError as error:
            name = _constraint_name(error)
            if name in _VERSION_RACES:
                raise ConcurrentWriteError(
                    "Another writer created this version."
                ) from error
            if name == "pk_idempotency_records":
                raise IdempotencyKeyClaimedError(
                    "Another request committed this idempotency key."
                ) from error
            if name == "pk_workflows":
                raise WorkflowAlreadyExistsError(
                    "Workflow ID is already in use."
                ) from error
            raise

    # Versions -----------------------------------------------------------------

    def _latest[R: (InvoiceVersionRow, PurchaseOrderVersionRow, PolicyVersionRow)](
        self, row_type: type[R], key_column: Any, business_id: str
    ) -> VersionRef | None:
        """Return the reference of the highest version number for a key."""
        row = self._session.scalars(
            select(row_type)
            .where(key_column == business_id)
            .order_by(row_type.version_no.desc())
            .limit(1)
        ).first()
        if row is None:
            return None
        return VersionRef(row.id, business_id, row.version_no, row.content_hash)

    def latest_invoice_version(self, invoice_id: str) -> VersionRef | None:
        """Return the highest stored version number for an invoice ID."""
        return self._latest(InvoiceVersionRow, InvoiceVersionRow.invoice_id, invoice_id)

    def latest_purchase_order_version(
        self, purchase_order_id: str
    ) -> VersionRef | None:
        """Return the highest stored version number for a purchase order ID."""
        return self._latest(
            PurchaseOrderVersionRow,
            PurchaseOrderVersionRow.purchase_order_id,
            purchase_order_id,
        )

    def latest_policy_version(self, policy_id: str) -> VersionRef | None:
        """Return the highest stored version number for a policy ID."""
        return self._latest(PolicyVersionRow, PolicyVersionRow.policy_id, policy_id)

    def add_invoice_version(self, version: Versioned[Invoice]) -> None:
        """Insert a header and ordered lines in the current transaction."""
        ref, invoice = version.ref, version.value
        header = InvoiceVersionRow(
            id=ref.version_id,
            invoice_id=invoice.invoice_id,
            version_no=ref.version_no,
            content_hash=ref.content_hash,
            supplier_id=invoice.supplier_id,
            purchase_order_id=invoice.purchase_order_id,
            currency=invoice.currency.value,
            declared_total=invoice.declared_total.amount,
        )
        # Insert the header first so a version-number race is detected early.
        self._insert(header)
        self._insert(
            *(
                InvoiceLineRow(
                    invoice_version_id=ref.version_id,
                    line_no=number,
                    sku=line.sku,
                    description=line.description,
                    quantity=line.quantity,
                    unit_price=line.unit_price.amount,
                )
                for number, line in enumerate(invoice.lines, start=1)
            )
        )

    def add_purchase_order_version(self, version: Versioned[PurchaseOrder]) -> None:
        """Insert a purchase order header and its ordered lines."""
        ref, order = version.ref, version.value
        self._insert(
            PurchaseOrderVersionRow(
                id=ref.version_id,
                purchase_order_id=order.purchase_order_id,
                version_no=ref.version_no,
                content_hash=ref.content_hash,
                supplier_id=order.supplier_id,
                currency=order.currency.value,
            )
        )
        self._insert(
            *(
                PurchaseOrderLineRow(
                    purchase_order_version_id=ref.version_id,
                    line_no=number,
                    sku=line.sku,
                    description=line.description,
                    quantity=line.quantity,
                    unit_price=line.unit_price.amount,
                )
                for number, line in enumerate(order.lines, start=1)
            )
        )

    def add_policy_version(self, version: Versioned[ValidationPolicy]) -> None:
        """Insert a policy version, keeping currency order."""
        ref, policy = version.ref, version.value
        self._insert(
            PolicyVersionRow(
                id=ref.version_id,
                policy_id=policy.policy_id,
                version_no=ref.version_no,
                content_hash=ref.content_hash,
                allowed_currencies=[item.value for item in policy.allowed_currencies],
            )
        )

    def get_invoice_version(self, version_id: UUID) -> Versioned[Invoice] | None:
        """Load and hash-verify one invoice version."""
        row = self._session.get(InvoiceVersionRow, version_id)
        if row is None:
            return None
        lines = self._session.scalars(
            select(InvoiceLineRow)
            .where(InvoiceLineRow.invoice_version_id == version_id)
            .order_by(InvoiceLineRow.line_no)
        ).all()
        with _reconstructing("invoice version"):
            currency = Currency(row.currency)
            invoice = Invoice(
                invoice_id=row.invoice_id,
                supplier_id=row.supplier_id,
                purchase_order_id=row.purchase_order_id,
                currency=currency,
                lines=_lines(lines, currency),
                declared_total=Money(row.declared_total, currency),
            )
        ref = VersionRef(row.id, row.invoice_id, row.version_no, row.content_hash)
        return _verified(ref, invoice, "invoice version")

    def get_purchase_order_version(
        self, version_id: UUID
    ) -> Versioned[PurchaseOrder] | None:
        """Load and hash-verify one purchase order version."""
        row = self._session.get(PurchaseOrderVersionRow, version_id)
        if row is None:
            return None
        lines = self._session.scalars(
            select(PurchaseOrderLineRow)
            .where(PurchaseOrderLineRow.purchase_order_version_id == version_id)
            .order_by(PurchaseOrderLineRow.line_no)
        ).all()
        with _reconstructing("purchase order version"):
            currency = Currency(row.currency)
            order = PurchaseOrder(
                purchase_order_id=row.purchase_order_id,
                supplier_id=row.supplier_id,
                currency=currency,
                lines=_lines(lines, currency),
            )
        ref = VersionRef(
            row.id, row.purchase_order_id, row.version_no, row.content_hash
        )
        return _verified(ref, order, "purchase order version")

    def get_policy_version(
        self, version_id: UUID
    ) -> Versioned[ValidationPolicy] | None:
        """Load and hash-verify one policy version."""
        row = self._session.get(PolicyVersionRow, version_id)
        if row is None:
            return None
        with _reconstructing("policy version"):
            policy = ValidationPolicy(
                policy_id=row.policy_id,
                allowed_currencies=tuple(
                    Currency(item) for item in row.allowed_currencies
                ),
            )
        ref = VersionRef(row.id, row.policy_id, row.version_no, row.content_hash)
        return _verified(ref, policy, "policy version")

    def _required[T](
        self, loader: Callable[[UUID], T | None], version_id: UUID, label: str
    ) -> T:
        """Load a referenced version that a foreign key guarantees should exist."""
        value = loader(version_id)
        if value is None:
            raise StoredDataIntegrityError(f"Referenced {label} is missing.")
        return value

    # Workflows and snapshots ----------------------------------------------------

    def get_workflow(self, workflow_id: str) -> StoredWorkflow | None:
        """Rebuild the domain workflow; the domain re-checks the stored result."""
        row = self._session.get(WorkflowRow, workflow_id)
        if row is None:
            return None
        invoice = self._required(
            self.get_invoice_version, row.invoice_version_id, "invoice version"
        )
        order = (
            None
            if row.purchase_order_version_id is None
            else self._required(
                self.get_purchase_order_version,
                row.purchase_order_version_id,
                "purchase order version",
            )
        )
        policy = self._required(
            self.get_policy_version, row.policy_version_id, "policy version"
        )
        last_check: ValidationResult | None = None
        if row.last_snapshot_id is not None:
            snapshot = self._required(
                self.get_snapshot, row.last_snapshot_id, "validation snapshot"
            )
            if (
                snapshot.invoice_version != invoice.ref
                or snapshot.purchase_order_version
                != (None if order is None else order.ref)
                or snapshot.policy_version != policy.ref
            ):
                raise StoredDataIntegrityError(
                    "Latest snapshot does not use the workflow's pinned inputs."
                )
            last_check = snapshot.result
        decision = (
            None
            if row.decision_actor_id is None or row.decision_reason is None
            else HumanDecision(row.decision_actor_id, row.decision_reason)
        )
        with _reconstructing("workflow"):
            workflow = Workflow(
                workflow_id=row.workflow_id,
                invoice=invoice.value,
                purchase_order=None if order is None else order.value,
                policy=policy.value,
                state=WorkflowState(row.state),
                last_check=last_check,
                decision=decision,
            )
        return StoredWorkflow(
            workflow=workflow,
            version=row.version,
            invoice_version=invoice.ref,
            purchase_order_version=None if order is None else order.ref,
            policy_version=policy.ref,
            last_snapshot_id=row.last_snapshot_id,
        )

    @staticmethod
    def _workflow_values(stored: StoredWorkflow) -> dict[str, Any]:
        """Map mutable workflow fields to columns for insert and update."""
        workflow = stored.workflow
        return {
            "invoice_id": workflow.invoice.invoice_id,
            "state": workflow.state.value,
            "version": stored.version,
            "invoice_version_id": stored.invoice_version.version_id,
            "purchase_order_version_id": None
            if stored.purchase_order_version is None
            else stored.purchase_order_version.version_id,
            "policy_version_id": stored.policy_version.version_id,
            "last_snapshot_id": stored.last_snapshot_id,
            "decision_actor_id": None
            if workflow.decision is None
            else workflow.decision.actor_id,
            "decision_reason": None
            if workflow.decision is None
            else workflow.decision.reason,
        }

    def add_workflow(self, stored: StoredWorkflow) -> None:
        """Insert a new workflow row."""
        self._insert(
            WorkflowRow(
                workflow_id=stored.workflow.workflow_id,
                **self._workflow_values(stored),
            )
        )

    def update_workflow(self, stored: StoredWorkflow, expected_version: int) -> None:
        """Compare-and-set on the version column; zero rows means a lost race."""
        statement = (
            update(WorkflowRow)
            .where(
                WorkflowRow.workflow_id == stored.workflow.workflow_id,
                WorkflowRow.version == expected_version,
            )
            .values(**self._workflow_values(stored), updated_at=sql_func.now())
            .execution_options(synchronize_session=False)
        )
        result = cast(CursorResult[Any], self._session.execute(statement))
        if result.rowcount != 1:
            raise VersionConflictError(
                "The workflow changed after it was read; reload and retry."
            )

    def add_snapshot(self, snapshot: ValidationSnapshot) -> None:
        """Insert a check result with copies of each input's content hash."""
        result = snapshot.result
        order = snapshot.purchase_order_version
        self._insert(
            ValidationSnapshotRow(
                id=snapshot.snapshot_id,
                workflow_id=snapshot.workflow_id,
                workflow_version=snapshot.workflow_version,
                invoice_version_id=snapshot.invoice_version.version_id,
                invoice_content_hash=snapshot.invoice_version.content_hash,
                purchase_order_version_id=None if order is None else order.version_id,
                purchase_order_content_hash=None
                if order is None
                else order.content_hash,
                policy_version_id=snapshot.policy_version.version_id,
                policy_content_hash=snapshot.policy_version.content_hash,
                currency=result.calculated_total.currency.value,
                calculated_total=result.calculated_total.amount,
                issues=[_issue_data(issue) for issue in result.issues],
            )
        )

    def get_snapshot(self, snapshot_id: UUID) -> ValidationSnapshot | None:
        """Load a historical result with its original, hash-verified inputs."""
        row = self._session.get(ValidationSnapshotRow, snapshot_id)
        if row is None:
            return None
        invoice = self._required(
            self.get_invoice_version, row.invoice_version_id, "invoice version"
        )
        order = (
            None
            if row.purchase_order_version_id is None
            else self._required(
                self.get_purchase_order_version,
                row.purchase_order_version_id,
                "purchase order version",
            )
        )
        policy = self._required(
            self.get_policy_version, row.policy_version_id, "policy version"
        )
        hashes_match = (
            row.invoice_content_hash == invoice.ref.content_hash
            and row.policy_content_hash == policy.ref.content_hash
            and row.purchase_order_content_hash
            == (None if order is None else order.ref.content_hash)
        )
        stored_total = Money(Decimal(row.calculated_total), Currency(row.currency))
        if not hashes_match or stored_total != invoice.value.calculated_total:
            raise StoredDataIntegrityError(
                "Snapshot hashes or totals disagree with stored inputs."
            )
        with _reconstructing("validation snapshot"):
            result = ValidationResult(
                invoice=invoice.value,
                purchase_order=None if order is None else order.value,
                policy=policy.value,
                issues=tuple(_issue_from_data(item) for item in row.issues),
            )
        return ValidationSnapshot(
            snapshot_id=row.id,
            workflow_id=row.workflow_id,
            workflow_version=row.workflow_version,
            invoice_version=invoice.ref,
            purchase_order_version=None if order is None else order.ref,
            policy_version=policy.ref,
            result=result,
        )

    # Audit and idempotency ------------------------------------------------------

    def add_audit_event(self, event: AuditEvent) -> None:
        """Append one audit row; the database assigns its ID and time."""
        self._insert(
            AuditEventRow(
                workflow_id=event.workflow_id,
                command=event.command.value,
                previous_state=None
                if event.previous_state is None
                else event.previous_state.value,
                new_state=event.new_state.value,
                workflow_version=event.workflow_version,
                invoice_version_id=event.invoice_version_id,
                purchase_order_version_id=event.purchase_order_version_id,
                policy_version_id=event.policy_version_id,
                snapshot_id=event.snapshot_id,
                issue_codes=list(event.issue_codes),
                actor_id=event.actor_id,
                source=event.source,
                idempotency_key=event.idempotency_key,
            )
        )

    def list_audit_events(self, workflow_id: str) -> tuple[AuditEvent, ...]:
        """Return a workflow's audit events ordered by their identity column."""
        rows = self._session.scalars(
            select(AuditEventRow)
            .where(AuditEventRow.workflow_id == workflow_id)
            .order_by(AuditEventRow.id)
        ).all()
        return tuple(
            AuditEvent(
                workflow_id=row.workflow_id,
                command=CommandName(row.command),
                previous_state=None
                if row.previous_state is None
                else WorkflowState(row.previous_state),
                new_state=WorkflowState(row.new_state),
                workflow_version=row.workflow_version,
                invoice_version_id=row.invoice_version_id,
                purchase_order_version_id=row.purchase_order_version_id,
                policy_version_id=row.policy_version_id,
                snapshot_id=row.snapshot_id,
                issue_codes=tuple(row.issue_codes),
                actor_id=row.actor_id,
                source=row.source,
                idempotency_key=row.idempotency_key,
                event_id=row.id,
                occurred_at=row.occurred_at,
            )
            for row in rows
        )

    def get_idempotency_record(self, key: str) -> IdempotencyRecord | None:
        """Return the stored outcome for a key, if one was committed."""
        row = self._session.get(IdempotencyRecordRow, key)
        if row is None:
            return None
        return IdempotencyRecord(
            key=row.key,
            request_hash=row.request_hash,
            result=CommandResult(
                workflow_id=row.workflow_id,
                command=CommandName(row.command),
                state=WorkflowState(row.result_state),
                version=row.result_version,
                snapshot_id=row.snapshot_id,
            ),
        )

    def add_idempotency_record(self, record: IdempotencyRecord) -> None:
        """Insert the key now; a concurrent same-key insert waits for this one."""
        result = record.result
        self._insert(
            IdempotencyRecordRow(
                key=record.key,
                command=result.command.value,
                request_hash=record.request_hash,
                workflow_id=result.workflow_id,
                result_state=result.state.value,
                result_version=result.version,
                snapshot_id=result.snapshot_id,
            )
        )


class SqlAlchemyUnitOfWork:
    """Open one session per command; leaving without commit rolls back."""

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        """Store the factory; the session is created when the block starts."""
        self._session_factory = session_factory
        self._session: Session | None = None
        self._repository: SqlAlchemyWorkflowRepository | None = None

    @property
    def repository(self) -> SqlAlchemyWorkflowRepository:
        """Return the repository for the open transaction."""
        if self._repository is None:
            raise RuntimeError("Use the unit of work inside a with block.")
        return self._repository

    def __enter__(self) -> Self:
        """Start a session; SQLAlchemy begins the transaction on first use."""
        self._session = self._session_factory()
        self._repository = SqlAlchemyWorkflowRepository(self._session)
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Discard uncommitted work, including after an exception, and close."""
        if self._session is not None:
            self._session.rollback()
            self._session.close()
        self._session = None
        self._repository = None

    def commit(self) -> None:
        """Commit every write made through this unit's repository."""
        if self._session is None:
            raise RuntimeError("Use the unit of work inside a with block.")
        self._session.commit()
