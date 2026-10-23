"""The HTTP API over PostgreSQL: tokens, roles, units, staleness and decisions."""

from collections.abc import Iterator
from dataclasses import dataclass, replace
from decimal import Decimal
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine

from app.api.dependencies import Services
from app.application.documents import DocumentService
from app.application.ports import CommandContext
from app.application.reconciliation import ReconciliationService
from app.application.users import UserService
from app.application.workflows import WorkflowService
from app.config import Settings
from app.demo import build_demo_documents
from app.domain.documents import DocumentLimits
from app.domain.models import LineItem
from app.domain.money import Currency, Money
from app.domain.rules import ValidationPolicy
from app.infrastructure.db.session import build_agent_service
from app.infrastructure.llm.demo import DemoRulesProvider
from app.main import create_app
from app.samples import invoice_text
from tests.persistence.conftest import UNIT, USD_RULES, employee

pytestmark = pytest.mark.postgres

EXACT = [
    ("DEMO-SEAL", "Fictional seal kit", "2", "12.50"),
    ("DEMO-FILTER", "Fictional filter", "1", "5.00"),
]


@dataclass(frozen=True, slots=True)
class Api:
    """A test client and one bearer header per fictional employee."""

    client: TestClient
    headers: dict[str, dict[str, str]]

    def call(
        self,
        method: str,
        path: str,
        who: str,
        *,
        key: str | None = None,
        **kwargs: object,
    ) -> dict[str, object] | list[object]:
        """Send a request as an employee and return the decoded JSON body."""
        headers = dict(self.headers[who])
        if key is not None:
            headers["Idempotency-Key"] = key
        response = self.client.request(method, path, headers=headers, **kwargs)  # type: ignore[arg-type]
        body = response.json()
        body_with_status = body if isinstance(body, list) else {**body}
        if isinstance(body_with_status, dict):
            body_with_status["_status"] = response.status_code
        return body_with_status


@pytest.fixture
def api(
    engine: Engine,
    service: WorkflowService,
    document_service: DocumentService,
    reconciliation_service: ReconciliationService,
    user_service: UserService,
) -> Iterator[Api]:
    """An app on the test database with reference data and five employees."""
    _, order = build_demo_documents()
    service.record_purchase_order(order)
    service.record_policy(ValidationPolicy("api-test"))
    reconciliation_service.record_rules(USD_RULES)
    people = {
        "clerk": ("clerk",),
        "approver": ("approver",),
        "viewer": ("viewer",),
    }
    headers: dict[str, dict[str, str]] = {}
    for name, roles in people.items():
        employee(user_service, f"{name}-1", *roles)
        token = user_service.issue_token(f"{name}-1", label="pytest").secret
        headers[name] = {"Authorization": f"Bearer {token}"}
    employee(user_service, "outsider-1", "approver", units=("UNIT-B",))
    outsider = user_service.issue_token("outsider-1", label="pytest").secret
    headers["outsider"] = {"Authorization": f"Bearer {outsider}"}
    services = Services(
        workflows=service,
        documents=document_service,
        reconciliations=reconciliation_service,
        users=user_service,
        agent=build_agent_service(engine, DemoRulesProvider()),
        max_document_bytes=DocumentLimits().max_bytes,
    )
    with TestClient(create_app(Settings(), services=services)) as client:
        yield Api(client, headers)


def _reference(api: Api, kind: str, business_id: str) -> str:
    """Look up the latest reference version ID."""
    body = api.call("get", f"/reference/{kind}/{business_id}", "viewer")
    assert isinstance(body, dict) and body["_status"] == 200
    return str(body["version_id"])


