"""Stage 06 business endpoints: identity, intake, workflows and decisions.

Routes only translate HTTP to service calls. Every call passes the
authenticated principal, and the services check roles, business units and
state inside the transaction. Commands require an ``Idempotency-Key`` header.
"""

from enum import StrEnum
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Path, Query, Request
from starlette.concurrency import run_in_threadpool

from app.api.dependencies import ContextDep, PrincipalDep, ServicesDep
from app.api.errors import DocumentTooLargeError
from app.api.schemas.business import (
    AgentRunResponse,
    ApproveRequest,
    AuditEventResponse,
    CommandResponse,
    DocumentResponse,
    ExpectedVersionRequest,
    MeResponse,
    OutboxEventResponse,
    ReconcileRequest,
    ReconciliationResponse,
    RejectRequest,
    ReviseRequest,
    StartWorkflowRequest,
    VersionRefResponse,
    WorkflowDocumentResponse,
    WorkflowResponse,
    WorkflowSummaryResponse,
)
from app.application.errors import NotFoundError
from app.application.workflows import ReferenceKind
from app.domain.workflow import WorkflowState

router = APIRouter()

WorkflowId = Annotated[str, Path(min_length=1, max_length=128)]


class ReferencePath(StrEnum):
    """URL names of the reference data kinds."""

    PURCHASE_ORDERS = "purchase-orders"
    POLICIES = "policies"
    RECONCILIATION_RULES = "reconciliation-rules"


_REFERENCE_KINDS = {
    ReferencePath.PURCHASE_ORDERS: ReferenceKind.PURCHASE_ORDER,
    ReferencePath.POLICIES: ReferenceKind.POLICY,
    ReferencePath.RECONCILIATION_RULES: ReferenceKind.RECONCILIATION_RULES,
}


@router.get("/me", response_model=MeResponse, tags=["identity"])
def me(principal: PrincipalDep) -> MeResponse:
    """Show who the token belongs to and what that user may do."""
    return MeResponse.of(principal)


async def _read_limited(request: Request, limit: int) -> bytes:
    """Read the body but stop as soon as it exceeds the document limit."""
    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > limit:
        raise DocumentTooLargeError("The document exceeds the size limit.")
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > limit:
            raise DocumentTooLargeError("The document exceeds the size limit.")
    return bytes(body)


@router.post(
    "/documents",
    response_model=DocumentResponse,
    tags=["documents"],
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {
                "application/octet-stream": {
                    "schema": {"type": "string", "format": "binary"}
                }
            },
        }
    },
)
async def upload_document(
    request: Request,
    services: ServicesDep,
    principal: PrincipalDep,
    name: Annotated[str | None, Query(max_length=255)] = None,
) -> DocumentResponse:
    """Upload raw document bytes; the format is detected from the bytes."""
    content = await _read_limited(request, services.max_document_bytes)
    result = await run_in_threadpool(
        services.documents.ingest, content, name, actor=principal
    )
    return DocumentResponse.of(result)


@router.get(
    "/reference/{kind}/{business_id}",
    response_model=VersionRefResponse,
    tags=["reference"],
)
def latest_reference(
    kind: ReferencePath,
    business_id: Annotated[str, Path(min_length=1, max_length=128)],
    services: ServicesDep,
    principal: PrincipalDep,
) -> VersionRefResponse:
    """Return the latest version of an order, policy or rule set."""
    ref = services.workflows.latest_reference(
        _REFERENCE_KINDS[kind], business_id, actor=principal
    )
    return VersionRefResponse.of(ref)


@router.post("/workflows", response_model=CommandResponse, tags=["workflows"])
def start_workflow(
    body: StartWorkflowRequest,
    services: ServicesDep,
    principal: PrincipalDep,
    context: ContextDep,
) -> CommandResponse:
    """Start a workflow in one of the caller's business units."""
    result = services.workflows.start(
        body.workflow_id,
        invoice_version_id=body.invoice_version_id,
        purchase_order_version_id=body.purchase_order_version_id,
        policy_version_id=body.policy_version_id,
        business_unit=body.business_unit,
        context=context,
        actor=principal,
    )
    return CommandResponse.of(result)


