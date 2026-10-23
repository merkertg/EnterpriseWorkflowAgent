"""Persistent workflow use cases with versions, audit and idempotent commands.

Each command runs in one unit of work: the idempotency record, an optional
validation snapshot, the workflow update and its audit event commit together or
not at all. Domain functions still decide every state transition.
"""

from collections.abc import Callable
from dataclasses import dataclass, replace
from uuid import UUID, uuid4

from app.application.errors import (
    ConcurrentWriteError,
    IdempotencyKeyClaimedError,
    IdempotencyKeyReusedError,
    NotFoundError,
    VersionConflictError,
    WorkflowAlreadyExistsError,
)
from app.application.ports import (
    AuditEvent,
    CommandContext,
    CommandName,
    CommandResult,
    IdempotencyRecord,
    StoredWorkflow,
    UnitOfWork,
    ValidationSnapshot,
    Versioned,
    VersionRef,
    WorkflowRepository,
    require_positive_version,
)
from app.domain.content_hash import CanonicalValue, content_hash, sha256_hex
from app.domain.errors import DomainValidationError
from app.domain.models import Invoice, PurchaseOrder, validate_text
from app.domain.rules import ValidationPolicy
from app.domain.workflow import (
    HumanDecision,
    Workflow,
    approve_workflow,
    check_workflow,
    reject_workflow,
    revise_workflow,
)

# A version-number race is retried a few times; a lost idempotency claim once.
_VERSION_ATTEMPTS = 3
_COMMAND_ATTEMPTS = 2


@dataclass(frozen=True, slots=True)
class _Change:
    """A computed domain result and the input versions it must be stored with."""

    previous: StoredWorkflow | None
    workflow: Workflow
    invoice_version: VersionRef
    purchase_order_version: VersionRef | None
    policy_version: VersionRef
    records_snapshot: bool = False


@dataclass(frozen=True, slots=True)
class _Inputs:
    """Input versions selected by a start or revise command."""

    invoice: Versioned[Invoice]
    purchase_order: Versioned[PurchaseOrder] | None
    policy: Versioned[ValidationPolicy]


def _require_type(value: object, expected: type, field: str) -> None:
    """Reject an argument of the wrong exact type before opening a transaction."""
    if type(value) is not expected:
        raise DomainValidationError(
            "invalid_type", field, f"{field} must be {expected.__name__}."
        )


def _found[T](value: T | None, label: str) -> T:
    """Turn a missing row into a stable not-found error without echoing input."""
    if value is None:
        raise NotFoundError(f"{label} was not found.")
    return value


def _load_inputs(
    repository: WorkflowRepository,
    invoice_version_id: UUID,
    purchase_order_version_id: UUID | None,
    policy_version_id: UUID,
) -> _Inputs:
    """Load explicitly selected versions; None deliberately means no purchase order."""
    invoice = _found(
        repository.get_invoice_version(invoice_version_id), "Invoice version"
    )
    purchase_order = (
        None
        if purchase_order_version_id is None
        else _found(
            repository.get_purchase_order_version(purchase_order_version_id),
            "Purchase order version",
        )
    )
    policy = _found(repository.get_policy_version(policy_version_id), "Policy version")
    return _Inputs(invoice, purchase_order, policy)


def _version_request(
    invoice_version_id: UUID,
    purchase_order_version_id: UUID | None,
    policy_version_id: UUID,
) -> dict[str, CanonicalValue]:
    """Describe selected versions for an idempotency request fingerprint."""
    _require_type(invoice_version_id, UUID, "invoice_version_id")
    if purchase_order_version_id is not None:
        _require_type(purchase_order_version_id, UUID, "purchase_order_version_id")
    _require_type(policy_version_id, UUID, "policy_version_id")
    return {
        "invoice_version_id": str(invoice_version_id),
        "purchase_order_version_id": None
        if purchase_order_version_id is None
        else str(purchase_order_version_id),
        "policy_version_id": str(policy_version_id),
    }


def append_version(
    business_id: str,
    digest: str,
    current: VersionRef | None,
    add: Callable[[VersionRef], None],
) -> VersionRef:
    """Inside a transaction: reuse identical latest content or add the next number."""
    if current is not None and current.content_hash == digest:
        return current
    ref = VersionRef(
        version_id=uuid4(),
        business_id=business_id,
        version_no=1 if current is None else current.version_no + 1,
        content_hash=digest,
    )
    add(ref)
    return ref


def append_invoice_version(
    repository: WorkflowRepository, invoice: Invoice
) -> VersionRef:
    """Record invoice content in the caller's transaction; documents reuse this."""
    return append_version(
        invoice.invoice_id,
        content_hash(invoice),
        repository.latest_invoice_version(invoice.invoice_id),
        lambda ref: repository.add_invoice_version(Versioned(ref, invoice)),
    )