def _submit(api: Api, workflow_id: str, invoice_id: str) -> dict[str, object]:
    """Upload, start and check a workflow as the clerk; return reconciliation."""
    content = invoice_text(invoice_id, "DEMO-PO-001", EXACT, "30.00").encode()
    uploaded = api.call("post", "/documents?name=invoice.txt", "clerk", content=content)
    assert isinstance(uploaded, dict) and uploaded["_status"] == 200
    assert uploaded["extraction_status"] == "complete"
    invoice = uploaded["invoice_version"]
    assert isinstance(invoice, dict)
    started = api.call(
        "post",
        "/workflows",
        "clerk",
        key=f"{workflow_id}-start",
        json={
            "workflow_id": workflow_id,
            "business_unit": UNIT,
            "invoice_version_id": invoice["version_id"],
            "purchase_order_version_id": _reference(
                api, "purchase-orders", "DEMO-PO-001"
            ),
            "policy_version_id": _reference(api, "policies", "api-test"),
        },
    )
    assert isinstance(started, dict) and started["state"] == "received"
    checked = api.call(
        "post",
        f"/workflows/{workflow_id}/check",
        "clerk",
        key=f"{workflow_id}-check",
        json={"expected_version": 1},
    )
    assert isinstance(checked, dict) and checked["state"] == "awaiting_approval"
    reconciled = api.call(
        "post",
        f"/workflows/{workflow_id}/reconciliations",
        "clerk",
        json={
            "rules_version_id": _reference(
                api, "reconciliation-rules", USD_RULES.rules_id
            )
        },
    )
    assert isinstance(reconciled, dict)
    return reconciled


def _approve(
    api: Api, who: str, snapshot_id: object, *, version: int = 2, key: str = "ok"
) -> dict[str, object]:
    """Send an approval and return the body with its status."""
    body = api.call(
        "post",
        "/workflows/WF-API-1/approve",
        who,
        key=key,
        json={
            "expected_version": version,
            "reconciliation_snapshot_id": snapshot_id,
            "reason": "Fictional invoice matches the order",
        },
    )
    assert isinstance(body, dict)
    return body


def _error(body: dict[str, object] | list[object]) -> tuple[object, object]:
    """Return status and error code of a failed call."""
    assert isinstance(body, dict)
    error = body["error"]
    assert isinstance(error, dict)
    return body["_status"], error["code"]


def test_tokens_are_required_and_resolve_to_current_rights(api: Api) -> None:
    """No, malformed and unknown tokens get one 401; /me shows the rights."""
    for headers in ({}, {"Authorization": "Bearer nope"}, {"Authorization": "Basic x"}):
        response = api.client.get("/me", headers=headers)
        assert response.status_code == 401
        assert response.headers["WWW-Authenticate"] == "Bearer"
        assert response.json()["error"]["code"] == "unauthenticated"
    me = api.call("get", "/me", "approver")
    assert me == {
        "user_id": "approver-1",
        "roles": ["approver"],
        "units": [UNIT],
        "permissions": ["assist", "decide", "reconcile", "view"],
        "_status": 200,
    }


def test_full_decision_flow_with_refusals(api: Api) -> None:
    """Role, unit, version and transition refusals, then one approval."""
    report = _submit(api, "WF-API-1", "INV-API-1")
    assert (report["status"], report["current"]) == ("matched", True)
    snapshot_id = report["snapshot_id"]

    assert _error(_approve(api, "viewer", snapshot_id, key="v")) == (
        403,
        "missing_permission",
    )
    assert _error(_approve(api, "clerk", snapshot_id, key="c")) == (
        403,
        "missing_permission",
    )
    assert _error(_approve(api, "outsider", snapshot_id, key="o")) == (
        404,
        "not_found",
    )
    assert _error(api.call("get", "/workflows/WF-API-1", "outsider")) == (
        404,
        "not_found",
    )
    assert _error(_approve(api, "approver", snapshot_id, version=1, key="old")) == (
        409,
        "version_conflict",
    )
    approved = _approve(api, "approver", snapshot_id)
    assert (approved["_status"], approved["state"], approved["replayed"]) == (
        200,
        "approved",
        False,
    )
    again = _approve(api, "approver", snapshot_id)
    assert again["replayed"] is True
    rejected = api.call(
        "post",
        "/workflows/WF-API-1/reject",
        "approver",
        key="late",
        json={"expected_version": 3, "reason": "Too late"},
    )
    assert _error(rejected) == (409, "invalid_transition")

    workflow = api.call("get", "/workflows/WF-API-1", "viewer")
    assert isinstance(workflow, dict)
    assert workflow["decision"] == {
        "decided_by": "approver-1",
        "reason": "Fictional invoice matches the order",
        "reconciliation_snapshot_id": snapshot_id,
    }
    assert workflow["created_by"] == "clerk-1"
    outbox = api.call("get", "/workflows/WF-API-1/outbox", "viewer")
    assert isinstance(outbox, list) and len(outbox) == 1
    [event] = outbox
    assert isinstance(event, dict) and event["event_type"] == "invoice.approved"
    audit = api.call("get", "/workflows/WF-API-1/audit", "viewer")
    assert isinstance(audit, list)
    last = audit[-1]
    assert isinstance(last, dict)
    assert (last["command"], last["actor_id"], last["source"]) == (
        "approve",
        "approver-1",
        "api",
    )
    assert last["reconciliation_snapshot_id"] == snapshot_id
    assert last["outbox_event_id"] == event["event_id"]


