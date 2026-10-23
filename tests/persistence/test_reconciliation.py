"""Stored reconciliations: sources, reuse, staleness, reproduction, duplicates."""

import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from decimal import Decimal
from uuid import UUID

import pytest
from sqlalchemy import Engine, text

from app.application.documents import DocumentService
from app.application.errors import StoredDataIntegrityError
from app.application.ports import StoredReconciliation, VersionRef
from app.application.reconciliation import ReconciliationService, StaleReason
from app.application.users import UserService
from app.application.workflows import WorkflowService
from app.demo import build_demo_documents
from app.domain.models import LineItem
from app.domain.money import Currency, Money
from app.domain.reconciliation import (
    CurrencyRule,
    DiscrepancyCode,
    ReconciliationRules,
    ReconciliationStatus,
)
from app.domain.rules import ValidationPolicy
from app.samples import invoice_text
from tests.persistence.conftest import TABLES, UNIT, USD_RULES, context, employee

pytestmark = pytest.mark.postgres

SEAL = ("DEMO-SEAL", "Fictional seal kit")
FILTER = ("DEMO-FILTER", "Fictional filter")
EXACT = [(*SEAL, "2", "12.50"), (*FILTER, "1", "5.00")]


@dataclass(frozen=True, slots=True)
class Setup:
    """Recorded order, policy and rules plus the services."""

    workflows: WorkflowService
    documents: DocumentService
    service: ReconciliationService
    order_version: VersionRef
    policy_version: VersionRef
    rules_version: VersionRef

    def start(
        self,
        workflow_id: str,
        invoice_id: str,
        lines: list[tuple[str, str, str, str]],
        total: str,
    ) -> str:
        """Ingest an invoice text and start a workflow on it."""
        text_bytes = invoice_text(invoice_id, "DEMO-PO-001", lines, total).encode()
        version = self.documents.ingest(text_bytes, f"{workflow_id}.txt")
        invoice_version = version.extraction.invoice_version
        assert invoice_version is not None
        self.workflows.start(
            workflow_id,
            invoice_version_id=invoice_version.version_id,
            purchase_order_version_id=self.order_version.version_id,
            policy_version_id=self.policy_version.version_id,
            business_unit=UNIT,
            context=context(f"{workflow_id}:start"),
        )
        return workflow_id

    def reconcile(self, workflow_id: str) -> StoredReconciliation:
        """Reconcile with the recorded rules."""
        return self.service.reconcile(
            workflow_id, rules_version_id=self.rules_version.version_id
        )


def _counts(engine: Engine) -> dict[str, int]:
    """Count rows in every table."""
    with engine.connect() as connection:
        return {
            table: connection.execute(
                text(f"SELECT count(*) FROM {table}")
            ).scalar_one()
            for table in TABLES
        }


@pytest.fixture
def setup(
    service: WorkflowService,
    document_service: DocumentService,
    reconciliation_service: ReconciliationService,
) -> Setup:
    """The demo order DEMO-PO-001, a basic policy and USD rules."""
    _, order = build_demo_documents()
    return Setup(
        service,
        document_service,
        reconciliation_service,
        service.record_purchase_order(order),
        service.record_policy(ValidationPolicy("recon-test")),
        reconciliation_service.record_rules(USD_RULES),
    )


def test_matched_snapshot_pins_every_input(setup: Setup) -> None:
    """Document, extraction, order and rules are recorded with their hashes."""
    workflow_id = setup.start("WF-R1", "INV-R1", EXACT, "30.00")
    snapshot = setup.reconcile(workflow_id)
    assert snapshot.result.status is ReconciliationStatus.MATCHED
    inputs = snapshot.inputs
    assert inputs.purchase_order_version == setup.order_version
    assert inputs.rules_version == setup.rules_version
    assert inputs.source_document_id is not None and inputs.extraction_id is not None
    assert inputs.fields_hash is not None and len(inputs.fields_hash) == 64
    assert setup.service.get_snapshot(snapshot.snapshot_id) == snapshot
    report = setup.service.report(snapshot.snapshot_id)
    assert report.freshness.is_current and report.reproducible


