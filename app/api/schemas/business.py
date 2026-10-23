"""Request and response models of the Stage 06 business API.

Requests forbid unknown fields and use strict types, so ``"1"`` is not a
version number. Responses expose IDs, hashes, codes and short values; stored
document text is returned only as cited quotes in reconciliation reports.
"""

from datetime import datetime
from typing import Annotated, Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.application.access import Principal
from app.application.documents import IngestResult
from app.application.ports import (
    AuditEvent,
    CommandResult,
    OutboxEvent,
    StoredWorkflow,
    VersionRef,
)
from app.application.reconciliation import ReconciliationReport

Version = Annotated[int, Field(gt=0, strict=True)]
Reason = Annotated[str, Field(min_length=1, max_length=2000)]
Identifier = Annotated[str, Field(min_length=1, max_length=128)]


class _Request(BaseModel):
    """Reject unknown fields so a typo is not silently ignored."""

    model_config = ConfigDict(extra="forbid")


class StartWorkflowRequest(_Request):
    """Pin explicit input versions and the business unit of the new workflow."""

    workflow_id: Identifier
    business_unit: Annotated[str, Field(min_length=1, max_length=64)]
    invoice_version_id: UUID
    purchase_order_version_id: UUID | None
    policy_version_id: UUID


class ExpectedVersionRequest(_Request):
    """Commands that only need the workflow version the caller has seen."""

    expected_version: Version


class ReviseRequest(_Request):
    """Pin other input versions; previous results stay in the history."""

    expected_version: Version
    invoice_version_id: UUID
    purchase_order_version_id: UUID | None
    policy_version_id: UUID


class ApproveRequest(_Request):
    """An approval cites the reconciliation snapshot the approver reviewed."""

    expected_version: Version
    reconciliation_snapshot_id: UUID
    reason: Reason


class RejectRequest(_Request):
    """A rejection needs only the version and a reason."""

    expected_version: Version
    reason: Reason


class ReconcileRequest(_Request):
    """Reconcile the workflow's pinned inputs under one rule version."""

    rules_version_id: UUID


class VersionRefResponse(BaseModel):
    """One immutable input version."""

    version_id: UUID
    business_id: str
    version_no: int
    content_hash: str

    @classmethod
    def of(cls, ref: VersionRef) -> "VersionRefResponse":
        """Copy a version reference."""
        return cls(
            version_id=ref.version_id,
            business_id=ref.business_id,
            version_no=ref.version_no,
            content_hash=ref.content_hash,
        )


def _ref(ref: VersionRef | None) -> VersionRefResponse | None:
    """Copy an optional version reference."""
    return None if ref is None else VersionRefResponse.of(ref)


class MeResponse(BaseModel):
    """The caller's current roles, units and resulting permissions."""

    user_id: str
    roles: list[str]
    units: list[str]
    permissions: list[str]

    @classmethod
    def of(cls, principal: Principal) -> "MeResponse":
        """Sort everything for stable output."""
        return cls(
            user_id=principal.user_id,
            roles=sorted(role.value for role in principal.roles),
            units=sorted(principal.units),
            permissions=sorted(item.value for item in principal.permissions),
        )


class FieldResponse(BaseModel):
    """One extracted field: status and value, never the surrounding text."""

    name: str
    status: str
    value: str | None


class ProblemResponse(BaseModel):
    """Why an extraction needs review."""

    code: str
    field: str


class DocumentResponse(BaseModel):
    """The stored document, its extraction and the invoice version if complete."""

    document_id: UUID
    content_hash: str
    media_type: str
    page_count: int
    reused_document: bool
    extraction_id: UUID
    extraction_status: str
    fields: list[FieldResponse]
    problems: list[ProblemResponse]
    invoice_version: VersionRefResponse | None

    @classmethod
    def of(cls, result: IngestResult) -> "DocumentResponse":
        """Summarize an intake result."""
        document, extraction = result.document, result.extraction
        return cls(
            document_id=document.document_id,
            content_hash=document.content_hash,
            media_type=document.media_type.value,
            page_count=document.page_count,
            reused_document=result.reused_document,
            extraction_id=extraction.extraction_id,
            extraction_status=extraction.status.value,
            fields=[
                FieldResponse(name=f.name, status=f.status.value, value=f.value)
                for f in extraction.result.fields
            ],
            problems=[
                ProblemResponse(code=p.code.value, field=p.field)
                for p in extraction.problems
            ],
            invoice_version=_ref(extraction.invoice_version),
        )


class CommandResponse(BaseModel):
    """The outcome of a command; ``replayed`` marks a repeated request."""

    workflow_id: str
    command: str
    state: str
    version: int
    snapshot_id: UUID | None
    replayed: bool

    @classmethod
    def of(cls, result: CommandResult) -> "CommandResponse":
        """Copy a command result."""
        return cls(
            workflow_id=result.workflow_id,
            command=result.command.value,
            state=result.state.value,
            version=result.version,
            snapshot_id=result.snapshot_id,
            replayed=result.replayed,
        )


class DecisionResponse(BaseModel):
    """Who decided, why, and on which reconciliation."""

    decided_by: str
    reason: str
    reconciliation_snapshot_id: UUID | None


