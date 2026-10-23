"""Persistent workflow use cases with versions, audit and idempotent commands.

Each command runs in one unit of work: the idempotency record, an optional
validation snapshot, the workflow update, an optional outbox event and the
audit event commit together or not at all. Domain functions still decide every
state transition.

Commands accept an optional ``actor``. With one, the user is reloaded and
authorized inside the same transaction, so a role removed or an account
deactivated a moment earlier is honoured. Internal tools call without an actor.
Decisions always require one and must cite a fresh, matched reconciliation.
"""

from collections.abc import Callable
from dataclasses import dataclass, replace
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

from app.application.access import Permission, Principal, validate_unit
from app.application.errors import (
    ConcurrentWriteError,
    IdempotencyKeyClaimedError,
    IdempotencyKeyReusedError,
    MissingPermissionError,
    NotFoundError,
    ReconciliationNotForWorkflowError,
    ReconciliationNotMatchedError,
    SelfApprovalError,
    StaleReconciliationError,
    VersionConflictError,
    WorkflowAlreadyExistsError,
)
from app.application.ports import (
    AuditEvent,
    CommandContext,
    CommandName,
    CommandResult,
    IdempotencyRecord,
    OutboxEvent,
    OutboxEventType,
    StoredWorkflow,
    UnitOfWork,
    ValidationSnapshot,
    Versioned,
    VersionRef,
    WorkflowRepository,
    WorkflowSummary,
    require_positive_version,
)
from app.application.reconciliation import compute_freshness
from app.application.users import current_principal
from app.application.versions import (
    append_invoice_version,
    append_version,
    invoice_number_key,
    purchase_order_key,
    rules_key,
)
from app.domain.content_hash import CanonicalValue, content_hash, sha256_hex
from app.domain.errors import DomainValidationError
from app.domain.models import Invoice, PurchaseOrder, validate_text
from app.domain.reconciliation import ReconciliationStatus
from app.domain.rules import ValidationPolicy
from app.domain.workflow import (
    HumanDecision,
    Workflow,
    WorkflowState,
    approve_workflow,
    check_workflow,
    reject_workflow,
    revise_workflow,
)


class ReferenceKind(StrEnum):
    """Reference data recorded by administrators and selected by version ID."""

    PURCHASE_ORDER = "purchase_order"
    POLICY = "policy"
    RECONCILIATION_RULES = "reconciliation_rules"


# A version-number race is retried a few times; a lost idempotency claim once.
_VERSION_ATTEMPTS = 3
_COMMAND_ATTEMPTS = 2

type Operation = Callable[[UnitOfWork], "_Change"]


@dataclass(frozen=True, slots=True)
class _Change:
    """A computed domain result and everything that must be stored with it."""

    previous: StoredWorkflow | None
    workflow: Workflow
    invoice_version: VersionRef
    purchase_order_version: VersionRef | None
    policy_version: VersionRef
    records_snapshot: bool = False
    business_unit: str | None = None
    created_by: str | None = None
    actor_id: str | None = None
    outbox: OutboxEvent | None = None
    decision_reconciliation_id: UUID | None = None


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


