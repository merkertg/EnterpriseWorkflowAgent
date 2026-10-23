"""SQLAlchemy storage for reconciliation rule versions and snapshots.

Rule sets are re-hashed on load. Snapshots store their result as JSON with
amounts as decimal strings, so reading one back gives equal Decimal values.
"""

from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.application.errors import StoredDataIntegrityError
from app.application.ports import (
    ReconciliationInputs,
    StoredReconciliation,
    Versioned,
    VersionRef,
)
from app.domain.content_hash import content_hash
from app.domain.extraction import Evidence
from app.domain.money import Currency, Money
from app.domain.reconciliation import (
    CurrencyRule,
    Discrepancy,
    DiscrepancyCode,
    LineMatch,
    ReconciliationResult,
    ReconciliationRules,
    ReconciliationStatus,
)
from app.domain.workflow import WorkflowState
from app.infrastructure.db.inserts import insert_rows
from app.infrastructure.db.models import (
    InvoiceVersionRow,
    PolicyVersionRow,
    PurchaseOrderVersionRow,
    ReconciliationCurrencyRuleRow,
    ReconciliationRuleVersionRow,
    ReconciliationSnapshotRow,
    WorkflowRow,
)
from app.infrastructure.db.repository import reconstructing

type VersionRow = (
    InvoiceVersionRow
    | PurchaseOrderVersionRow
    | PolicyVersionRow
    | ReconciliationRuleVersionRow
)


def _amount(value: Money | None) -> str | None:
    """Store an amount as an exact decimal string."""
    return None if value is None else str(value.amount)


def _money(value: str | None, currency: Currency) -> Money | None:
    """Read an amount stored as a decimal string."""
    return None if value is None else Money(Decimal(value), currency)


def _required_money(value: str, currency: Currency) -> Money:
    """Read a mandatory amount."""
    return Money(Decimal(value), currency)


def _discrepancy_data(item: Discrepancy) -> dict[str, Any]:
    """Serialize a discrepancy with its source spans."""
    return {
        "code": item.code.value,
        "field": item.field,
        "message": item.message,
        "expected": item.expected,
        "actual": item.actual,
        "rule": item.rule,
        "sources": [[e.page, e.start, e.end] for e in item.sources],
    }


def _line_data(item: LineMatch) -> dict[str, Any]:
    """Serialize one line comparison with exact decimal strings."""
    return {
        "invoice_line": item.invoice_line,
        "sku": item.sku,
        "order_line": item.order_line,
        "invoiced_quantity": str(item.invoiced_quantity),
        "ordered_quantity": None
        if item.ordered_quantity is None
        else str(item.ordered_quantity),
        "invoice_unit_price": str(item.invoice_unit_price.amount),
        "order_unit_price": _amount(item.order_unit_price),
        "invoice_line_total": str(item.invoice_line_total.amount),
        "expected_line_total": _amount(item.expected_line_total),
    }


def _ref(row: VersionRow | None, business_id: str | None, label: str) -> VersionRef:
    """Build a version reference from a row a foreign key points to."""
    if row is None or business_id is None:
        raise StoredDataIntegrityError(f"Referenced {label} is missing.")
    return VersionRef(row.id, business_id, row.version_no, row.content_hash)