class WorkflowResponse(BaseModel):
    """Current state, version, unit, pinned inputs, last check and decision."""

    workflow_id: str
    state: str
    version: int
    business_unit: str
    created_by: str | None
    invoice_version: VersionRefResponse
    purchase_order_version: VersionRefResponse | None
    policy_version: VersionRefResponse
    last_validation_snapshot_id: UUID | None
    issue_codes: list[str]
    decision: DecisionResponse | None

    @classmethod
    def of(cls, stored: StoredWorkflow) -> "WorkflowResponse":
        """Summarize a stored workflow."""
        workflow = stored.workflow
        check = workflow.last_check
        decision = workflow.decision
        return cls(
            workflow_id=workflow.workflow_id,
            state=workflow.state.value,
            version=stored.version,
            business_unit=stored.business_unit,
            created_by=stored.created_by,
            invoice_version=VersionRefResponse.of(stored.invoice_version),
            purchase_order_version=_ref(stored.purchase_order_version),
            policy_version=VersionRefResponse.of(stored.policy_version),
            last_validation_snapshot_id=stored.last_snapshot_id,
            issue_codes=[]
            if check is None
            else [issue.code.value for issue in check.issues],
            decision=None
            if decision is None
            else DecisionResponse(
                decided_by=decision.actor_id,
                reason=decision.reason,
                reconciliation_snapshot_id=stored.decision_reconciliation_id,
            ),
        )


class SourceResponse(BaseModel):
    """A quoted line of the stored document that supports a discrepancy."""

    page: int
    line_number: int
    quote: str


class DiscrepancyResponse(BaseModel):
    """One reconciliation finding with expected and actual values."""

    code: str
    field: str
    expected: str | None
    actual: str | None
    rule: str
    sources: list[SourceResponse]


class ReconciliationResponse(BaseModel):
    """A stored reconciliation with freshness and a reproduction check."""

    snapshot_id: UUID
    workflow_id: str
    workflow_version: int
    status: str
    current: bool
    stale_reasons: list[str]
    reproducible: bool
    inputs_hash: str
    invoice_version: VersionRefResponse
    purchase_order_version: VersionRefResponse | None
    rules_version: VersionRefResponse
    discrepancies: list[DiscrepancyResponse]

    @classmethod
    def of(cls, report: ReconciliationReport) -> "ReconciliationResponse":
        """Summarize a report; quotes come from the stored page text."""
        snapshot = report.snapshot
        inputs = snapshot.inputs
        return cls(
            snapshot_id=snapshot.snapshot_id,
            workflow_id=snapshot.workflow_id,
            workflow_version=snapshot.workflow_version,
            status=snapshot.result.status.value,
            current=report.freshness.is_current,
            stale_reasons=[reason.value for reason in report.freshness.reasons],
            reproducible=report.reproducible,
            inputs_hash=snapshot.inputs_hash,
            invoice_version=VersionRefResponse.of(inputs.invoice_version),
            purchase_order_version=_ref(inputs.purchase_order_version),
            rules_version=VersionRefResponse.of(inputs.rules_version),
            discrepancies=[
                DiscrepancyResponse(
                    code=item.code.value,
                    field=item.field,
                    expected=item.expected,
                    actual=item.actual,
                    rule=item.rule,
                    sources=[
                        SourceResponse(
                            page=c.page, line_number=c.line_number, quote=c.quote
                        )
                        for c in report.citations.get(item.field, ())
                    ],
                )
                for item in snapshot.result.discrepancies
            ],
        )


class AuditEventResponse(BaseModel):
    """One audit entry: IDs and codes, never document text."""

    event_id: int | None
    command: str
    previous_state: str | None
    new_state: str
    workflow_version: int
    actor_id: str | None
    source: str
    issue_codes: list[str]
    validation_snapshot_id: UUID | None
    reconciliation_snapshot_id: UUID | None
    outbox_event_id: UUID | None
    occurred_at: datetime | None

    @classmethod
    def of(cls, event: AuditEvent) -> "AuditEventResponse":
        """Copy an audit event without the idempotency key."""
        return cls(
            event_id=event.event_id,
            command=event.command.value,
            previous_state=None
            if event.previous_state is None
            else event.previous_state.value,
            new_state=event.new_state.value,
            workflow_version=event.workflow_version,
            actor_id=event.actor_id,
            source=event.source,
            issue_codes=list(event.issue_codes),
            validation_snapshot_id=event.snapshot_id,
            reconciliation_snapshot_id=event.reconciliation_snapshot_id,
            outbox_event_id=event.outbox_event_id,
            occurred_at=event.occurred_at,
        )


class OutboxEventResponse(BaseModel):
    """A stored decision event; delivery is not implemented until Stage 09."""

    event_id: UUID
    event_type: str
    workflow_id: str
    workflow_version: int
    payload: dict[str, Any]
    created_at: datetime | None

    @classmethod
    def of(cls, event: OutboxEvent) -> "OutboxEventResponse":
        """Copy an outbox event."""
        return cls(
            event_id=event.event_id,
            event_type=event.event_type.value,
            workflow_id=event.workflow_id,
            workflow_version=event.workflow_version,
            payload=event.payload,
            created_at=event.created_at,
        )