@router.get(
    "/workflows", response_model=list[WorkflowSummaryResponse], tags=["workflows"]
)
def list_workflows(
    services: ServicesDep,
    principal: PrincipalDep,
    state: WorkflowState | None = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> list[WorkflowSummaryResponse]:
    """List recently changed workflows in the caller's business units."""
    summaries = services.workflows.list_workflows(
        actor=principal, state=state, limit=limit
    )
    return [WorkflowSummaryResponse.of(item) for item in summaries]


@router.get(
    "/workflows/{workflow_id}", response_model=WorkflowResponse, tags=["workflows"]
)
def get_workflow(
    workflow_id: WorkflowId, services: ServicesDep, principal: PrincipalDep
) -> WorkflowResponse:
    """Return a workflow from one of the caller's business units."""
    return WorkflowResponse.of(
        services.workflows.get_workflow(workflow_id, actor=principal)
    )


@router.get(
    "/workflows/{workflow_id}/document",
    response_model=WorkflowDocumentResponse,
    tags=["documents"],
)
def workflow_document(
    workflow_id: WorkflowId, services: ServicesDep, principal: PrincipalDep
) -> WorkflowDocumentResponse:
    """Return the invoice's source document with every field's citations."""
    found = services.documents.workflow_evidence(workflow_id, actor=principal)
    if found is None:
        raise NotFoundError("The workflow's invoice has no source document.")
    return WorkflowDocumentResponse.of(*found)


@router.post(
    "/workflows/{workflow_id}/check",
    response_model=CommandResponse,
    tags=["workflows"],
)
def check_workflow(
    workflow_id: WorkflowId,
    body: ExpectedVersionRequest,
    services: ServicesDep,
    principal: PrincipalDep,
    context: ContextDep,
) -> CommandResponse:
    """Run the basic checks on the pinned inputs."""
    result = services.workflows.check(
        workflow_id,
        expected_version=body.expected_version,
        context=context,
        actor=principal,
    )
    return CommandResponse.of(result)


@router.post(
    "/workflows/{workflow_id}/revise",
    response_model=CommandResponse,
    tags=["workflows"],
)
def revise_workflow(
    workflow_id: WorkflowId,
    body: ReviseRequest,
    services: ServicesDep,
    principal: PrincipalDep,
    context: ContextDep,
) -> CommandResponse:
    """Pin other input versions; the workflow returns to received."""
    result = services.workflows.revise(
        workflow_id,
        expected_version=body.expected_version,
        invoice_version_id=body.invoice_version_id,
        purchase_order_version_id=body.purchase_order_version_id,
        policy_version_id=body.policy_version_id,
        context=context,
        actor=principal,
    )
    return CommandResponse.of(result)


@router.post(
    "/workflows/{workflow_id}/reconciliations",
    response_model=ReconciliationResponse,
    tags=["reconciliations"],
)
def reconcile_workflow(
    workflow_id: WorkflowId,
    body: ReconcileRequest,
    services: ServicesDep,
    principal: PrincipalDep,
) -> ReconciliationResponse:
    """Reconcile the pinned invoice and order; equal inputs reuse a snapshot."""
    snapshot = services.reconciliations.reconcile(
        workflow_id, rules_version_id=body.rules_version_id, actor=principal
    )
    return ReconciliationResponse.of(
        services.reconciliations.report(snapshot.snapshot_id, actor=principal)
    )


@router.get(
    "/workflows/{workflow_id}/reconciliations/latest",
    response_model=ReconciliationResponse,
    tags=["reconciliations"],
)
def latest_reconciliation(
    workflow_id: WorkflowId, services: ServicesDep, principal: PrincipalDep
) -> ReconciliationResponse:
    """Return the workflow's newest reconciliation with its freshness."""
    snapshot = services.reconciliations.latest(workflow_id, actor=principal)
    return ReconciliationResponse.of(
        services.reconciliations.report(snapshot.snapshot_id, actor=principal)
    )


@router.get(
    "/reconciliations/{snapshot_id}",
    response_model=ReconciliationResponse,
    tags=["reconciliations"],
)
def get_reconciliation(
    snapshot_id: UUID, services: ServicesDep, principal: PrincipalDep
) -> ReconciliationResponse:
    """Return one reconciliation snapshot with freshness and sources."""
    return ReconciliationResponse.of(
        services.reconciliations.report(snapshot_id, actor=principal)
    )


@router.post(
    "/workflows/{workflow_id}/approve",
    response_model=CommandResponse,
    tags=["decisions"],
)
def approve_workflow(
    workflow_id: WorkflowId,
    body: ApproveRequest,
    services: ServicesDep,
    principal: PrincipalDep,
    context: ContextDep,
) -> CommandResponse:
    """Approve on a fresh matched reconciliation; writes an outbox event."""
    result = services.workflows.approve(
        workflow_id,
        expected_version=body.expected_version,
        reconciliation_snapshot_id=body.reconciliation_snapshot_id,
        reason=body.reason,
        actor=principal,
        context=context,
    )
    return CommandResponse.of(result)


@router.post(
    "/workflows/{workflow_id}/reject",
    response_model=CommandResponse,
    tags=["decisions"],
)
def reject_workflow(
    workflow_id: WorkflowId,
    body: RejectRequest,
    services: ServicesDep,
    principal: PrincipalDep,
    context: ContextDep,
) -> CommandResponse:
    """Reject an open workflow with a reason; writes an outbox event."""
    result = services.workflows.reject(
        workflow_id,
        expected_version=body.expected_version,
        reason=body.reason,
        actor=principal,
        context=context,
    )
    return CommandResponse.of(result)


@router.get(
    "/workflows/{workflow_id}/audit",
    response_model=list[AuditEventResponse],
    tags=["workflows"],
)
def audit_trail(
    workflow_id: WorkflowId, services: ServicesDep, principal: PrincipalDep
) -> list[AuditEventResponse]:
    """List the workflow's audit events in commit order."""
    events = services.workflows.audit_trail(workflow_id, actor=principal)
    return [AuditEventResponse.of(event) for event in events]


@router.get(
    "/workflows/{workflow_id}/outbox",
    response_model=list[OutboxEventResponse],
    tags=["decisions"],
)
def outbox_events(
    workflow_id: WorkflowId, services: ServicesDep, principal: PrincipalDep
) -> list[OutboxEventResponse]:
    """List decision events waiting for a future delivery worker."""
    events = services.workflows.outbox_events(workflow_id, actor=principal)
    return [OutboxEventResponse.of(event) for event in events]


@router.post(
    "/workflows/{workflow_id}/agent-runs",
    response_model=AgentRunResponse,
    tags=["agent"],
)
def run_agent(
    workflow_id: WorkflowId, services: ServicesDep, principal: PrincipalDep
) -> AgentRunResponse:
    """Run the bounded review agent; its draft never changes the workflow."""
    return AgentRunResponse.of_result(services.agent.run(workflow_id, actor=principal))


@router.get("/agent-runs/{run_id}", response_model=AgentRunResponse, tags=["agent"])
def get_agent_run(
    run_id: UUID, services: ServicesDep, principal: PrincipalDep
) -> AgentRunResponse:
    """Return a stored run and its trace; the explanation is not stored."""
    return AgentRunResponse.of(services.agent.get_run(run_id, actor=principal))
