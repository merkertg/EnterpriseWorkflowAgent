"""The smoke check against a real HTTP server, receiver and worker thread."""

import threading
from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import Engine

from app.api.dependencies import Services
from app.config import Settings
from app.domain.documents import DocumentLimits
from app.fictional_desk import FictionalDesk
from app.infrastructure.db.session import build_agent_service, build_delivery_service
from app.infrastructure.llm.demo import DemoRulesProvider
from app.infrastructure.webhook.sender import UrllibWebhookSender
from app.main import create_app
from app.smoke import run_smoke
from sandbox.receiver import ReceiverStore, create_receiver, serving, serving_app

pytestmark = pytest.mark.postgres
SECRET = "fictional-smoke-secret-" + "m" * 16


@pytest.fixture
def api_url(engine: Engine) -> Iterator[str]:
    """The API over the test database on a free loopback port."""
    desk = FictionalDesk(engine, "pytest")
    services = Services(
        workflows=desk.workflows,
        documents=desk.documents,
        reconciliations=desk.reconciliations,
        users=desk.users,
        agent=build_agent_service(engine, DemoRulesProvider()),
        max_document_bytes=DocumentLimits().max_bytes,
    )
    with serving_app(create_app(Settings(), services=services)) as url:
        yield url


def test_scenario_reaches_delivery(
    engine: Engine, api_url: str, tmp_path: Path
) -> None:
    """Every step matches, and the worker delivers the approval event."""
    store = ReceiverStore(tmp_path / "receiver.sqlite3")
    stop = threading.Event()
    with serving(create_receiver(store, SECRET)) as webhook:
        worker = build_delivery_service(engine, UrllibWebhookSender(webhook), SECRET)

        def deliver() -> None:
            """Deliver due events until the test ends."""
            while not stop.is_set():
                worker.deliver_due()
                stop.wait(0.2)

        thread = threading.Thread(target=deliver, daemon=True)
        thread.start()
        try:
            report = run_smoke(engine, api_url, wait_seconds=20)
        finally:
            stop.set()
            thread.join(timeout=10)
    assert report["ok"], report
    assert [step["step"] for step in report["steps"]] == [
        "health",
        "sign-in",
        "upload",
        "start",
        "check",
        "reconcile",
        "approve",
        "delivery",
    ]
    assert [action.workflow_id for action in store.actions()] == [report["workflow_id"]]


def test_delivery_wait_can_be_skipped(engine: Engine, api_url: str) -> None:
    """Without a worker, --wait 0 still checks the HTTP scenario."""
    report = run_smoke(engine, api_url, wait_seconds=0)
    assert report["ok"] and report["steps"][-1]["step"] == "approve"
