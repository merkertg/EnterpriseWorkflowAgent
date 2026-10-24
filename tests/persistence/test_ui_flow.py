"""The Streamlit interface end to end: headless page, real API, PostgreSQL.

Each page session talks to the API through FastAPI's in-process client, so
the interface reaches data only over HTTP, exactly as in the browser. Needs
the ``ui`` dependency group and TEST_DATABASE_URL.
"""

from collections.abc import Callable, Iterator
from dataclasses import dataclass, replace
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text

from app.api.dependencies import Services
from app.application.delivery import DeliveryPolicy, WebhookMessage, WebhookResponse
from app.application.documents import DocumentService
from app.application.reconciliation import ReconciliationService
from app.application.users import UserService
from app.application.workflows import WorkflowService
from app.config import Settings
from app.domain.documents import DocumentLimits
from app.domain.models import LineItem, PurchaseOrder
from app.domain.money import Currency, Money
from app.infrastructure.db.session import build_agent_service, build_delivery_service
from app.infrastructure.llm.demo import DemoRulesProvider
from app.main import create_app
from app.ui_demo_setup import ORDER_ID, setup_ui_demo
from ui.client import ApiClient, RawResponse

pytestmark = pytest.mark.postgres
testing = pytest.importorskip("streamlit.testing.v1")
APP = str(Path(__file__).resolve().parents[2] / "ui" / "app.py")


@dataclass(frozen=True, slots=True)
class Desk:
    """Tokens, sample texts and a way to open a page session per user."""

    engine: Engine
    workflows: WorkflowService
    samples: dict[str, str]
    session: Callable[[str], Any]


@pytest.fixture
def desk(
    engine: Engine,
    service: WorkflowService,
    document_service: DocumentService,
    reconciliation_service: ReconciliationService,
    user_service: UserService,
    tmp_path: Path,
) -> Iterator[Desk]:
    """The demo setup, an API on the test database and page sessions."""
    setup = setup_ui_demo(engine, tmp_path)
    tokens: dict[str, str] = setup["tokens"]  # type: ignore[assignment]
    samples = {Path(p).name: Path(p).read_text() for p in setup["samples"]}  # type: ignore[attr-defined]
    services = Services(
        workflows=service,
        documents=document_service,
        reconciliations=reconciliation_service,
        users=user_service,
        agent=build_agent_service(engine, DemoRulesProvider()),
        max_document_bytes=DocumentLimits().max_bytes,
    )
    with TestClient(create_app(Settings(), services=services)) as api:

        def transport(
            method: str, url: str, headers: dict[str, str], body: bytes | None
        ) -> RawResponse:
            """Send the interface's request to the in-process API."""
            response = api.request(method, url, headers=headers, content=body)
            return RawResponse(response.status_code, response.content)

        def session(user: str) -> Any:
            """A fresh page session, as after opening or reloading the page."""
            app = testing.AppTest.from_file(APP, default_timeout=60)
            app.session_state["api_client"] = ApiClient(
                "http://testserver", tokens[user], transport
            )
            return app.run()

        yield Desk(engine, service, samples, session)


def _texts(elements: Any) -> str:
    """All values of message elements as one text."""
    return " ".join(str(item.value) for item in elements)


def _start(desk: Desk, sample: str) -> str:
    """The clerk uploads a sample, starts the workflow and reconciles it."""
    page = desk.session("ui-clerk")
    page.radio(key="page").set_value("New invoice").run()
    page.text_area(key="invoice_text").input(desk.samples[sample]).run()
    page.button(key="do_upload").click().run()
    assert "**complete**" in " ".join(m.value for m in page.markdown)
    workflow_id = str(page.text_input(key="new_workflow_id").value)
    page.button(key="do_start").click().run()
    assert f"Started {workflow_id}" in _texts(page.success), _texts(page.error)
    assert page.selectbox(key="workflow_picker").value == workflow_id
    page.button(key="do_reconcile").click().run()
    assert "Reconcile:" in _texts(page.success)
    assert not page.exception
    return workflow_id


def _open(desk: Desk, user: str, workflow_id: str) -> Any:
    """Open the workflow in a fresh session of the given user."""
    page = desk.session(user)
    page.selectbox(key="workflow_picker").select(workflow_id).run()
    return page


def _approve(page: Any, reason: str = "Fictional invoice matches the order") -> Any:
    """Type a reason and press Approve."""
    page.text_input(key="decision_reason").input(reason)
    return page.button(key="do_approve").click().run()


def _outbox_count(engine: Engine, workflow_id: str) -> int:
    """Count stored decision events."""
    with engine.connect() as connection:
        return int(
            connection.execute(
                text("SELECT count(*) FROM outbox_events WHERE workflow_id = :id"),
                {"id": workflow_id},
            ).scalar_one()
        )