class SqlAlchemyReconciliationRepository:
    """Reconciliation storage bound to the unit of work's session."""

    def __init__(self, session: Session) -> None:
        """Use the shared session; this class never commits."""
        self._session = session

    # Rule versions -------------------------------------------------------------

    def latest_rules_version(self, rules_id: str) -> VersionRef | None:
        """Return the highest stored version number of a rule set."""
        row = self._session.scalars(
            select(ReconciliationRuleVersionRow)
            .where(ReconciliationRuleVersionRow.rules_id == rules_id)
            .order_by(ReconciliationRuleVersionRow.version_no.desc())
            .limit(1)
        ).first()
        if row is None:
            return None
        return VersionRef(row.id, rules_id, row.version_no, row.content_hash)

    def add_rules_version(self, version: Versioned[ReconciliationRules]) -> None:
        """Insert the header first so a version-number race is detected early."""
        ref, rules = version.ref, version.value
        insert_rows(
            self._session,
            ReconciliationRuleVersionRow(
                id=ref.version_id,
                rules_id=rules.rules_id,
                version_no=ref.version_no,
                content_hash=ref.content_hash,
                allow_partial_quantities=rules.allow_partial_quantities,
                require_source_document=rules.require_source_document,
            ),
        )
        if rules.currency_rules:
            insert_rows(
                self._session,
                *(
                    ReconciliationCurrencyRuleRow(
                        rules_version_id=ref.version_id,
                        currency=rule.currency.value,
                        unit_price_tolerance=rule.unit_price_tolerance.amount,
                        total_tolerance=rule.total_tolerance.amount,
                        decimal_places=rule.decimal_places,
                        rounding=rule.rounding,
                    )
                    for rule in rules.currency_rules
                ),
            )

    def get_rules_version(
        self, version_id: UUID
    ) -> Versioned[ReconciliationRules] | None:
        """Rebuild a rule set and verify that it still matches its hash."""
        row = self._session.get(ReconciliationRuleVersionRow, version_id)
        if row is None:
            return None
        currency_rows = self._session.scalars(
            select(ReconciliationCurrencyRuleRow).where(
                ReconciliationCurrencyRuleRow.rules_version_id == version_id
            )
        ).all()
        with reconstructing("reconciliation rules"):
            rules = ReconciliationRules(
                rules_id=row.rules_id,
                currency_rules=tuple(
                    CurrencyRule(
                        currency=Currency(item.currency),
                        unit_price_tolerance=Money(
                            item.unit_price_tolerance, Currency(item.currency)
                        ),
                        total_tolerance=Money(
                            item.total_tolerance, Currency(item.currency)
                        ),
                        decimal_places=item.decimal_places,
                        rounding=item.rounding,
                    )
                    for item in currency_rows
                ),
                allow_partial_quantities=row.allow_partial_quantities,
                require_source_document=row.require_source_document,
            )
        if content_hash(rules) != row.content_hash:
            raise StoredDataIntegrityError("Stored rules do not match their hash.")
        return Versioned(
            VersionRef(row.id, row.rules_id, row.version_no, row.content_hash), rules
        )

    # Duplicates ----------------------------------------------------------------

    def duplicate_workflow_ids(
        self, supplier_id: str, invoice_id: str, exclude_workflow_id: str
    ) -> tuple[str, ...]:
        """Find other open or approved workflows for the same supplier invoice."""
        rows = self._session.scalars(
            select(WorkflowRow.workflow_id)
            .join(
                InvoiceVersionRow,
                WorkflowRow.invoice_version_id == InvoiceVersionRow.id,
            )
            .where(
                InvoiceVersionRow.supplier_id == supplier_id,
                InvoiceVersionRow.invoice_id == invoice_id,
                WorkflowRow.workflow_id != exclude_workflow_id,
                WorkflowRow.state != WorkflowState.REJECTED.value,
            )
            .order_by(WorkflowRow.workflow_id)
        ).all()
        return tuple(rows)

    # Snapshots -----------------------------------------------------------------

    def _snapshot(self, row: ReconciliationSnapshotRow) -> StoredReconciliation:
        """Rebuild a stored snapshot exactly as it was written."""
        invoice = self._session.get(InvoiceVersionRow, row.invoice_version_id)
        order = (
            None
            if row.purchase_order_version_id is None
            else self._session.get(
                PurchaseOrderVersionRow, row.purchase_order_version_id
            )
        )
        rules = self._session.get(ReconciliationRuleVersionRow, row.rules_version_id)
        inputs = ReconciliationInputs(
            invoice_version=_ref(
                invoice, None if invoice is None else invoice.invoice_id, "invoice"
            ),
            purchase_order_version=None
            if row.purchase_order_version_id is None
            else _ref(
                order,
                None if order is None else order.purchase_order_id,
                "purchase order",
            ),
            rules_version=_ref(
                rules, None if rules is None else rules.rules_id, "rules"
            ),
            source_document_id=row.source_document_id,
            document_content_hash=row.document_content_hash,
            extraction_id=row.extraction_id,
            fields_hash=row.fields_hash,
            duplicate_workflow_ids=tuple(row.duplicate_workflow_ids),
        )
        if (
            inputs.invoice_version.content_hash != row.invoice_content_hash
            or inputs.rules_version.content_hash != row.rules_content_hash
            or (
                None
                if inputs.purchase_order_version is None
                else inputs.purchase_order_version.content_hash
            )
            != row.purchase_order_content_hash
        ):
            raise StoredDataIntegrityError("Snapshot hashes disagree with versions.")
        with reconstructing("reconciliation snapshot"):
            currency = Currency(row.currency)
            result = ReconciliationResult(
                status=ReconciliationStatus(row.status),
                currency=currency,
                calculated_total=Money(row.calculated_total, currency),
                expected_total=None
                if row.expected_total is None
                else Money(row.expected_total, currency),
                order_total=None
                if row.order_total is None
                else Money(row.order_total, currency),
                lines=tuple(
                    LineMatch(
                        invoice_line=item["invoice_line"],
                        sku=item["sku"],
                        order_line=item["order_line"],
                        invoiced_quantity=Decimal(item["invoiced_quantity"]),
                        ordered_quantity=None
                        if item["ordered_quantity"] is None
                        else Decimal(item["ordered_quantity"]),
                        invoice_unit_price=_required_money(
                            item["invoice_unit_price"], currency
                        ),
                        order_unit_price=_money(item["order_unit_price"], currency),
                        invoice_line_total=_required_money(
                            item["invoice_line_total"], currency
                        ),
                        expected_line_total=_money(
                            item["expected_line_total"], currency
                        ),
                    )
                    for item in row.lines
                ),
                discrepancies=tuple(
                    Discrepancy(
                        code=DiscrepancyCode(item["code"]),
                        field=item["field"],
                        message=item["message"],
                        expected=item["expected"],
                        actual=item["actual"],
                        rule=item["rule"],
                        sources=tuple(
                            Evidence(page, start, end)
                            for page, start, end in item["sources"]
                        ),
                    )
                    for item in row.discrepancies
                ),
            )
        return StoredReconciliation(
            snapshot_id=row.id,
            workflow_id=row.workflow_id,
            workflow_version=row.workflow_version,
            engine=row.engine,
            inputs_hash=row.inputs_hash,
            inputs=inputs,
            result=result,
            created_at=row.created_at,
        )

    def find_snapshot(
        self, workflow_id: str, inputs_hash: str
    ) -> StoredReconciliation | None:
        """Return a snapshot computed from identical inputs, if any."""
        row = self._session.scalars(
            select(ReconciliationSnapshotRow).where(
                ReconciliationSnapshotRow.workflow_id == workflow_id,
                ReconciliationSnapshotRow.inputs_hash == inputs_hash,
            )
        ).first()
        return None if row is None else self._snapshot(row)

    def add_snapshot(self, snapshot: StoredReconciliation) -> None:
        """Insert an immutable snapshot with copies of every input hash."""
        inputs, result = snapshot.inputs, snapshot.result
        order = inputs.purchase_order_version
        insert_rows(
            self._session,
            ReconciliationSnapshotRow(
                id=snapshot.snapshot_id,
                workflow_id=snapshot.workflow_id,
                workflow_version=snapshot.workflow_version,
                inputs_hash=snapshot.inputs_hash,
                engine=snapshot.engine,
                invoice_version_id=inputs.invoice_version.version_id,
                invoice_content_hash=inputs.invoice_version.content_hash,
                purchase_order_version_id=None if order is None else order.version_id,
                purchase_order_content_hash=None
                if order is None
                else order.content_hash,
                rules_version_id=inputs.rules_version.version_id,
                rules_content_hash=inputs.rules_version.content_hash,
                source_document_id=inputs.source_document_id,
                document_content_hash=inputs.document_content_hash,
                extraction_id=inputs.extraction_id,
                fields_hash=inputs.fields_hash,
                duplicate_workflow_ids=list(inputs.duplicate_workflow_ids),
                status=result.status.value,
                currency=result.currency.value,
                calculated_total=result.calculated_total.amount,
                expected_total=None
                if result.expected_total is None
                else result.expected_total.amount,
                order_total=None
                if result.order_total is None
                else result.order_total.amount,
                discrepancies=[_discrepancy_data(d) for d in result.discrepancies],
                lines=[_line_data(line) for line in result.lines],
            ),
        )

    def get_snapshot(self, snapshot_id: UUID) -> StoredReconciliation | None:
        """Load a snapshot by ID."""
        row = self._session.get(ReconciliationSnapshotRow, snapshot_id)
        return None if row is None else self._snapshot(row)

    def latest_snapshot(self, workflow_id: str) -> StoredReconciliation | None:
        """Return the newest snapshot of a workflow; ties break by ID."""
        row = self._session.scalars(
            select(ReconciliationSnapshotRow)
            .where(ReconciliationSnapshotRow.workflow_id == workflow_id)
            .order_by(
                ReconciliationSnapshotRow.created_at.desc(),
                ReconciliationSnapshotRow.id,
            )
            .limit(1)
        ).first()
        return None if row is None else self._snapshot(row)