def _actor_id(actor: Principal | None) -> str | None:
    """Validate an optional actor and return its ID for request fingerprints."""
    if actor is not None:
        _require_type(actor, Principal, "actor")
    return None if actor is None else actor.user_id


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
        """Store purchase order content; a change creates the next version.

        The order's lock key serializes this with approvals that check whether
        their reconciliation still uses the latest order version.
        """
        _require_type(purchase_order, PurchaseOrder, "purchase_order")
        business_id = purchase_order.purchase_order_id

        def append(repository: WorkflowRepository) -> VersionRef:
            """Lock the order key, then append a version if content changed."""
            repository.lock_keys((purchase_order_key(business_id),))
            return append_version(
                business_id,
                content_hash(purchase_order),
                repository.latest_purchase_order_version(business_id),
                lambda ref: repository.add_purchase_order_version(
                    Versioned(ref, purchase_order)
                ),
            )

        return self._in_new_transaction(append)

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
        business_unit: str,
        context: CommandContext,
        actor: Principal | None = None,
    ) -> CommandResult:
        """Create a received workflow in a business unit with pinned versions."""
        workflow_id = validate_text(workflow_id, "workflow_id")
        business_unit = validate_unit(business_unit)
        request = {
            "workflow_id": workflow_id,
            "business_unit": business_unit,
            "actor": _actor_id(actor),
            **_version_request(
                invoice_version_id, purchase_order_version_id, policy_version_id
            ),
        }

        def operation(unit: UnitOfWork) -> _Change:
            """Authorize, lock the invoice number, then let the domain validate."""
            principal = current_principal(unit, actor, Permission.SUBMIT, None)
            # The caller names the unit, so a foreign one is a refusal, not 404.
            if principal is not None and business_unit not in principal.units:
                raise MissingPermissionError(
                    "You cannot start workflows in this business unit."
                )
            repository = unit.repository
            inputs = _load_inputs(
                repository,
                invoice_version_id,
                purchase_order_version_id,
                policy_version_id,
            )
            invoice = inputs.invoice.value
            # Serializes with approvals that check this invoice for duplicates.
            repository.lock_keys(
                (invoice_number_key(invoice.supplier_id, invoice.invoice_id),)
            )
            if repository.get_workflow(workflow_id) is not None:
                raise WorkflowAlreadyExistsError("Workflow ID is already in use.")
            workflow = Workflow(
                workflow_id,
                invoice,
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
                business_unit=business_unit,
                created_by=None if principal is None else principal.user_id,
                actor_id=None if principal is None else principal.user_id,
            )

        return self._execute(CommandName.START, request, context, operation)

    def check(
        self,
        workflow_id: str,
        *,
        expected_version: int,
        context: CommandContext,
        actor: Principal | None = None,
    ) -> CommandResult:
        """Evaluate the pinned inputs and store an immutable validation snapshot."""

        def compute(unit: UnitOfWork, current: StoredWorkflow) -> _Change:
            """Authorize, then run the domain check."""
            principal = current_principal(
                unit, actor, Permission.SUBMIT, current.business_unit
            )
            return _same_inputs(
                current,
                check_workflow(current.workflow),
                records_snapshot=True,
                actor_id=None if principal is None else principal.user_id,
            )

        return self._transition(
            CommandName.CHECK,
            workflow_id,
            expected_version,
            {"actor": _actor_id(actor)},
            context,
            compute,
        )

    def approve(
        self,
        workflow_id: str,
        *,
        expected_version: int,
        reconciliation_snapshot_id: UUID,
        reason: str,
        actor: Principal,
        context: CommandContext,
    ) -> CommandResult:
        """Approve on a fresh matched reconciliation, as an authorized employee.

        Inside one transaction: the approver's current rights, the four-eyes
        rule, the snapshot's workflow and status, locks on the order, rules and
        invoice-number keys, freshness against current versions, the domain
        approval, the outbox event and the audit event.
        """
        _require_type(actor, Principal, "actor")
        _require_type(reconciliation_snapshot_id, UUID, "reconciliation_snapshot_id")
        reason = validate_text(reason, "reason", 2000)

        def compute(unit: UnitOfWork, current: StoredWorkflow) -> _Change:
            """Run every approval check after the locks are held."""
            principal = current_principal(
                unit, actor, Permission.DECIDE, current.business_unit
            )
            assert principal is not None
            if principal.user_id == current.created_by:
                raise SelfApprovalError(
                    "The employee who started a workflow cannot approve it."
                )
            snapshot = _found(
                unit.reconciliations.get_snapshot(reconciliation_snapshot_id),
                "Reconciliation snapshot",
            )
            if snapshot.workflow_id != current.workflow.workflow_id:
                raise ReconciliationNotForWorkflowError(
                    "The reconciliation snapshot belongs to another workflow."
                )
            invoice = current.workflow.invoice
            order = snapshot.inputs.purchase_order_version
            unit.repository.lock_keys(
                (
                    invoice_number_key(invoice.supplier_id, invoice.invoice_id),
                    purchase_order_key(
                        invoice.purchase_order_id
                        if order is None
                        else order.business_id
                    ),
                    rules_key(snapshot.inputs.rules_version.business_id),
                )
            )
            freshness = compute_freshness(unit, snapshot)
            if not freshness.is_current:
                reasons = tuple(reason.value for reason in freshness.reasons)
                raise StaleReconciliationError(
                    "The reconciliation is stale: " + ", ".join(reasons) + ".",
                    reasons,
                )
            if snapshot.result.status is not ReconciliationStatus.MATCHED:
                raise ReconciliationNotMatchedError(
                    "Approval needs a reconciliation without discrepancies."
                )
            workflow = approve_workflow(
                current.workflow, HumanDecision(principal.user_id, reason)
            )
            outbox = _outbox_event(
                OutboxEventType.INVOICE_APPROVED,
                current,
                principal,
                {
                    "reconciliation_snapshot_id": str(snapshot.snapshot_id),
                    "reconciliation_inputs_hash": snapshot.inputs_hash,
                },
            )
            return _same_inputs(
                current,
                workflow,
                actor_id=principal.user_id,
                outbox=outbox,
                decision_reconciliation_id=snapshot.snapshot_id,
            )

        return self._transition(
            CommandName.APPROVE,
            workflow_id,
            expected_version,
            {
                "actor": actor.user_id,
                "reconciliation_snapshot_id": str(reconciliation_snapshot_id),
                "reason": reason,
            },
            context,
            compute,
        )

    def reject(
        self,
        workflow_id: str,
        *,
        expected_version: int,
        reason: str,
        actor: Principal,
        context: CommandContext,
    ) -> CommandResult:
        """Reject in any open state, as an authorized employee, with an outbox event."""
        _require_type(actor, Principal, "actor")
        reason = validate_text(reason, "reason", 2000)

        def compute(unit: UnitOfWork, current: StoredWorkflow) -> _Change:
            """Authorize, then record the rejection and its outbox event."""
            principal = current_principal(
                unit, actor, Permission.DECIDE, current.business_unit
            )
            assert principal is not None
            workflow = reject_workflow(
                current.workflow, HumanDecision(principal.user_id, reason)
            )
            outbox = _outbox_event(
                OutboxEventType.INVOICE_REJECTED, current, principal, {}
            )
            return _same_inputs(
                current, workflow, actor_id=principal.user_id, outbox=outbox
            )

        return self._transition(
            CommandName.REJECT,
            workflow_id,
            expected_version,
            {"actor": actor.user_id, "reason": reason},
            context,
            compute,
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
        actor: Principal | None = None,
    ) -> CommandResult:
        """Pin other input versions; older snapshots keep their original references."""
        request = {
            "actor": _actor_id(actor),
            **_version_request(
                invoice_version_id, purchase_order_version_id, policy_version_id
            ),
        }

        def compute(unit: UnitOfWork, current: StoredWorkflow) -> _Change:
            """Authorize, load the new versions and reset through the domain."""
            principal = current_principal(
                unit, actor, Permission.SUBMIT, current.business_unit
            )
            inputs = _load_inputs(
                unit.repository,
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
                actor_id=None if principal is None else principal.user_id,
            )

        return self._transition(
            CommandName.REVISE,
            workflow_id,
            expected_version,
            request,
            context,
            compute,
        )

    # Queries ------------------------------------------------------------------

    def get_workflow(
        self, workflow_id: str, *, actor: Principal | None = None
    ) -> StoredWorkflow:
        """Return the current workflow; an actor must see its business unit."""
        workflow_id = validate_text(workflow_id, "workflow_id")
        with self._unit_of_work() as unit:
            return _visible(unit, workflow_id, actor)

    def list_workflows(
        self,
        *,
        actor: Principal | None = None,
        state: WorkflowState | None = None,
        limit: int = 100,
    ) -> tuple[WorkflowSummary, ...]:
        """List recently changed workflows in the actor's business units.

        Without an actor (internal tools) every unit is listed.
        """
        if state is not None:
            _require_type(state, WorkflowState, "state")
        if type(limit) is not int or not 1 <= limit <= 500:
            raise DomainValidationError(
                "invalid_limit", "limit", "limit must be between 1 and 500."
            )
        with self._unit_of_work() as unit:
            principal = current_principal(unit, actor, Permission.VIEW, None)
            units = None if principal is None else principal.units
            return unit.repository.list_workflows(units, state, limit)

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

    def latest_reference(
        self,
        kind: ReferenceKind,
        business_id: str,
        *,
        actor: Principal | None = None,
    ) -> VersionRef:
        """Return the latest version of an order, policy or rule set.

        Reference data is shared by all business units; viewing it needs the
        view permission only.
        """
        _require_type(kind, ReferenceKind, "kind")
        business_id = validate_text(business_id, "business_id")
        with self._unit_of_work() as unit:
            current_principal(unit, actor, Permission.VIEW, None)
            repository = unit.repository
            if kind is ReferenceKind.PURCHASE_ORDER:
                ref = repository.latest_purchase_order_version(business_id)
            elif kind is ReferenceKind.POLICY:
                ref = repository.latest_policy_version(business_id)
            else:
                ref = unit.reconciliations.latest_rules_version(business_id)
        return _found(ref, "Reference version")

    def audit_trail(
        self, workflow_id: str, *, actor: Principal | None = None
    ) -> tuple[AuditEvent, ...]:
        """Return all audit events for a visible workflow in commit order."""
        workflow_id = validate_text(workflow_id, "workflow_id")
        with self._unit_of_work() as unit:
            _visible(unit, workflow_id, actor)
            return unit.repository.list_audit_events(workflow_id)

    def outbox_events(
        self, workflow_id: str, *, actor: Principal | None = None
    ) -> tuple[OutboxEvent, ...]:
        """Return the outbox events written by a visible workflow's decisions."""
        workflow_id = validate_text(workflow_id, "workflow_id")
        with self._unit_of_work() as unit:
            _visible(unit, workflow_id, actor)
            return unit.repository.list_outbox_events(workflow_id)

    # Shared command machinery -------------------------------------------------

    def _transition(
        self,
        command: CommandName,
        workflow_id: str,
        expected_version: int,
        arguments: dict[str, CanonicalValue],
        context: CommandContext,
        compute: Callable[[UnitOfWork, StoredWorkflow], _Change],
    ) -> CommandResult:
        """Load a workflow, check the caller's version and compute the change."""
        workflow_id = validate_text(workflow_id, "workflow_id")
        require_positive_version(expected_version)
        request = {
            "workflow_id": workflow_id,
            "expected_version": expected_version,
            **arguments,
        }

        def operation(unit: UnitOfWork) -> _Change:
            """Reject stale input before the domain sees an unexpected state."""
            current = _found(unit.repository.get_workflow(workflow_id), "Workflow")
            if current.version != expected_version:
                raise VersionConflictError(
                    f"Expected version {expected_version}, "
                    f"current version is {current.version}."
                )
            return compute(unit, current)

        return self._execute(command, request, context, operation)

    def _execute(
        self,
        command: CommandName,
        arguments: dict[str, CanonicalValue],
        context: CommandContext,
        operation: Operation,
    ) -> CommandResult:
        """Replay a known key or apply a new change in one transaction."""
        _require_type(context, CommandContext, "context")
        request_hash = sha256_hex({"command": command.value, **arguments})
        for _attempt in range(_COMMAND_ATTEMPTS):
            try:
                with self._unit_of_work() as unit:
                    existing = unit.repository.get_idempotency_record(
                        context.idempotency_key
                    )
                    if existing is not None:
                        return _replay(existing, request_hash)
                    change = operation(unit)
                    result = _persist(
                        unit.repository, command, change, context, request_hash
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


def _visible(
    unit: UnitOfWork, workflow_id: str, actor: Principal | None
) -> StoredWorkflow:
    """Load a workflow the actor may view; another unit's work is not found."""
    stored = _found(unit.repository.get_workflow(workflow_id), "Workflow")
    current_principal(unit, actor, Permission.VIEW, stored.business_unit)
    return stored


def _outbox_event(
    event_type: OutboxEventType,
    current: StoredWorkflow,
    principal: Principal,
    extra: dict[str, Any],
) -> OutboxEvent:
    """Describe a decision for the next process step with IDs and hashes only."""
    version = current.version + 1
    order = current.purchase_order_version
    return OutboxEvent(
        event_id=uuid4(),
        event_type=event_type,
        workflow_id=current.workflow.workflow_id,
        workflow_version=version,
        payload={
            "event_type": event_type.value,
            "workflow_id": current.workflow.workflow_id,
            "workflow_version": version,
            "business_unit": current.business_unit,
            "decided_by": principal.user_id,
            "invoice_version_id": str(current.invoice_version.version_id),
            "invoice_content_hash": current.invoice_version.content_hash,
            "purchase_order_version_id": None
            if order is None
            else str(order.version_id),
            **extra,
        },
    )


def _same_inputs(
    current: StoredWorkflow,
    workflow: Workflow,
    *,
    records_snapshot: bool = False,
    actor_id: str | None = None,
    outbox: OutboxEvent | None = None,
    decision_reconciliation_id: UUID | None = None,
) -> _Change:
    """Describe a transition that keeps the currently pinned input versions."""
    return _Change(
        previous=current,
        workflow=workflow,
        invoice_version=current.invoice_version,
        purchase_order_version=current.purchase_order_version,
        policy_version=current.policy_version,
        records_snapshot=records_snapshot,
        actor_id=actor_id,
        outbox=outbox,
        decision_reconciliation_id=decision_reconciliation_id,
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
    """Write key, snapshot, workflow, outbox and audit in dependency order."""
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
    business_unit = change.business_unit or (
        None if previous is None else previous.business_unit
    )
    if business_unit is None:
        raise DomainValidationError(
            "missing_unit", "business_unit", "A workflow needs a business unit."
        )
    stored = StoredWorkflow(
        workflow=workflow,
        version=version,
        invoice_version=change.invoice_version,
        purchase_order_version=change.purchase_order_version,
        policy_version=change.policy_version,
        last_snapshot_id=last_snapshot_id,
        business_unit=business_unit,
        created_by=change.created_by if previous is None else previous.created_by,
        decision_reconciliation_id=change.decision_reconciliation_id
        or (None if previous is None else previous.decision_reconciliation_id),
    )
    if previous is None:
        repository.add_workflow(stored)
    else:
        repository.update_workflow(stored, previous.version)
    if change.outbox is not None:
        repository.add_outbox_event(change.outbox)
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
            actor_id=change.actor_id,
            source=context.source,
            idempotency_key=context.idempotency_key,
            reconciliation_snapshot_id=change.decision_reconciliation_id,
            outbox_event_id=None if change.outbox is None else change.outbox.event_id,
        )
    )
    return result