class WorkflowService:
    """Coordinate domain commands with one transaction per command."""

    def __init__(self, unit_of_work: Callable[[], UnitOfWork]) -> None:
        """Receive a factory so every call gets a fresh transaction and session."""
        self._unit_of_work = unit_of_work

    # Immutable input versions -------------------------------------------------

    def record_invoice(self, invoice: Invoice) -> VersionRef:
        """Store invoice content, reusing the latest version if it is identical."""
        _require_type(invoice, Invoice, "invoice")
        return self._in_new_transaction(
            lambda repository: append_invoice_version(repository, invoice)
        )

    def record_purchase_order(self, purchase_order: PurchaseOrder) -> VersionRef:
        """Store purchase order content; a change creates the next version."""
        _require_type(purchase_order, PurchaseOrder, "purchase_order")
        business_id = purchase_order.purchase_order_id
        return self._in_new_transaction(
            lambda repository: append_version(
                business_id,
                content_hash(purchase_order),
                repository.latest_purchase_order_version(business_id),
                lambda ref: repository.add_purchase_order_version(
                    Versioned(ref, purchase_order)
                ),
            )
        )

    def record_policy(self, policy: ValidationPolicy) -> VersionRef:
        """Store validation policy data; a changed allowlist is a new version."""
        _require_type(policy, ValidationPolicy, "policy")
        return self._in_new_transaction(
            lambda repository: append_version(
                policy.policy_id,
                content_hash(policy),
                repository.latest_policy_version(policy.policy_id),
                lambda ref: repository.add_policy_version(Versioned(ref, policy)),
            )
        )

    def _in_new_transaction(
        self, append: Callable[[WorkflowRepository], VersionRef]
    ) -> VersionRef:
        """Run one version append per transaction, retrying a lost number race.

        Two writers can choose the same next number. The unique constraint lets
        only one commit; the other retries and then sees the winner's version.
        """
        for _attempt in range(_VERSION_ATTEMPTS):
            try:
                with self._unit_of_work() as unit:
                    ref = append(unit.repository)
                    unit.commit()
                    return ref
            except ConcurrentWriteError:
                continue
        raise VersionConflictError("Concurrent writers are creating versions.")

    # Workflow commands --------------------------------------------------------

    def start(
        self,
        workflow_id: str,
        *,
        invoice_version_id: UUID,
        purchase_order_version_id: UUID | None,
        policy_version_id: UUID,
        context: CommandContext,
    ) -> CommandResult:
        """Create a received workflow pinned to explicitly selected versions."""
        workflow_id = validate_text(workflow_id, "workflow_id")
        request = {
            "workflow_id": workflow_id,
            **_version_request(
                invoice_version_id, purchase_order_version_id, policy_version_id
            ),
        }

        def operation(repository: WorkflowRepository) -> _Change:
            """Refuse a reused ID, then let the domain validate the new workflow."""
            if repository.get_workflow(workflow_id) is not None:
                raise WorkflowAlreadyExistsError("Workflow ID is already in use.")
            inputs = _load_inputs(
                repository,
                invoice_version_id,
                purchase_order_version_id,
                policy_version_id,
            )
            workflow = Workflow(
                workflow_id,
                inputs.invoice.value,
                None if inputs.purchase_order is None else inputs.purchase_order.value,
                inputs.policy.value,
            )
            return _Change(
                previous=None,
                workflow=workflow,
                invoice_version=inputs.invoice.ref,
                purchase_order_version=None
                if inputs.purchase_order is None
                else inputs.purchase_order.ref,
                policy_version=inputs.policy.ref,
            )

        return self._execute(CommandName.START, request, context, operation)

    def check(
        self, workflow_id: str, *, expected_version: int, context: CommandContext
    ) -> CommandResult:
        """Evaluate the pinned inputs and store an immutable validation snapshot."""
        return self._transition(
            CommandName.CHECK,
            workflow_id,
            expected_version,
            {},
            context,
            lambda _repository, current: _same_inputs(
                current, check_workflow(current.workflow), records_snapshot=True
            ),
        )

    def approve(
        self,
        workflow_id: str,
        *,
        expected_version: int,
        decision: HumanDecision,
        context: CommandContext,
    ) -> CommandResult:
        """Record a caller-supplied decision after a fresh clean check."""
        _require_type(decision, HumanDecision, "decision")
        return self._transition(
            CommandName.APPROVE,
            workflow_id,
            expected_version,
            {"actor_id": decision.actor_id, "reason": decision.reason},
            context,
            lambda _repository, current: _same_inputs(
                current, approve_workflow(current.workflow, decision)
            ),
        )

    def reject(
        self,
        workflow_id: str,
        *,
        expected_version: int,
        decision: HumanDecision,
        context: CommandContext,
    ) -> CommandResult:
        """Record a reasoned rejection in any open state."""
        _require_type(decision, HumanDecision, "decision")
        return self._transition(
            CommandName.REJECT,
            workflow_id,
            expected_version,
            {"actor_id": decision.actor_id, "reason": decision.reason},
            context,
            lambda _repository, current: _same_inputs(
                current, reject_workflow(current.workflow, decision)
            ),
        )

    def revise(
        self,
        workflow_id: str,
        *,
        expected_version: int,
        invoice_version_id: UUID,
        purchase_order_version_id: UUID | None,
        policy_version_id: UUID,
        context: CommandContext,
    ) -> CommandResult:
        """Pin other input versions; older snapshots keep their original references."""
        request = _version_request(
            invoice_version_id, purchase_order_version_id, policy_version_id
        )

        def operation(
            repository: WorkflowRepository, current: StoredWorkflow
        ) -> _Change:
            """Load the new versions and reset the workflow through the domain."""
            inputs = _load_inputs(
                repository,
                invoice_version_id,
                purchase_order_version_id,
                policy_version_id,
            )
            workflow = revise_workflow(
                current.workflow,
                inputs.invoice.value,
                None if inputs.purchase_order is None else inputs.purchase_order.value,
                policy=inputs.policy.value,
            )
            return _Change(
                previous=current,
                workflow=workflow,
                invoice_version=inputs.invoice.ref,
                purchase_order_version=None
                if inputs.purchase_order is None
                else inputs.purchase_order.ref,
                policy_version=inputs.policy.ref,
            )

        return self._transition(
            CommandName.REVISE,
            workflow_id,
            expected_version,
            request,
            context,
            operation,
        )

    # Queries ------------------------------------------------------------------

    def get_workflow(self, workflow_id: str) -> StoredWorkflow:
        """Return the current stored workflow and its pinned version references."""
        workflow_id = validate_text(workflow_id, "workflow_id")
        with self._unit_of_work() as unit:
            return _found(unit.repository.get_workflow(workflow_id), "Workflow")

    def get_snapshot(self, snapshot_id: UUID) -> ValidationSnapshot:
        """Return a historical check result with the exact inputs it used."""
        _require_type(snapshot_id, UUID, "snapshot_id")
        with self._unit_of_work() as unit:
            return _found(
                unit.repository.get_snapshot(snapshot_id), "Validation snapshot"
            )

    def get_purchase_order_version(self, version_id: UUID) -> Versioned[PurchaseOrder]:
        """Return one stored purchase order version, including superseded ones."""
        _require_type(version_id, UUID, "version_id")
        with self._unit_of_work() as unit:
            return _found(
                unit.repository.get_purchase_order_version(version_id),
                "Purchase order version",
            )

    def audit_trail(self, workflow_id: str) -> tuple[AuditEvent, ...]:
        """Return all audit events for an existing workflow in commit order."""
        workflow_id = validate_text(workflow_id, "workflow_id")
        with self._unit_of_work() as unit:
            _found(unit.repository.get_workflow(workflow_id), "Workflow")
            return unit.repository.list_audit_events(workflow_id)

    # Shared command machinery -------------------------------------------------

    def _transition(
        self,
        command: CommandName,
        workflow_id: str,
        expected_version: int,
        arguments: dict[str, CanonicalValue],
        context: CommandContext,
        compute: Callable[[WorkflowRepository, StoredWorkflow], _Change],
    ) -> CommandResult:
        """Load a workflow, check the caller's version and compute the change."""
        workflow_id = validate_text(workflow_id, "workflow_id")
        require_positive_version(expected_version)
        request = {
            "workflow_id": workflow_id,
            "expected_version": expected_version,
            **arguments,
        }

        def operation(repository: WorkflowRepository) -> _Change:
            """Reject stale input before the domain sees an unexpected state."""
            current = _found(repository.get_workflow(workflow_id), "Workflow")
            if current.version != expected_version:
                raise VersionConflictError(
                    f"Expected version {expected_version}, "
                    f"current version is {current.version}."
                )
            return compute(repository, current)

        return self._execute(command, request, context, operation)

    def _execute(
        self,
        command: CommandName,
        arguments: dict[str, CanonicalValue],
        context: CommandContext,
        operation: Callable[[WorkflowRepository], _Change],
    ) -> CommandResult:
        """Replay a known key or apply a new change in one transaction."""
        _require_type(context, CommandContext, "context")
        request_hash = sha256_hex({"command": command.value, **arguments})
        for _attempt in range(_COMMAND_ATTEMPTS):
            try:
                with self._unit_of_work() as unit:
                    repository = unit.repository
                    existing = repository.get_idempotency_record(
                        context.idempotency_key
                    )
                    if existing is not None:
                        return _replay(existing, request_hash)
                    change = operation(repository)
                    result = _persist(
                        repository, command, change, context, request_hash
                    )
                    unit.commit()
                    return result
            except IdempotencyKeyClaimedError:
                # The other request committed first; the next attempt replays it.
                continue
            except (VersionConflictError, WorkflowAlreadyExistsError):
                # A duplicate may commit between our key lookup and workflow read;
                # its result is then visible, so a retry is answered as a replay.
                replayed = self._find_replay(context.idempotency_key, request_hash)
                if replayed is None:
                    raise
                return replayed
        raise VersionConflictError("The idempotency key is being used concurrently.")

    def _find_replay(self, key: str, request_hash: str) -> CommandResult | None:
        """Look the key up again in a new transaction after a conflict."""
        with self._unit_of_work() as unit:
            existing = unit.repository.get_idempotency_record(key)
        return None if existing is None else _replay(existing, request_hash)


