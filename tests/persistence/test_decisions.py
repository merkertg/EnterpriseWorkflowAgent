"""Employee decisions: roles, units, four eyes, fresh snapshots and the outbox."""

import threading
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, replace
from decimal import Decimal
from uuid import UUID

import pytest
from sqlalchemy import Engine, text

from app.application.access import Principal
from app.application.documents import DocumentService
from app.application.errors import (
    MissingPermissionError,
    NotFoundError,
    ReconciliationNotForWorkflowError,
    ReconciliationNotMatchedError,
    SelfApprovalError,
    StaleReconciliationError,
    UnauthenticatedError,
)
from app.application.ports import (
    CommandName,
    CommandResult,
    DeliveryStatus,
    OutboxEventType,
)
from app.application.reconciliation import ReconciliationService
from app.application.users import UserService
from app.application.versions import invoice_number_key, purchase_order_key, rules_key
from app.application.workflows import WorkflowService
from app.demo import build_demo_documents
from app.domain.models import LineItem
from app.domain.money import Currency, Money
from app.domain.reconciliation import ReconciliationRules
from app.domain.rules import ValidationPolicy
from app.domain.workflow import WorkflowState
from app.infrastructure.db.repository import SqlAlchemyWorkflowRepository
from app.samples import invoice_text
from tests.persistence.conftest import TABLES, UNIT, USD_RULES, context, employee

pytestmark = pytest.mark.postgres

SEAL = ("DEMO-SEAL", "Fictional seal kit")
FILTER = ("DEMO-FILTER", "Fictional filter")
EXACT = [(*SEAL, "2", "12.50"), (*FILTER, "1", "5.00")]
REASON = "Fictional invoice matches the order"


@dataclass(frozen=True, slots=True)
class Team:
    """Fictional employees of UNIT-A plus an approver of UNIT-B."""

    clerk: Principal
    lead: Principal
    approver: Principal
    viewer: Principal
    outsider: Principal


@dataclass(frozen=True, slots=True)
class Desk:
    """Services, reference versions and the team."""

    workflows: WorkflowService
    documents: DocumentService
    reconciliations: ReconciliationService
    users: UserService
    team: Team
    order_version_id: UUID
    policy_version_id: UUID
    rules_version_id: UUID

    def submit(
        self,
        workflow_id: str,
        invoice_id: str,
        lines: list[tuple[str, str, str, str]] = EXACT,
        total: str = "30.00",
        *,
        by: Principal | None = None,
    ) -> str:
        """Upload an invoice, start and check a workflow as a submitter."""
        actor = self.team.clerk if by is None else by
        content = invoice_text(invoice_id, "DEMO-PO-001", lines, total).encode()
        uploaded = self.documents.ingest(content, f"{invoice_id}.txt", actor=actor)
        version = uploaded.extraction.invoice_version
        assert version is not None
        self.workflows.start(
            workflow_id,
            invoice_version_id=version.version_id,
            purchase_order_version_id=self.order_version_id,
            policy_version_id=self.policy_version_id,
            business_unit=UNIT,
            context=context(f"{workflow_id}:start"),
            actor=actor,
        )
        self.workflows.check(
            workflow_id,
            expected_version=1,
            context=context(f"{workflow_id}:check"),
            actor=actor,
        )
        return workflow_id

    def reconcile(self, workflow_id: str) -> UUID:
        """Reconcile as the clerk and return the snapshot ID."""
        return self.reconciliations.reconcile(
            workflow_id, rules_version_id=self.rules_version_id, actor=self.team.clerk
        ).snapshot_id

    def approve(
        self,
        workflow_id: str,
        snapshot_id: UUID,
        actor: Principal,
        *,
        version: int = 2,
        key: str = "approve",
    ) -> CommandResult:
        """Approve with a reason as the given employee."""
        return self.workflows.approve(
            workflow_id,
            expected_version=version,
            reconciliation_snapshot_id=snapshot_id,
            reason=REASON,
            actor=actor,
            context=context(f"{workflow_id}:{key}"),
        )