def test_order_change_after_reconciliation_needs_a_new_one(
    api: Api, service: WorkflowService
) -> None:
    """A stale snapshot is refused with reasons and nothing changes."""
    report = _submit(api, "WF-API-1", "INV-API-1")
    _, order = build_demo_documents()
    service.record_purchase_order(
        replace(
            order,
            lines=(
                order.lines[0],
                LineItem(
                    "DEMO-FILTER",
                    "Fictional filter",
                    Decimal("2"),
                    Money(Decimal("5.00"), Currency.USD),
                ),
            ),
        )
    )
    stale = _approve(api, "approver", report["snapshot_id"])
    assert _error(stale) == (409, "stale_reconciliation")
    error = stale["error"]
    assert isinstance(error, dict) and error["reasons"] == ["purchase_order_changed"]
    latest = api.call("get", "/workflows/WF-API-1/reconciliations/latest", "viewer")
    assert isinstance(latest, dict)
    assert (latest["current"], latest["stale_reasons"]) == (
        False,
        ["purchase_order_changed"],
    )


def test_requests_are_validated_strictly(api: Api) -> None:
    """Missing key, string versions and unknown fields are 422, without echo."""
    report = _submit(api, "WF-API-1", "INV-API-1")
    no_key = api.call(
        "post",
        "/workflows/WF-API-1/reject",
        "approver",
        json={"expected_version": 2, "reason": "x"},
    )
    assert _error(no_key) == (422, "invalid_request")
    secret = "fictional-value-not-echoed"
    for body in (
        {"expected_version": "2", "reason": secret},
        {"expected_version": 2, "reason": "x", "approved": secret},
        {"expected_version": 2, "reason": ""},
    ):
        response = api.call(
            "post", "/workflows/WF-API-1/reject", "approver", key="bad", json=body
        )
        assert _error(response) == (422, "invalid_request")
        assert secret not in str(response)
    bad_key = api.call(
        "post",
        "/workflows/WF-API-1/reject",
        "approver",
        key="has space",
        json={"expected_version": 2, "reason": "x"},
    )
    assert _error(bad_key) == (422, "invalid_request")
    assert report["status"] == "matched"


def test_upload_limits_and_permissions(api: Api) -> None:
    """Oversized bodies stop at the limit; viewers cannot upload."""
    too_large = api.call(
        "post",
        "/documents",
        "clerk",
        content=b"a" * (DocumentLimits().max_bytes + 1),
    )
    assert _error(too_large) == (413, "too_large")
    picture = api.call(
        "post", "/documents", "clerk", content=b"\x89PNG\r\n\x1a\nfictional"
    )
    assert _error(picture) == (422, "unsupported_type")
    viewer = api.call("post", "/documents", "viewer", content=b"Invoice number: X")
    assert _error(viewer) == (403, "missing_permission")