def test_full_scenario_with_double_click_and_reload(desk: Desk) -> None:
    """Upload to approval; repeated submits never decide twice."""
    workflow_id = _start(desk, "clean.txt")
    # The same start form sent again (reload, second tab) replays the result.
    again = desk.session("ui-clerk")
    again.radio(key="page").set_value("New invoice").run()
    again.text_area(key="invoice_text").input(desk.samples["clean.txt"]).run()
    again.button(key="do_upload").click().run()
    assert "same bytes as an earlier upload" in " ".join(
        m.value for m in again.markdown
    )
    again.button(key="do_start").click().run()
    assert "stored result" in _texts(again.success), _texts(again.error)

    first = _open(desk, "ui-approver", workflow_id)
    stale_tab = _open(desk, "ui-approver", workflow_id)
    metrics = {m.label: m.value for m in first.metric}
    assert (
        metrics["State"] == "awaiting_approval" and metrics["Started by"] == "ui-clerk"
    )
    assert "matched" in " ".join(m.value for m in first.markdown)
    _approve(first)
    assert "Approve: approved, version 3" in _texts(first.success)
    # Each click reruns the page with fresh data, so the old tab sends
    # version 3; the server refuses instead of deciding again.
    _approve(stale_tab)
    assert "409 invalid_transition" in _texts(stale_tab.error)
    assert _outbox_count(desk.engine, workflow_id) == 1
    reloaded = _open(desk, "ui-approver", workflow_id)
    assert "Decision by ui-approver" in _texts(reloaded.info)
    history = reloaded.dataframe[-2].value
    assert list(history["command"]) == ["start", "check", "approve"]
    assert not reloaded.exception


def test_server_rights_are_shown_not_bypassed(desk: Desk) -> None:
    """Viewer and clerk get 403, another unit sees nothing."""
    workflow_id = _start(desk, "clean.txt")
    for user in ("ui-viewer", "ui-clerk"):
        page = _approve(_open(desk, user, workflow_id))
        assert "403 missing_permission" in _texts(page.error)
    outsider = desk.session("ui-outsider")
    assert "No workflows" in _texts(outsider.info)
    assert _outbox_count(desk.engine, workflow_id) == 0


def test_stale_reconciliation_then_recovery(desk: Desk) -> None:
    """A new order version blocks approval until revise, check, reconcile."""
    workflow_id = _start(desk, "clean.txt")
    order = desk.workflows.get_purchase_order_version(
        desk.workflows.get_workflow(workflow_id).purchase_order_version.version_id  # type: ignore[union-attr]
    ).value
    desk.workflows.record_purchase_order(
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
    assert isinstance(order, PurchaseOrder) and order.purchase_order_id == ORDER_ID
    page = _approve(_open(desk, "ui-approver", workflow_id))
    refused = _texts(page.error)
    assert "409 stale_reconciliation" in refused and "purchase_order_changed" in refused
    clerk = _open(desk, "ui-clerk", workflow_id)
    clerk.button(key="do_revise").click().run()
    assert "Pin the latest order: received" in _texts(clerk.success)
    clerk.button(key="do_check").click().run()
    clerk.button(key="do_reconcile").click().run()
    page = _approve(_open(desk, "ui-approver", workflow_id))
    assert "Approve: approved" in _texts(page.success), _texts(page.error)


def test_document_instruction_and_assistant(desk: Desk) -> None:
    """Text that tries to instruct is shown as data; the assistant changes nothing."""
    workflow_id = _start(desk, "instruction_in_text.txt")
    page = _open(desk, "ui-approver", workflow_id)
    # Tables: the workflow list, then the document fields with sources.
    fields = page.dataframe[1].value
    assert any("SYSTEM" in str(value) for value in fields["value"])
    page.button(key="do_agent").click().run()
    assert "ready_for_approval" in " ".join(m.value for m in page.markdown)
    assert {m.label: m.value for m in page.metric}["Version"] == "2"
    assert _outbox_count(desk.engine, workflow_id) == 0


class _Down:
    """A receiver that is always unavailable."""

    def send(self, message: WebhookMessage, timeout_seconds: float) -> WebhookResponse:
        """Answer 503 without sending."""
        return WebhookResponse(503, "http_503")


def test_exhausted_delivery_is_shown_to_the_approver(desk: Desk) -> None:
    """After the worker gives up, the page says so and names the next step."""
    workflow_id = _start(desk, "clean.txt")
    _approve(_open(desk, "ui-approver", workflow_id))
    page = _open(desk, "ui-approver", workflow_id)
    assert not page.error
    worker = build_delivery_service(
        desk.engine, _Down(), "s" * 32, DeliveryPolicy(max_attempts=1)
    )
    reports = worker.deliver_due()
    assert [(r.workflow_id, r.status.value) for r in reports] == [
        (workflow_id, "exhausted")
    ], reports
    page = _open(desk, "ui-approver", workflow_id)
    assert len(page.error) == 1
    error = _texts(page.error)
    assert "not delivered after 1 attempt (http_503)" in error
    assert "outbox_worker requeue" in error