def test_identical_inputs_reuse_the_snapshot(engine: Engine, setup: Setup) -> None:
    """Reconciling again with unchanged inputs writes nothing new."""
    workflow_id = setup.start("WF-R1", "INV-R1", EXACT, "30.00")
    first = setup.reconcile(workflow_id)
    counts = _counts(engine)
    assert setup.reconcile(workflow_id) == first
    assert _counts(engine) == counts


def test_discrepancies_quote_their_source(setup: Setup) -> None:
    """The report shows the page, line and exact text behind each problem."""
    lines = [(*SEAL, "2", "12.54"), (*FILTER, "1", "5.10")]
    snapshot = setup.reconcile(setup.start("WF-R2", "INV-R2", lines, "30.18"))
    report = setup.service.report(snapshot.snapshot_id)
    assert [(d.code, d.field) for d in snapshot.result.discrepancies] == [
        (DiscrepancyCode.UNIT_PRICE_MISMATCH, "lines[2].unit_price"),
        (DiscrepancyCode.EXPECTED_TOTAL_MISMATCH, "matched_lines_total"),
    ]
    [price] = report.citations["lines[2].unit_price"]
    assert (price.page, price.line_number, price.quote) == (1, 7, "5.10")
    assert price.line == "Line: DEMO-FILTER | Fictional filter | 1 | 5.10"
    assert [c.quote for c in report.citations["matched_lines_total"]] == [
        "12.54",
        "5.10",
    ]


def test_order_and_rule_changes_make_a_snapshot_stale(setup: Setup) -> None:
    """New versions mark it stale; the old snapshot still reproduces exactly."""
    workflow_id = setup.start("WF-R1", "INV-R1", EXACT, "30.00")
    snapshot = setup.reconcile(workflow_id)
    _, order = build_demo_documents()
    bigger = replace(
        order,
        lines=(replace(order.lines[0], quantity=Decimal("3")), order.lines[1]),
    )
    setup.workflows.record_purchase_order(bigger)
    assert setup.service.freshness(snapshot.snapshot_id).reasons == (
        StaleReason.PURCHASE_ORDER_CHANGED,
    )
    looser = ReconciliationRules(
        USD_RULES.rules_id,
        (
            CurrencyRule(
                Currency.USD,
                Money(Decimal("0.05"), Currency.USD),
                Money(Decimal("0.20"), Currency.USD),
            ),
        ),
    )
    setup.service.record_rules(looser)
    assert setup.service.freshness(snapshot.snapshot_id).reasons == (
        StaleReason.PURCHASE_ORDER_CHANGED,
        StaleReason.RULES_CHANGED,
    )
    assert setup.service.reproduce(snapshot.snapshot_id) == snapshot.result
    assert setup.service.get_snapshot(snapshot.snapshot_id) == snapshot


def test_revise_and_reconcile_again_gives_a_current_snapshot(setup: Setup) -> None:
    """After pinning the new order the workflow gets a fresh, separate snapshot."""
    workflow_id = setup.start("WF-R1", "INV-R1", EXACT, "30.00")
    old = setup.reconcile(workflow_id)
    _, order = build_demo_documents()
    order_v2 = setup.workflows.record_purchase_order(
        replace(
            order,
            lines=(
                *order.lines,
                LineItem(
                    "DEMO-NUT",
                    "Nut",
                    Decimal("1"),
                    Money(Decimal("1.00"), Currency.USD),
                ),
            ),
        )
    )
    setup.workflows.revise(
        workflow_id,
        expected_version=1,
        invoice_version_id=old.inputs.invoice_version.version_id,
        purchase_order_version_id=order_v2.version_id,
        policy_version_id=setup.policy_version.version_id,
        context=context("revise"),
    )
    new = setup.reconcile(workflow_id)
    assert new.snapshot_id != old.snapshot_id
    assert new.inputs.purchase_order_version == order_v2
    assert setup.service.freshness(new.snapshot_id).is_current
    assert setup.service.latest(workflow_id).snapshot_id == new.snapshot_id
    assert (
        StaleReason.WORKFLOW_INPUTS_CHANGED
        in setup.service.freshness(old.snapshot_id).reasons
    )