def test_start_in_a_foreign_unit_is_forbidden(api: Api) -> None:
    """A clerk names the unit, so a foreign one is 403, not 404."""
    body = api.call(
        "post",
        "/workflows",
        "clerk",
        key="foreign",
        json={
            "workflow_id": "WF-B",
            "business_unit": "UNIT-B",
            "invoice_version_id": "00000000-0000-0000-0000-000000000000",
            "purchase_order_version_id": None,
            "policy_version_id": "00000000-0000-0000-0000-000000000000",
        },
    )
    assert _error(body) == (403, "missing_permission")


def test_agent_run_over_http(api: Api) -> None:
    """Clerks run the agent; viewers read the trace; nothing else changes."""
    _submit(api, "WF-API-1", "INV-API-1")
    run = api.call("post", "/workflows/WF-API-1/agent-runs", "clerk")
    assert isinstance(run, dict) and run["_status"] == 200
    assert (run["stop_reason"], run["next_step"], run["provider"]) == (
        "final_answer",
        "ready_for_approval",
        "demo-rules/1",
    )
    assert isinstance(run["explanation"], str) and run["explanation_sha256"]
    steps: Any = run["steps"]
    assert [step["kind"] for step in steps] == [
        "provider_call",
        "tool_call",
        "tool_call",
        "provider_call",
        "final_answer",
    ]
    stored = api.call("get", f"/agent-runs/{run['run_id']}", "viewer")
    assert isinstance(stored, dict) and stored["_status"] == 200
    assert stored["explanation"] is None
    assert stored["explanation_sha256"] == run["explanation_sha256"]
    assert _error(api.call("get", f"/agent-runs/{run['run_id']}", "outsider")) == (
        404,
        "not_found",
    )
    assert _error(api.call("post", "/workflows/WF-API-1/agent-runs", "viewer")) == (
        403,
        "missing_permission",
    )
    assert _error(api.call("post", "/workflows/WF-API-1/agent-runs", "outsider")) == (
        404,
        "not_found",
    )
    workflow = api.call("get", "/workflows/WF-API-1", "viewer")
    assert isinstance(workflow, dict)
    assert (workflow["state"], workflow["version"], workflow["decision"]) == (
        "awaiting_approval",
        2,
        None,
    )


def test_workflow_list_and_document_endpoints(
    api: Api, service: WorkflowService
) -> None:
    """Lists are scoped to the caller's units; documents carry citations."""
    _submit(api, "WF-API-1", "INV-API-1")
    listed = api.call("get", "/workflows", "viewer")
    assert isinstance(listed, list) and [w["workflow_id"] for w in listed] == [  # type: ignore[index]
        "WF-API-1"
    ]
    assert api.call("get", "/workflows?state=approved", "viewer") == []
    assert api.call("get", "/workflows", "outsider") == []
    assert _error(api.call("get", "/workflows?state=paid", "viewer")) == (
        422,
        "invalid_request",
    )
    assert _error(api.call("get", "/workflows?limit=0", "viewer")) == (
        422,
        "invalid_request",
    )
    document = api.call("get", "/workflows/WF-API-1/document", "viewer")
    assert isinstance(document, dict) and document["extraction_status"] == "complete"
    fields: Any = document["fields"]
    total = next(f for f in fields if f["name"] == "declared_total")
    [citation] = total["citations"]
    assert citation["quote"] == total["value"] == "30.00"
    assert citation["line"].startswith("Total due")
    assert _error(api.call("get", "/workflows/WF-API-1/document", "outsider")) == (
        404,
        "not_found",
    )
    invoice, _ = build_demo_documents()
    code_made = service.record_invoice(replace(invoice, invoice_id="INV-CODE"))
    service.start(
        "WF-CODE",
        invoice_version_id=code_made.version_id,
        purchase_order_version_id=None,
        policy_version_id=service.record_policy(
            ValidationPolicy("api-test")
        ).version_id,
        business_unit=UNIT,
        context=CommandContext("code-start", "pytest"),
    )
    missing = api.call("get", "/workflows/WF-CODE/document", "viewer")
    assert _error(missing) == (404, "not_found")
    assert "no source document" in str(missing)
