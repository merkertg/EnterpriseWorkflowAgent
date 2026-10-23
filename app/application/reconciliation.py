"""Reconciliation use cases: versioned rules, immutable snapshots and staleness.

A snapshot pins the workflow's invoice and order versions, a rule version,
the source document and extracted fields, and the duplicate context. Equal
inputs return the existing snapshot. Later changes to the applicable order or
rules mark it stale; the stored inputs can always recompute the same result.
"""

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from uuid import UUID, uuid4

from app.application.access import Permission, Principal
from app.application.documents import Citation, cite
from app.application.errors import (
    ConcurrentWriteError,
    NotFoundError,
    StoredDataIntegrityError,
    VersionConflictError,
)
from app.application.ports import (
    ReconciliationInputs,
    StoredExtraction,
    StoredReconciliation,
    UnitOfWork,
    Versioned,
    VersionRef,
)
from app.application.users import current_principal
from app.application.versions import append_version, rules_key
from app.domain.content_hash import content_hash, extraction_hash, sha256_hex
from app.domain.errors import DomainValidationError
from app.domain.models import Invoice, PurchaseOrder, validate_text
from app.domain.reconciliation import (
    RECONCILIATION_ENGINE,
    ReconciliationResult,
    ReconciliationRules,
    reconcile,
    sources_from_extraction,
)

_ATTEMPTS = 3


class StaleReason(StrEnum):
    """Why a stored reconciliation no longer describes the applicable inputs."""

    WORKFLOW_INPUTS_CHANGED = "workflow_inputs_changed"
    PURCHASE_ORDER_CHANGED = "purchase_order_changed"
    PURCHASE_ORDER_AVAILABLE = "purchase_order_available"
    RULES_CHANGED = "rules_changed"
    DUPLICATES_CHANGED = "duplicates_changed"


@dataclass(frozen=True, slots=True)
class Freshness:
    """Current or stale, with every reason found."""

    reasons: tuple[StaleReason, ...]

    @property
    def is_current(self) -> bool:
        """A snapshot is current only when nothing it used has changed."""
        return not self.reasons


@dataclass(frozen=True, slots=True)
class ReconciliationReport:
    """A snapshot with freshness, a reproduction check and quoted sources."""

    snapshot: StoredReconciliation
    freshness: Freshness
    reproducible: bool
    citations: dict[str, tuple[Citation, ...]]


def inputs_hash(inputs: ReconciliationInputs) -> str:
    """Fingerprint every input, including version IDs and the engine version."""

    def ref(value: VersionRef | None) -> list[str] | None:
        """Represent a version as [id, hash]."""
        return None if value is None else [str(value.version_id), value.content_hash]

    return sha256_hex(
        {
            "engine": RECONCILIATION_ENGINE,
            "invoice": ref(inputs.invoice_version),
            "purchase_order": ref(inputs.purchase_order_version),
            "rules": ref(inputs.rules_version),
            "document": None
            if inputs.source_document_id is None
            else [str(inputs.source_document_id), inputs.document_content_hash],
            "extraction": None
            if inputs.extraction_id is None
            else [str(inputs.extraction_id), inputs.fields_hash],
            "duplicates": sorted(inputs.duplicate_workflow_ids),
        }
    )


def _compute(
    invoice: Invoice,
    purchase_order: PurchaseOrder | None,
    rules: ReconciliationRules,
    extraction: StoredExtraction | None,
    duplicates: tuple[str, ...],
) -> ReconciliationResult:
    """Run the pure domain comparison with sources from the extraction."""
    return reconcile(
        invoice,
        purchase_order,
        rules,
        has_source_document=extraction is not None,
        sources=sources_from_extraction(
            None if extraction is None else extraction.result
        ),
        duplicate_workflow_ids=duplicates,
    )


def _require_uuid(value: object, field: str) -> None:
    """Reject a non-UUID identifier before opening a transaction."""
    if type(value) is not UUID:
        raise DomainValidationError("invalid_type", field, f"{field} must be a UUID.")