def _same_inputs(
    current: StoredWorkflow, workflow: Workflow, *, records_snapshot: bool = False
) -> _Change:
    """Describe a transition that keeps the currently pinned input versions."""
    return _Change(
        previous=current,
        workflow=workflow,
        invoice_version=current.invoice_version,
        purchase_order_version=current.purchase_order_version,
        policy_version=current.policy_version,
        records_snapshot=records_snapshot,
    )


def _replay(existing: IdempotencyRecord, request_hash: str) -> CommandResult:
    """Return the original outcome only for the same command and arguments."""
    if existing.request_hash != request_hash:
        raise IdempotencyKeyReusedError(
            "The idempotency key was already used for another request."
        )
    return replace(existing.result, replayed=True)


def _persist(
    repository: WorkflowRepository,
    command: CommandName,
    change: _Change,
    context: CommandContext,
    request_hash: str,
) -> CommandResult:
    """Write key claim, snapshot, workflow and audit event in dependency order."""
    previous = change.previous
    workflow = change.workflow
    version = 1 if previous is None else previous.version + 1
    snapshot: ValidationSnapshot | None = None
    if change.records_snapshot:
        if workflow.last_check is None:
            raise DomainValidationError(
                "missing_check", "last_check", "A check command must produce a result"
            )
        snapshot = ValidationSnapshot(
            snapshot_id=uuid4(),
            workflow_id=workflow.workflow_id,
            workflow_version=version,
            invoice_version=change.invoice_version,
            purchase_order_version=change.purchase_order_version,
            policy_version=change.policy_version,
            result=workflow.last_check,
        )
    if snapshot is not None:
        last_snapshot_id: UUID | None = snapshot.snapshot_id
    elif workflow.last_check is not None and previous is not None:
        last_snapshot_id = previous.last_snapshot_id
    else:
        last_snapshot_id = None

    result = CommandResult(
        workflow_id=workflow.workflow_id,
        command=command,
        state=workflow.state,
        version=version,
        snapshot_id=None if snapshot is None else snapshot.snapshot_id,
    )
    # Claim the key first: a concurrent duplicate waits here instead of racing
    # on the workflow row, then replays the winner's committed result.
    repository.add_idempotency_record(
        IdempotencyRecord(context.idempotency_key, request_hash, result)
    )
    if snapshot is not None:
        repository.add_snapshot(snapshot)
    stored = StoredWorkflow(
        workflow=workflow,
        version=version,
        invoice_version=change.invoice_version,
        purchase_order_version=change.purchase_order_version,
        policy_version=change.policy_version,
        last_snapshot_id=last_snapshot_id,
    )
    if previous is None:
        repository.add_workflow(stored)
    else:
        repository.update_workflow(stored, previous.version)
    issues = () if workflow.last_check is None else workflow.last_check.issues
    repository.add_audit_event(
        AuditEvent(
            workflow_id=workflow.workflow_id,
            command=command,
            previous_state=None if previous is None else previous.workflow.state,
            new_state=workflow.state,
            workflow_version=version,
            invoice_version_id=change.invoice_version.version_id,
            purchase_order_version_id=None
            if change.purchase_order_version is None
            else change.purchase_order_version.version_id,
            policy_version_id=change.policy_version.version_id,
            snapshot_id=result.snapshot_id,
            issue_codes=tuple(issue.code.value for issue in issues)
            if snapshot is not None
            else (),
            actor_id=None if workflow.decision is None else workflow.decision.actor_id,
            source=context.source,
            idempotency_key=context.idempotency_key,
        )
    )
    return result