def test_duplicate_invoice_and_rejection(
    setup: Setup, user_service: UserService
) -> None:
    """A second workflow for the same supplier invoice is flagged until rejected."""
    first = setup.start("WF-A", "INV-DUP", EXACT, "30.00")
    snapshot_a = setup.reconcile(first)
    second = setup.start("WF-B", "INV-DUP", EXACT, "30.00")
    snapshot_b = setup.reconcile(second)
    [duplicate] = snapshot_b.result.discrepancies
    assert (duplicate.code, duplicate.actual) == (
        DiscrepancyCode.DUPLICATE_INVOICE,
        "WF-A",
    )
    assert setup.service.freshness(snapshot_a.snapshot_id).reasons == (
        StaleReason.DUPLICATES_CHANGED,
    )
    setup.workflows.reject(
        second,
        expected_version=1,
        reason="Duplicate",
        actor=employee(user_service, "approver-1", "approver"),
        context=context("reject-b"),
    )
    assert setup.service.freshness(snapshot_a.snapshot_id).is_current


def test_invoice_without_document_or_order(
    setup: Setup, service: WorkflowService
) -> None:
    """Code-created invoices lack a source; a missing order blocks comparison."""
    invoice, _ = build_demo_documents()
    invoice = replace(invoice, invoice_id="INV-NODOC", purchase_order_id="PO-LATER")
    version = service.record_invoice(invoice)
    service.start(
        "WF-NODOC",
        invoice_version_id=version.version_id,
        purchase_order_version_id=None,
        policy_version_id=setup.policy_version.version_id,
        business_unit=UNIT,
        context=context("nodoc"),
    )
    snapshot = setup.reconcile("WF-NODOC")
    assert [d.code for d in snapshot.result.discrepancies] == [
        DiscrepancyCode.MISSING_SOURCE_DOCUMENT,
        DiscrepancyCode.MISSING_PURCHASE_ORDER,
    ]
    assert snapshot.inputs.source_document_id is None
    _, order = build_demo_documents()
    service.record_purchase_order(replace(order, purchase_order_id="PO-LATER"))
    assert setup.service.freshness(snapshot.snapshot_id).reasons == (
        StaleReason.PURCHASE_ORDER_AVAILABLE,
    )


def test_edited_rules_cannot_reproduce(engine: Engine, setup: Setup) -> None:
    """A tolerance changed behind the application's back fails its hash check."""
    snapshot = setup.reconcile(setup.start("WF-R1", "INV-R1", EXACT, "30.00"))
    with engine.begin() as connection:
        connection.execute(
            text(
                "ALTER TABLE reconciliation_currency_rules "
                "DISABLE TRIGGER trg_reconciliation_currency_rules_append_only"
            )
        )
        connection.execute(
            text("UPDATE reconciliation_currency_rules SET total_tolerance = 9.99")
        )
        connection.execute(
            text(
                "ALTER TABLE reconciliation_currency_rules "
                "ENABLE TRIGGER trg_reconciliation_currency_rules_append_only"
            )
        )
    with pytest.raises(StoredDataIntegrityError, match="hash"):
        setup.service.reproduce(snapshot.snapshot_id)


def test_concurrent_identical_reconciliations_store_one_snapshot(
    engine: Engine, setup: Setup
) -> None:
    """Racing requests with equal inputs converge on one stored snapshot."""
    workflow_id = setup.start("WF-R1", "INV-R1", EXACT, "30.00")
    barrier = threading.Barrier(3)

    def run() -> UUID:
        """Start all reconciliations together."""
        barrier.wait()
        return setup.reconcile(workflow_id).snapshot_id

    with ThreadPoolExecutor(3) as pool:
        ids = {snapshot_id for snapshot_id in pool.map(lambda _: run(), range(3))}
    assert len(ids) == 1
    assert _counts(engine)["reconciliation_snapshots"] == 1