@pytest.fixture
def desk(
    service: WorkflowService,
    document_service: DocumentService,
    reconciliation_service: ReconciliationService,
    user_service: UserService,
) -> Desk:
    """The demo order DEMO-PO-001, a basic policy, USD rules and a team."""
    _, order = build_demo_documents()
    team = Team(
        clerk=employee(user_service, "clerk-1", "clerk"),
        lead=employee(user_service, "lead-1", "clerk", "approver"),
        approver=employee(user_service, "approver-1", "approver"),
        viewer=employee(user_service, "viewer-1", "viewer"),
        outsider=employee(user_service, "approver-b", "approver", units=("UNIT-B",)),
    )
    return Desk(
        service,
        document_service,
        reconciliation_service,
        user_service,
        team,
        service.record_purchase_order(order).version_id,
        service.record_policy(ValidationPolicy("decisions-test")).version_id,
        reconciliation_service.record_rules(USD_RULES).version_id,
    )


def _counts(engine: Engine) -> dict[str, int]:
    """Count rows in every table to prove a refusal wrote nothing."""
    with engine.connect() as connection:
        return {
            table: connection.execute(
                text(f"SELECT count(*) FROM {table}")
            ).scalar_one()
            for table in TABLES
        }


def test_approval_writes_decision_outbox_and_audit_together(desk: Desk) -> None:
    """The decision cites the snapshot; audit links snapshot and outbox event."""
    workflow_id = desk.submit("WF-1", "INV-1")
    snapshot_id = desk.reconcile(workflow_id)
    snapshot = desk.reconciliations.get_snapshot(snapshot_id)
    result = desk.approve(workflow_id, snapshot_id, desk.team.approver)

    stored = desk.workflows.get_workflow(workflow_id)
    assert stored.workflow.state is WorkflowState.APPROVED
    assert (stored.created_by, stored.decision_reconciliation_id) == (
        "clerk-1",
        snapshot_id,
    )
    assert stored.workflow.decision is not None
    assert stored.workflow.decision.actor_id == "approver-1"
    [entry] = desk.workflows.outbox_events(workflow_id)
    event = entry.event
    assert entry.delivery.status is DeliveryStatus.PENDING
    assert (entry.delivery.attempts, entry.attempts) == (0, ())
    assert event.event_type is OutboxEventType.INVOICE_APPROVED
    assert event.workflow_version == 3 == result.version
    assert event.payload["decided_by"] == "approver-1"
    assert event.payload["business_unit"] == UNIT
    assert event.payload["reconciliation_snapshot_id"] == str(snapshot_id)
    assert event.payload["reconciliation_inputs_hash"] == snapshot.inputs_hash
    audit = desk.workflows.audit_trail(workflow_id)[-1]
    assert (audit.command, audit.actor_id) == (CommandName.APPROVE, "approver-1")
    assert (audit.reconciliation_snapshot_id, audit.outbox_event_id) == (
        snapshot_id,
        event.event_id,
    )
    # A retried request is answered from the stored result: no second event.
    replay = desk.approve(workflow_id, snapshot_id, desk.team.approver)
    assert replay.replayed is True
    assert len(desk.workflows.outbox_events(workflow_id)) == 1


def test_rejection_writes_its_own_outbox_event(desk: Desk) -> None:
    """A rejection needs no reconciliation and announces invoice.rejected."""
    workflow_id = desk.submit("WF-1", "INV-1")
    desk.workflows.reject(
        workflow_id,
        expected_version=2,
        reason="Fictional supplier asked to withdraw",
        actor=desk.team.approver,
        context=context("reject"),
    )
    [entry] = desk.workflows.outbox_events(workflow_id)
    event = entry.event
    assert event.event_type is OutboxEventType.INVOICE_REJECTED
    audit = desk.workflows.audit_trail(workflow_id)[-1]
    assert audit.outbox_event_id == event.event_id
    assert audit.reconciliation_snapshot_id is None


def test_missing_role_other_unit_and_four_eyes_write_nothing(
    engine: Engine, desk: Desk
) -> None:
    """Viewer and clerk lack the role, another unit sees nothing, starter waits."""
    workflow_id = desk.submit("WF-1", "INV-1", by=desk.team.lead)
    snapshot_id = desk.reconcile(workflow_id)
    counts = _counts(engine)
    for actor in (desk.team.viewer, desk.team.clerk):
        with pytest.raises(MissingPermissionError):
            desk.approve(workflow_id, snapshot_id, actor, key=actor.user_id)
    with pytest.raises(NotFoundError):
        desk.approve(workflow_id, snapshot_id, desk.team.outsider, key="outsider")
    with pytest.raises(SelfApprovalError):
        desk.approve(workflow_id, snapshot_id, desk.team.lead, key="lead")
    assert _counts(engine) == counts
    desk.approve(workflow_id, snapshot_id, desk.team.approver)


