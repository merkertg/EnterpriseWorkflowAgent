"""Immutable in-memory workflow transitions; caller identity is not authentication."""

from dataclasses import dataclass, replace
from enum import StrEnum

from app.domain.errors import DomainValidationError
from app.domain.models import Invoice, PurchaseOrder, validate_text
from app.domain.rules import ValidationPolicy, ValidationResult, evaluate_invoice


class WorkflowState(StrEnum):
    """States of the educational invoice review process."""

    RECEIVED = "received"
    NEEDS_REVIEW = "needs_review"
    AWAITING_APPROVAL = "awaiting_approval"
    APPROVED = "approved"
    REJECTED = "rejected"


@dataclass(frozen=True, slots=True)
class HumanDecision:
    """Caller-supplied decision metadata; roles and verified identity arrive later."""

    actor_id: str
    reason: str

    def __post_init__(self) -> None:
        """Require an explicit actor and a nonempty bounded reason for a decision."""
        object.__setattr__(self, "actor_id", validate_text(self.actor_id, "actor_id"))
        object.__setattr__(self, "reason", validate_text(self.reason, "reason", 2000))


@dataclass(frozen=True, slots=True)
class Workflow:
    """A consistent snapshot; commands return a new object and retain the old one."""

    workflow_id: str
    invoice: Invoice
    purchase_order: PurchaseOrder | None
    policy: ValidationPolicy = ValidationPolicy()
    state: WorkflowState = WorkflowState.RECEIVED
    last_check: ValidationResult | None = None
    decision: HumanDecision | None = None

    def __post_init__(self) -> None:
        """Reject invalid inputs, stale findings and contradictory workflow states."""
        object.__setattr__(
            self, "workflow_id", validate_text(self.workflow_id, "workflow_id")
        )
        if not isinstance(self.state, WorkflowState):
            raise DomainValidationError("invalid_type", "state", "Use a WorkflowState")
        if not isinstance(self.policy, ValidationPolicy):
            raise DomainValidationError(
                "invalid_type", "policy", "Use a ValidationPolicy"
            )
        if self.decision is not None and not isinstance(self.decision, HumanDecision):
            raise DomainValidationError(
                "invalid_type", "decision", "Use a HumanDecision or None"
            )
        # Validate inputs even before the first check, without recording its outcome.
        current_check = evaluate_invoice(self.invoice, self.purchase_order, self.policy)
        if self.last_check is not None:
            if not isinstance(self.last_check, ValidationResult):
                raise DomainValidationError(
                    "invalid_type", "last_check", "Use a ValidationResult or None"
                )
            if self.last_check != current_check:
                raise DomainValidationError(
                    "stale_validation",
                    "last_check",
                    "Check does not match current inputs",
                )

        if self.state == WorkflowState.RECEIVED:
            valid = self.last_check is None and self.decision is None
        elif self.state == WorkflowState.NEEDS_REVIEW:
            valid = (
                self.last_check is not None
                and not self.last_check.is_valid
                and self.decision is None
            )
        elif self.state == WorkflowState.AWAITING_APPROVAL:
            valid = (
                self.last_check is not None
                and self.last_check.is_valid
                and self.decision is None
            )
        elif self.state == WorkflowState.APPROVED:
            valid = (
                self.last_check is not None
                and self.last_check.is_valid
                and self.decision is not None
            )
        else:
            valid = self.decision is not None
        if not valid:
            raise DomainValidationError(
                "inconsistent_workflow", "state", "State, check and decision disagree"
            )


def _require_state(
    workflow: Workflow, command: str, allowed: tuple[WorkflowState, ...]
) -> None:
    """Reject a command outside its documented source states, including terminals."""
    if not isinstance(workflow, Workflow):
        raise DomainValidationError("invalid_type", "workflow", "Use a Workflow")
    if workflow.state not in allowed:
        raise DomainValidationError(
            "invalid_transition",
            "state",
            f"Cannot {command} a workflow in state {workflow.state.value}",
        )


_OPEN_STATES = (
    WorkflowState.RECEIVED,
    WorkflowState.NEEDS_REVIEW,
    WorkflowState.AWAITING_APPROVAL,
)


def check_workflow(workflow: Workflow) -> Workflow:
    """Run basic checks in any open state; successful checks still need a decision."""
    _require_state(workflow, "check", _OPEN_STATES)
    result = evaluate_invoice(
        workflow.invoice, workflow.purchase_order, workflow.policy
    )
    state = (
        WorkflowState.AWAITING_APPROVAL
        if result.is_valid
        else WorkflowState.NEEDS_REVIEW
    )
    return replace(workflow, state=state, last_check=result, decision=None)


def approve_workflow(workflow: Workflow, decision: HumanDecision) -> Workflow:
    """Record an explicit demo decision only after a fresh successful basic check."""
    _require_state(workflow, "approve", (WorkflowState.AWAITING_APPROVAL,))
    current_check = evaluate_invoice(
        workflow.invoice, workflow.purchase_order, workflow.policy
    )
    if workflow.last_check != current_check or not current_check.is_valid:
        raise DomainValidationError(
            "stale_validation", "last_check", "Run checks on the current inputs first"
        )
    if not isinstance(decision, HumanDecision):
        raise DomainValidationError("invalid_type", "decision", "Use a HumanDecision")
    return replace(workflow, state=WorkflowState.APPROVED, decision=decision)


def reject_workflow(workflow: Workflow, decision: HumanDecision) -> Workflow:
    """Record a reasoned rejection in any open state, even before the first check."""
    _require_state(workflow, "reject", _OPEN_STATES)
    if not isinstance(decision, HumanDecision):
        raise DomainValidationError("invalid_type", "decision", "Use a HumanDecision")
    return replace(workflow, state=WorkflowState.REJECTED, decision=decision)


def revise_workflow(
    workflow: Workflow,
    invoice: Invoice,
    purchase_order: PurchaseOrder | None,
    *,
    policy: ValidationPolicy | None = None,
) -> Workflow:
    """Replace open inputs for the same invoice and require a new check and decision.

    The purchase order argument is required: None deliberately removes it.
    Omitting policy preserves the current policy; passing one changes it explicitly.
    """
    _require_state(workflow, "revise", _OPEN_STATES)
    if not isinstance(invoice, Invoice):
        raise DomainValidationError("invalid_type", "invoice", "Use an Invoice")
    if invoice.invoice_id != workflow.invoice.invoice_id:
        raise DomainValidationError(
            "invoice_identity_changed",
            "invoice_id",
            "Revision must keep the invoice ID",
        )
    return replace(
        workflow,
        invoice=invoice,
        purchase_order=purchase_order,
        policy=workflow.policy if policy is None else policy,
        state=WorkflowState.RECEIVED,
        last_check=None,
        decision=None,
    )