def compute_freshness(unit: UnitOfWork, snapshot: StoredReconciliation) -> Freshness:
    """Compare a snapshot with current inputs inside the caller's transaction.

    Approval calls this after locking the order, rules and invoice-number keys,
    so no new version can appear between this check and the commit.
    """
    inputs = snapshot.inputs
    reasons: list[StaleReason] = []
    workflow = unit.repository.get_workflow(snapshot.workflow_id)
    invoice = unit.repository.get_invoice_version(inputs.invoice_version.version_id)
    if workflow is None or invoice is None:
        raise StoredDataIntegrityError("Snapshot inputs are missing.")
    if (
        workflow.invoice_version != inputs.invoice_version
        or workflow.purchase_order_version != inputs.purchase_order_version
    ):
        reasons.append(StaleReason.WORKFLOW_INPUTS_CHANGED)
    pinned = inputs.purchase_order_version
    order_id = invoice.value.purchase_order_id if pinned is None else pinned.business_id
    latest_order = unit.repository.latest_purchase_order_version(order_id)
    if pinned is None and latest_order is not None:
        reasons.append(StaleReason.PURCHASE_ORDER_AVAILABLE)
    elif pinned is not None and latest_order != pinned:
        reasons.append(StaleReason.PURCHASE_ORDER_CHANGED)
    latest_rules = unit.reconciliations.latest_rules_version(
        inputs.rules_version.business_id
    )
    if latest_rules != inputs.rules_version:
        reasons.append(StaleReason.RULES_CHANGED)
    duplicates = unit.reconciliations.duplicate_workflow_ids(
        invoice.value.supplier_id, invoice.value.invoice_id, snapshot.workflow_id
    )
    if duplicates != inputs.duplicate_workflow_ids:
        reasons.append(StaleReason.DUPLICATES_CHANGED)
    return Freshness(tuple(reasons))


def _authorize_view(unit: UnitOfWork, workflow_id: str, actor: Principal) -> None:
    """Hide reconciliations of workflows outside the actor's business units."""
    stored = unit.repository.get_workflow(workflow_id)
    if stored is None:
        raise NotFoundError("Workflow was not found.")
    current_principal(unit, actor, Permission.VIEW, stored.business_unit)