def test_other_unit_cannot_read_or_act(desk: Desk) -> None:
    """Every query and command answers not found outside the actor's units."""
    workflow_id = desk.submit("WF-1", "INV-1")
    snapshot_id = desk.reconcile(workflow_id)
    outsider = desk.team.outsider
    calls: list[Callable[[], object]] = [
        lambda: desk.workflows.get_workflow(workflow_id, actor=outsider),
        lambda: desk.workflows.audit_trail(workflow_id, actor=outsider),
        lambda: desk.workflows.outbox_events(workflow_id, actor=outsider),
        lambda: desk.reconciliations.get_snapshot(snapshot_id, actor=outsider),
        lambda: desk.reconciliations.latest(workflow_id, actor=outsider),
        lambda: desk.reconciliations.reconcile(
            workflow_id, rules_version_id=desk.rules_version_id, actor=outsider
        ),
        lambda: desk.workflows.reject(
            workflow_id,
            expected_version=2,
            reason="No",
            actor=outsider,
            context=context("outsider-reject"),
        ),
    ]
    for call in calls:
        with pytest.raises(NotFoundError):
            call()
    # A viewer of the right unit can read everything but not reconcile.
    viewer = desk.team.viewer
    assert desk.workflows.get_workflow(workflow_id, actor=viewer).version == 2
    assert desk.reconciliations.latest(workflow_id, actor=viewer).snapshot_id == (
        snapshot_id
    )
    with pytest.raises(MissingPermissionError):
        desk.reconciliations.reconcile(
            workflow_id, rules_version_id=desk.rules_version_id, actor=viewer
        )


def test_submit_permission_guards_intake_and_start(desk: Desk) -> None:
    """Viewers and approvers do not upload; clerks start only in their units."""
    content = invoice_text("INV-X", "DEMO-PO-001", EXACT, "30.00").encode()
    for actor in (desk.team.viewer, desk.team.approver):
        with pytest.raises(MissingPermissionError):
            desk.documents.ingest(content, "x.txt", actor=actor)
    clerk_b = employee(desk.users, "clerk-b", "clerk", units=("UNIT-B",))
    with pytest.raises(MissingPermissionError) as error:
        desk.submit("WF-B", "INV-B", by=clerk_b)
    assert "business unit" in str(error.value)


@pytest.mark.parametrize("change", ["purchase_order", "rules"])
def test_input_change_between_reconciliation_and_approval_is_refused(
    engine: Engine, desk: Desk, change: str
) -> None:
    """A new order or rule version makes the cited snapshot stale."""
    workflow_id = desk.submit("WF-1", "INV-1")
    snapshot_id = desk.reconcile(workflow_id)
    if change == "purchase_order":
        _, order = build_demo_documents()
        desk.workflows.record_purchase_order(
            replace(
                order,
                lines=(
                    order.lines[0],
                    LineItem(*FILTER, Decimal("2"), Money(Decimal("5"), Currency.USD)),
                ),
            )
        )
        expected = ("purchase_order_changed",)
    else:
        desk.reconciliations.record_rules(
            ReconciliationRules(USD_RULES.rules_id, USD_RULES.currency_rules, False)
        )
        expected = ("rules_changed",)
    counts = _counts(engine)
    with pytest.raises(StaleReconciliationError) as error:
        desk.approve(workflow_id, snapshot_id, desk.team.approver)
    assert error.value.reasons == expected
    assert _counts(engine) == counts
    assert desk.workflows.get_workflow(workflow_id).workflow.state is (
        WorkflowState.AWAITING_APPROVAL
    )


def test_unmatched_or_foreign_snapshot_cannot_approve(desk: Desk) -> None:
    """Discrepancies block approval; a snapshot of another workflow is refused."""
    priced = desk.submit(
        "WF-P", "INV-P", [(*SEAL, "2", "12.54"), (*FILTER, "1", "5.10")], "30.18"
    )
    priced_snapshot = desk.reconcile(priced)
    version = desk.workflows.get_workflow(priced).version
    with pytest.raises(ReconciliationNotMatchedError):
        desk.approve(priced, priced_snapshot, desk.team.approver, version=version)
    clean = desk.submit("WF-C", "INV-C")
    with pytest.raises(ReconciliationNotForWorkflowError):
        desk.approve(clean, priced_snapshot, desk.team.approver)
    with pytest.raises(NotFoundError):
        desk.approve(clean, UUID(int=0), desk.team.approver, key="missing")


