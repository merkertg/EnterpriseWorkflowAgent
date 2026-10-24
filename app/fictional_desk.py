"""A fictional desk for demos and evaluations: employees, reference data, steps.

Every run gets a random suffix, so repeated runs against the same database
never collide. Employees sign in with freshly issued tokens, exactly like the
API does; the tokens never leave this object.
"""

import threading
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import Engine

from app.application.access import Principal
from app.application.documents import DocumentService
from app.application.ports import CommandContext, CommandResult, VersionRef
from app.application.reconciliation import ReconciliationService
from app.application.users import UserService
from app.application.workflows import WorkflowService
from app.domain.models import LineItem, PurchaseOrder
from app.domain.money import Currency, Money
from app.domain.reconciliation import ReconciliationRules
from app.domain.rules import ValidationPolicy
from app.infrastructure.db.session import (
    build_document_service,
    build_reconciliation_service,
    build_user_service,
    build_workflow_service,
)
from app.reconciliation_cli import currency_rule
from app.samples import invoice_text

UNIT = "DEMO-UNIT-A"
OTHER_UNIT = "DEMO-UNIT-B"
SEAL = ("DEMO-SEAL", "Fictional seal kit")
FILTER = ("DEMO-FILTER", "Fictional filter")
# Invoice lines that match the desk's order exactly: 2 × 12.50 + 1 × 5.00.
CLEAN_LINES = ((*SEAL, "2", "12.50"), (*FILTER, "1", "5.00"))


class ShiftedClock:
    """Real time plus an offset that demos advance instead of sleeping.

    The delivery worker and the sandbox receiver share one instance, so a
    retry becomes due and its signature stays within the receiver's window.
    """

    def __init__(self) -> None:
        """Start at the real time."""
        self._offset = timedelta()
        self._lock = threading.Lock()

    def now(self) -> datetime:
        """The shifted current time."""
        with self._lock:
            return datetime.now(UTC) + self._offset

    def advance(self, seconds: float) -> None:
        """Move time forward, e.g. past a retry delay."""
        with self._lock:
            self._offset += timedelta(seconds=seconds)


@dataclass(frozen=True, slots=True)
class Team:
    """Signed-in employees of the desk."""

    clerk: Principal
    approver: Principal
    viewer: Principal
    outsider: Principal


class FictionalDesk:
    """Services over one database plus a team and reference data."""

    def __init__(self, engine: Engine, source: str) -> None:
        """Create employees, an order, a policy and reconciliation rules."""
        self.engine = engine
        self.source = source
        self.run = uuid4().hex[:8].upper()
        self.users: UserService = build_user_service(engine)
        self.documents: DocumentService = build_document_service(engine)
        self.workflows: WorkflowService = build_workflow_service(engine)
        self.reconciliations: ReconciliationService = build_reconciliation_service(
            engine
        )
        self.team = Team(
            clerk=self._employee("clerk", "clerk", UNIT),
            approver=self._employee("approver", "approver", UNIT),
            viewer=self._employee("viewer", "viewer", UNIT),
            outsider=self._employee("outsider", "approver", OTHER_UNIT),
        )
        self.order_id = f"DEMO-PO-{self.run}"
        self.order: VersionRef = self.workflows.record_purchase_order(
            PurchaseOrder(
                self.order_id,
                "DEMO-SUPPLIER-001",
                Currency.USD,
                (
                    LineItem(
                        *SEAL, Decimal("2"), Money(Decimal("12.50"), Currency.USD)
                    ),
                    LineItem(
                        *FILTER, Decimal("1"), Money(Decimal("5.00"), Currency.USD)
                    ),
                ),
            )
        )
        self.policy: VersionRef = self.workflows.record_policy(
            ValidationPolicy(f"demo-policy-{self.run.lower()}")
        )
        self.rules: VersionRef = self.reconciliations.record_rules(
            ReconciliationRules(
                f"demo-rules-{self.run.lower()}",
                (currency_rule(Currency.USD, "0.05", "0.10"),),
            )
        )

    def _employee(self, name: str, role: str, unit: str) -> Principal:
        """Create an employee, issue a token and sign in with it."""
        user_id = f"{self.source}-{name}-{self.run.lower()}"
        self.users.create_user(
            user_id, display_name=f"Fictional {name}", roles=(role,), units=(unit,)
        )
        token = self.users.issue_token(user_id, label=self.source).secret
        return self.users.authenticate(token)

    def context(self, key: str) -> CommandContext:
        """One idempotency key per intent, unique to this run."""
        return CommandContext(f"{self.run}:{key}", self.source)

    def invoice(
        self,
        name: str,
        lines: Sequence[tuple[str, str, str, str]] = CLEAN_LINES,
        total: str = "30.00",
    ) -> str:
        """The text of a fictional invoice for this desk's order."""
        return invoice_text(f"DEMO-INV-{self.run}-{name}", self.order_id, lines, total)

    def submit_text(self, name: str, text: str) -> str:
        """The clerk uploads a text, starts a workflow and runs the basic check."""
        uploaded = self.documents.ingest(
            text.encode("utf-8"), f"{name}.txt", actor=self.team.clerk
        )
        version = uploaded.extraction.invoice_version
        if version is None:
            raise RuntimeError("This step needs a completely extracted invoice.")
        workflow_id = f"DEMO-WF-{self.run}-{name}"
        self.workflows.start(
            workflow_id,
            invoice_version_id=version.version_id,
            purchase_order_version_id=self.order.version_id,
            policy_version_id=self.policy.version_id,
            business_unit=UNIT,
            context=self.context(f"{name}:start"),
            actor=self.team.clerk,
        )
        self.workflows.check(
            workflow_id,
            expected_version=1,
            context=self.context(f"{name}:check"),
            actor=self.team.clerk,
        )
        return workflow_id

    def submit(
        self,
        name: str,
        lines: Sequence[tuple[str, str, str, str]] = CLEAN_LINES,
        total: str = "30.00",
    ) -> str:
        """Upload, start and check a generated invoice."""
        return self.submit_text(name, self.invoice(name, lines, total))

    def reconcile(self, workflow_id: str) -> UUID:
        """The clerk reconciles with the desk's rules; returns the snapshot ID."""
        report = self.reconciliations.reconcile(
            workflow_id, rules_version_id=self.rules.version_id, actor=self.team.clerk
        )
        return report.snapshot_id

    def approve(
        self, workflow_id: str, snapshot_id: UUID, *, key: str, version: int = 2
    ) -> CommandResult:
        """The approver approves on a reconciliation snapshot."""
        return self.workflows.approve(
            workflow_id,
            expected_version=version,
            reconciliation_snapshot_id=snapshot_id,
            reason="Fictional invoice matches the order",
            actor=self.team.approver,
            context=self.context(key),
        )

    def approved(self, name: str) -> str:
        """A clean invoice taken all the way to approval; returns its workflow."""
        workflow_id = self.submit(name)
        self.approve(workflow_id, self.reconcile(workflow_id), key=f"{name}:approve")
        return workflow_id