class ReconciliationService:
    """Reconcile workflows against versioned rules; one transaction per call."""

    def __init__(self, unit_of_work: Callable[[], UnitOfWork]) -> None:
        """Receive a factory so every call gets its own transaction."""
        self._unit_of_work = unit_of_work

    def record_rules(self, rules: ReconciliationRules) -> VersionRef:
        """Store a rule set; identical to the latest reuses it, else a new version."""
        if type(rules) is not ReconciliationRules:
            raise DomainValidationError(
                "invalid_type", "rules", "Use ReconciliationRules."
            )
        for _attempt in range(_ATTEMPTS):
            try:
                return self._record_rules_once(rules)
            except ConcurrentWriteError:
                continue
        raise VersionConflictError("Concurrent writers are creating rule versions.")

    def _record_rules_once(self, rules: ReconciliationRules) -> VersionRef:
        """Append one rule version in its own transaction."""
        with self._unit_of_work() as unit:
            store = unit.reconciliations
            # Serializes with approvals that check this rule set is still latest.
            unit.repository.lock_keys((rules_key(rules.rules_id),))
            ref = append_version(
                rules.rules_id,
                content_hash(rules),
                store.latest_rules_version(rules.rules_id),
                lambda new: store.add_rules_version(Versioned(new, rules)),
            )
            unit.commit()
            return ref

    def reconcile(
        self,
        workflow_id: str,
        *,
        rules_version_id: UUID,
        actor: Principal | None = None,
    ) -> StoredReconciliation:
        """Compare the workflow's pinned invoice and order under a rule version."""
        workflow_id = validate_text(workflow_id, "workflow_id")
        _require_uuid(rules_version_id, "rules_version_id")
        for _attempt in range(_ATTEMPTS):
            try:
                snapshot_id = self._reconcile_once(workflow_id, rules_version_id, actor)
            except ConcurrentWriteError:
                # A concurrent identical reconciliation won; the retry reuses it.
                continue
            return self.get_snapshot(snapshot_id)
        raise VersionConflictError("Concurrent reconciliations did not settle.")

    def _reconcile_once(
        self, workflow_id: str, rules_version_id: UUID, actor: Principal | None
    ) -> UUID:
        """Reuse a snapshot with identical inputs or store a new one."""
        with self._unit_of_work() as unit:
            stored = unit.repository.get_workflow(workflow_id)
            if stored is None:
                raise NotFoundError("Workflow was not found.")
            current_principal(unit, actor, Permission.RECONCILE, stored.business_unit)
            rules = unit.reconciliations.get_rules_version(rules_version_id)
            if rules is None:
                raise NotFoundError("Reconciliation rules version was not found.")
            invoice = stored.workflow.invoice
            extraction = unit.documents.first_extraction_for_invoice(
                stored.invoice_version.version_id
            )
            document = (
                None
                if extraction is None
                else unit.documents.get_document(extraction.document_id)
            )
            duplicates = unit.reconciliations.duplicate_workflow_ids(
                invoice.supplier_id, invoice.invoice_id, workflow_id
            )
            inputs = ReconciliationInputs(
                invoice_version=stored.invoice_version,
                purchase_order_version=stored.purchase_order_version,
                rules_version=rules.ref,
                source_document_id=None if document is None else document.document_id,
                document_content_hash=None
                if document is None
                else document.content_hash,
                extraction_id=None if extraction is None else extraction.extraction_id,
                fields_hash=None
                if extraction is None
                else extraction_hash(extraction.result),
                duplicate_workflow_ids=duplicates,
            )
            digest = inputs_hash(inputs)
            existing = unit.reconciliations.find_snapshot(workflow_id, digest)
            if existing is not None:
                return existing.snapshot_id
            snapshot = StoredReconciliation(
                snapshot_id=uuid4(),
                workflow_id=workflow_id,
                workflow_version=stored.version,
                engine=RECONCILIATION_ENGINE,
                inputs_hash=digest,
                inputs=inputs,
                result=_compute(
                    invoice,
                    stored.workflow.purchase_order,
                    rules.value,
                    extraction,
                    duplicates,
                ),
            )
            unit.reconciliations.add_snapshot(snapshot)
            unit.commit()
            return snapshot.snapshot_id

    def get_snapshot(
        self, snapshot_id: UUID, *, actor: Principal | None = None
    ) -> StoredReconciliation:
        """Return one stored snapshot; an actor must see its workflow's unit."""
        _require_uuid(snapshot_id, "snapshot_id")
        with self._unit_of_work() as unit:
            snapshot = unit.reconciliations.get_snapshot(snapshot_id)
            if snapshot is None:
                raise NotFoundError("Reconciliation snapshot was not found.")
            if actor is not None:
                _authorize_view(unit, snapshot.workflow_id, actor)
        return snapshot

    def latest(
        self, workflow_id: str, *, actor: Principal | None = None
    ) -> StoredReconciliation:
        """Return a workflow's most recent snapshot."""
        workflow_id = validate_text(workflow_id, "workflow_id")
        with self._unit_of_work() as unit:
            if actor is not None:
                _authorize_view(unit, workflow_id, actor)
            snapshot = unit.reconciliations.latest_snapshot(workflow_id)
        if snapshot is None:
            raise NotFoundError("The workflow has no reconciliation yet.")
        return snapshot

    def freshness(self, snapshot_id: UUID) -> Freshness:
        """Compare a snapshot's inputs with what currently applies."""
        snapshot = self.get_snapshot(snapshot_id)
        with self._unit_of_work() as unit:
            return compute_freshness(unit, snapshot)

    def reproduce(self, snapshot_id: UUID) -> ReconciliationResult:
        """Recompute from the snapshot's own versions, never the latest ones."""
        snapshot = self.get_snapshot(snapshot_id)
        if snapshot.engine != RECONCILIATION_ENGINE:
            raise StoredDataIntegrityError(
                f"Snapshot engine {snapshot.engine} differs from "
                f"{RECONCILIATION_ENGINE}."
            )
        inputs = snapshot.inputs
        with self._unit_of_work() as unit:
            invoice = unit.repository.get_invoice_version(
                inputs.invoice_version.version_id
            )
            order = (
                None
                if inputs.purchase_order_version is None
                else unit.repository.get_purchase_order_version(
                    inputs.purchase_order_version.version_id
                )
            )
            rules = unit.reconciliations.get_rules_version(
                inputs.rules_version.version_id
            )
            extraction = (
                None
                if inputs.extraction_id is None
                else unit.documents.get_extraction(inputs.extraction_id)
            )
            document = (
                None
                if inputs.source_document_id is None
                else unit.documents.get_document(inputs.source_document_id)
            )
        if (
            invoice is None
            or rules is None
            or (inputs.purchase_order_version is not None and order is None)
            or (inputs.extraction_id is not None and extraction is None)
        ):
            raise StoredDataIntegrityError("Snapshot inputs are missing.")
        if (
            None if extraction is None else extraction_hash(extraction.result)
        ) != inputs.fields_hash or (
            None if document is None else document.content_hash
        ) != inputs.document_content_hash:
            raise StoredDataIntegrityError(
                "Snapshot sources do not match their hashes."
            )
        return _compute(
            invoice.value,
            None if order is None else order.value,
            rules.value,
            extraction,
            inputs.duplicate_workflow_ids,
        )

    def report(
        self, snapshot_id: UUID, *, actor: Principal | None = None
    ) -> ReconciliationReport:
        """Combine a snapshot with freshness, reproduction and quoted sources."""
        snapshot = self.get_snapshot(snapshot_id, actor=actor)
        pages: tuple[str, ...] = ()
        if snapshot.inputs.source_document_id is not None:
            with self._unit_of_work() as unit:
                pages = unit.documents.get_pages(snapshot.inputs.source_document_id)
        citations = {
            item.field: tuple(
                cite(pages, e.page, e.start, e.end)
                for e in item.sources
                if e.quote(pages) is not None
            )
            for item in snapshot.result.discrepancies
            if item.sources
        }
        return ReconciliationReport(
            snapshot=snapshot,
            freshness=self.freshness(snapshot_id),
            reproducible=self.reproduce(snapshot_id) == snapshot.result,
            citations=citations,
        )