def test_deactivation_and_revocation_apply_to_the_next_command(desk: Desk) -> None:
    """Rights are re-read in every transaction, not trusted from login."""
    workflow_id = desk.submit("WF-1", "INV-1")
    snapshot_id = desk.reconcile(workflow_id)
    issued = desk.users.issue_token("approver-1", label="pytest")
    signed_in = desk.users.authenticate(issued.secret)
    assert signed_in == desk.team.approver
    desk.users.set_active("approver-1", active=False)
    with pytest.raises(UnauthenticatedError):
        desk.users.authenticate(issued.secret)
    with pytest.raises(UnauthenticatedError):
        desk.approve(workflow_id, snapshot_id, signed_in)
    desk.users.set_active("approver-1", active=True)
    desk.users.revoke_token(issued.token_id)
    with pytest.raises(UnauthenticatedError):
        desk.users.authenticate(issued.secret)
    with pytest.raises(NotFoundError):
        desk.users.revoke_token(issued.token_id)
    other = desk.users.issue_token("approver-1", label="second")
    assert desk.users.authenticate(other.secret) == desk.team.approver


def test_failure_after_outbox_insert_rolls_everything_back(
    engine: Engine, desk: Desk, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If the audit insert fails, the decision and outbox event vanish too."""
    workflow_id = desk.submit("WF-1", "INV-1")
    snapshot_id = desk.reconcile(workflow_id)
    counts = _counts(engine)

    def broken(*_args: object, **_kwargs: object) -> None:
        """Simulate a crash after the outbox row was written."""
        raise RuntimeError("simulated failure")

    monkeypatch.setattr(SqlAlchemyWorkflowRepository, "add_audit_event", broken)
    with pytest.raises(RuntimeError):
        desk.approve(workflow_id, snapshot_id, desk.team.approver)
    monkeypatch.undo()
    assert _counts(engine) == counts
    assert desk.workflows.get_workflow(workflow_id).version == 2
    desk.approve(workflow_id, snapshot_id, desk.team.approver)
    assert len(desk.workflows.outbox_events(workflow_id)) == 1


def _hold(
    engine: Engine, key: str, held: threading.Event, release: threading.Event
) -> None:
    """Hold an advisory transaction lock until told to release it."""
    with engine.begin() as connection:
        connection.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
            {"key": key},
        )
        held.set()
        release.wait(10)


@pytest.mark.parametrize("writer", ["purchase_order", "rules", "start"])
def test_writers_wait_for_the_approval_lock(
    engine: Engine, desk: Desk, writer: str
) -> None:
    """New order or rule versions and duplicate starts wait for an approval.

    The test holds the key an approval would hold and checks that the writer
    cannot commit until it is released, so freshness checked inside an
    approval stays true until that approval commits.
    """
    _, order = build_demo_documents()
    workflow_id = desk.submit("WF-1", "INV-1")
    invoice = desk.workflows.get_workflow(workflow_id).workflow.invoice
    if writer == "purchase_order":
        key = purchase_order_key(order.purchase_order_id)

        def write() -> object:
            """Record the next order version."""
            return desk.workflows.record_purchase_order(
                replace(order, supplier_id="DEMO-SUPPLIER-002")
            )
    elif writer == "rules":
        key = rules_key(USD_RULES.rules_id)

        def write() -> object:
            """Record the next rule version."""
            return desk.reconciliations.record_rules(
                ReconciliationRules(USD_RULES.rules_id, USD_RULES.currency_rules, False)
            )
    else:
        key = invoice_number_key(invoice.supplier_id, invoice.invoice_id)
        stored = desk.workflows.get_workflow(workflow_id)

        def write() -> object:
            """Start a duplicate workflow for the same invoice number."""
            return desk.workflows.start(
                "WF-DUPLICATE",
                invoice_version_id=stored.invoice_version.version_id,
                purchase_order_version_id=desk.order_version_id,
                policy_version_id=desk.policy_version_id,
                business_unit=UNIT,
                context=context("dup-start"),
            )

    held, release = threading.Event(), threading.Event()
    with ThreadPoolExecutor(max_workers=2) as pool:
        holder = pool.submit(_hold, engine, key, held, release)
        assert held.wait(5)
        pending: Future[object] = pool.submit(write)
        with pytest.raises(TimeoutError):
            pending.result(timeout=0.5)
        release.set()
        holder.result(timeout=5)
        assert pending.result(timeout=5) is not None
