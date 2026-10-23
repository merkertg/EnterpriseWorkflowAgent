"""Exercise the documented in-memory transition matrix and snapshot guards."""

from collections.abc import Callable
from dataclasses import replace
from decimal import Decimal
from typing import cast

import pytest

from app.domain.errors import DomainValidationError
from app.domain.models import Invoice, PurchaseOrder
from app.domain.money import Currency, Money
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


@pytest.fixture
def decision() -> HumanDecision:
    """Supply an explicit fictional actor and reason without claiming authentication."""
    return HumanDecision("REVIEWER-DEMO-001", "Fictional documents reviewed manually.")


@pytest.fixture
def workflow(invoice: Invoice, purchase_order: PurchaseOrder) -> Workflow:
    """Start a fictional workflow with no recorded check or human decision."""
    return Workflow("WF-DEMO-001", invoice, purchase_order)


def _at_state(
    workflow: Workflow, state: WorkflowState, decision: HumanDecision
) -> Workflow:
    """Reach each test state using public commands rather than forged state fields."""
    if state == WorkflowState.RECEIVED:
        return workflow
    if state == WorkflowState.NEEDS_REVIEW:
        return check_workflow(revise_workflow(workflow, workflow.invoice, None))
    checked = check_workflow(workflow)
    if state == WorkflowState.AWAITING_APPROVAL:
        return checked
    if state == WorkflowState.APPROVED:
        return approve_workflow(checked, decision)
    return reject_workflow(checked, decision)


@pytest.mark.parametrize(
    "state",
    [
        WorkflowState.RECEIVED,
        WorkflowState.NEEDS_REVIEW,
        WorkflowState.AWAITING_APPROVAL,
    ],
)
def test_check_is_allowed_in_each_open_state(
    workflow: Workflow, decision: HumanDecision, state: WorkflowState
) -> None:
    """Checks can be repeated, and their result still never constitutes approval."""
    current = _at_state(workflow, state, decision)
    checked = check_workflow(current)
    expected = (
        WorkflowState.NEEDS_REVIEW
        if state == WorkflowState.NEEDS_REVIEW
        else WorkflowState.AWAITING_APPROVAL
    )
    assert checked.state == expected
    assert checked.last_check is not None
    assert checked.decision is None
    assert check_workflow(checked) == checked
    assert current.state == state


def test_approval_records_only_an_explicit_human_decision(
    workflow: Workflow, decision: HumanDecision
) -> None:
    """Approval preserves the successful input snapshot and the caller's reason."""
    checked = check_workflow(workflow)
    approved = approve_workflow(checked, decision)
    assert approved.state == WorkflowState.APPROVED
    assert approved.decision == decision
    assert approved.last_check == checked.last_check
    assert checked.state == WorkflowState.AWAITING_APPROVAL
    assert checked.decision is None


@pytest.mark.parametrize("state", [WorkflowState.RECEIVED, WorkflowState.NEEDS_REVIEW])
def test_approval_requires_a_successful_check(
    workflow: Workflow, decision: HumanDecision, state: WorkflowState
) -> None:
    """Unchecked and failing documents cannot move directly to approved."""
    current = _at_state(workflow, state, decision)
    with pytest.raises(DomainValidationError) as error:
        approve_workflow(current, decision)
    assert error.value.code == "invalid_transition"
    assert current.state == state


@pytest.mark.parametrize(
    "state",
    [
        WorkflowState.RECEIVED,
        WorkflowState.NEEDS_REVIEW,
        WorkflowState.AWAITING_APPROVAL,
    ],
)
def test_rejection_is_allowed_from_each_open_state(
    workflow: Workflow, decision: HumanDecision, state: WorkflowState
) -> None:
    """A human can reject with a reason before or after running the basic checks."""
    current = _at_state(workflow, state, decision)
    rejected = reject_workflow(current, decision)
    assert rejected.state == WorkflowState.REJECTED
    assert rejected.decision == decision
    assert rejected.last_check == current.last_check
    assert current.state == state


@pytest.mark.parametrize(
    "state",
    [
        WorkflowState.RECEIVED,
        WorkflowState.NEEDS_REVIEW,
        WorkflowState.AWAITING_APPROVAL,
    ],
)
def test_revision_resets_each_open_state_and_removes_order_explicitly(
    workflow: Workflow, decision: HumanDecision, state: WorkflowState
) -> None:
    """A same-invoice edit clears the check and requires the workflow to start again."""
    current = _at_state(workflow, state, decision)
    revised_invoice = replace(current.invoice, supplier_id="SUPPLIER-DEMO-OTHER")
    revised = revise_workflow(current, revised_invoice, None)
    assert revised.workflow_id == current.workflow_id
    assert revised.invoice == revised_invoice
    assert revised.purchase_order is None
    assert revised.policy == current.policy
    assert revised.state == WorkflowState.RECEIVED
    assert revised.last_check is None
    assert revised.decision is None
    assert current.state == state
    assert check_workflow(revised).state == WorkflowState.NEEDS_REVIEW


@pytest.mark.parametrize("state", [WorkflowState.APPROVED, WorkflowState.REJECTED])
@pytest.mark.parametrize("command", ["check", "approve", "reject", "revise"])
def test_terminal_states_reject_every_command(
    workflow: Workflow, decision: HumanDecision, state: WorkflowState, command: str
) -> None:
    """Neither terminal state can be checked, decided again or silently reopened."""
    terminal = _at_state(workflow, state, decision)
    commands: dict[str, Callable[[], Workflow]] = {
        "check": lambda: check_workflow(terminal),
        "approve": lambda: approve_workflow(terminal, decision),
        "reject": lambda: reject_workflow(terminal, decision),
        "revise": lambda: revise_workflow(
            terminal, terminal.invoice, terminal.purchase_order
        ),
    }
    with pytest.raises(DomainValidationError) as error:
        commands[command]()
    assert error.value.code == "invalid_transition"
    assert terminal.state == state


def test_revision_cannot_change_the_invoice_identity(workflow: Workflow) -> None:
    """An edit cannot turn the existing workflow into a different invoice's process."""
    changed = replace(workflow.invoice, invoice_id="INV-DEMO-OTHER")
    with pytest.raises(DomainValidationError) as error:
        revise_workflow(workflow, changed, workflow.purchase_order)
    assert error.value.code == "invoice_identity_changed"


def test_workflow_requires_an_explicit_typed_policy(workflow: Workflow) -> None:
    """A workflow cannot keep None where its reproducible policy snapshot belongs."""
    with pytest.raises(DomainValidationError) as error:
        replace(workflow, policy=cast(ValidationPolicy, None))
    assert error.value.code == "invalid_type"
    assert error.value.field == "policy"


def test_revision_can_explicitly_replace_policy(workflow: Workflow) -> None:
    """A changed policy requires a fresh check even if the document values are equal."""
    checked = check_workflow(workflow)
    new_policy = ValidationPolicy("eur-only-demo", (Currency.EUR,))
    revised = revise_workflow(
        checked, checked.invoice, checked.purchase_order, policy=new_policy
    )
    assert revised.policy == new_policy
    assert revised.last_check is None
    assert checked.policy.policy_id == "basic-demo-v1"
    assert check_workflow(revised).state == WorkflowState.NEEDS_REVIEW


@pytest.mark.parametrize("field", ["invoice", "purchase_order", "policy"])
def test_checked_inputs_cannot_be_replaced_while_retaining_the_old_check(
    workflow: Workflow, field: str
) -> None:
    """Even otherwise valid replacement inputs make an earlier check stale."""
    checked = check_workflow(workflow)
    order = checked.purchase_order
    assert order is not None
    with pytest.raises(DomainValidationError) as error:
        if field == "invoice":
            replace(
                checked,
                invoice=replace(checked.invoice, supplier_id="SUPPLIER-DEMO-OTHER"),
            )
        elif field == "purchase_order":
            replace(
                checked,
                purchase_order=replace(order, supplier_id="SUPPLIER-DEMO-OTHER"),
            )
        else:
            replace(checked, policy=ValidationPolicy("new-demo-policy"))
    assert error.value.code == "stale_validation"


def test_repaired_invoice_needs_a_new_check_and_a_new_decision(
    workflow: Workflow, decision: HumanDecision
) -> None:
    """A repair must be checked again before an explicit decision can approve it."""
    incorrect = replace(
        workflow.invoice, declared_total=Money(Decimal("29.99"), Currency.USD)
    )
    failed = check_workflow(
        revise_workflow(workflow, incorrect, workflow.purchase_order)
    )
    assert failed.state == WorkflowState.NEEDS_REVIEW
    revised = revise_workflow(failed, workflow.invoice, workflow.purchase_order)
    with pytest.raises(DomainValidationError):
        approve_workflow(revised, decision)
    approved = approve_workflow(check_workflow(revised), decision)
    assert approved.state == WorkflowState.APPROVED
    assert failed.state == WorkflowState.NEEDS_REVIEW


@pytest.mark.parametrize("state", list(WorkflowState))
def test_workflow_constructor_rejects_state_without_required_evidence(
    workflow: Workflow, state: WorkflowState
) -> None:
    """Assigning a state string or enum cannot manufacture a check or human decision."""
    if state == WorkflowState.RECEIVED:
        checked = check_workflow(workflow)
        with pytest.raises(DomainValidationError) as error:
            replace(checked, state=state)
    else:
        with pytest.raises(DomainValidationError) as error:
            replace(workflow, state=state)
    assert error.value.code == "inconsistent_workflow"


@pytest.mark.parametrize("command", [approve_workflow, reject_workflow])
def test_decision_commands_reject_missing_metadata(
    workflow: Workflow,
    command: Callable[[Workflow, HumanDecision], Workflow],
) -> None:
    """Passing None is not an explicit decision, even in an eligible open state."""
    with pytest.raises(DomainValidationError) as error:
        command(check_workflow(workflow), cast(HumanDecision, None))
    assert error.value.code == "invalid_type"
    assert error.value.field == "decision"


@pytest.mark.parametrize("actor,reason", [("", "Reviewed"), ("demo", "   ")])
def test_human_decision_requires_both_actor_and_reason(actor: str, reason: str) -> None:
    """A blank caller ID or explanation cannot become a recorded human decision."""
    with pytest.raises(DomainValidationError):
        HumanDecision(actor, reason)
