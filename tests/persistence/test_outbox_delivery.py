"""Outbox delivery against PostgreSQL and a real loopback receiver.

Covers the roadmap's conditions: a failed delivery never loses the event, a
repeat creates no second action at the receiver, and exhaustion is visible
to employees through the API.
"""

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text

from app.api.dependencies import Services
from app.application.agent import AgentLimits
from app.application.delivery import (
    DeliveryPolicy,
    OutboxDeliveryService,
    WebhookMessage,
    WebhookResponse,
)
from app.application.errors import AgentRunLimitError, NotFoundError
from app.application.ports import DeliveryStatus
from app.config import Settings
from app.domain.documents import DocumentLimits
from app.domain.errors import DomainValidationError
from app.fictional_desk import FictionalDesk
from app.infrastructure.db.session import (
    build_agent_service,
    build_delivery_service,
    create_session_factory,
)
from app.infrastructure.db.unit_of_work import SqlAlchemyUnitOfWork
from app.infrastructure.llm.demo import DemoRulesProvider
from app.infrastructure.webhook.sender import UrllibWebhookSender
from app.main import create_app
from sandbox.receiver import Behaviour, Mode, ReceiverStore, create_receiver, serving
from tests.persistence.conftest import alembic_config

pytestmark = pytest.mark.postgres
SECRET = "fictional-delivery-secret-" + "d" * 16
FAST = DeliveryPolicy(max_attempts=2, base_delay_seconds=0.0, timeout_seconds=2.0)


@dataclass(frozen=True, slots=True)
class Line:
    """A desk plus a live receiver."""

    engine: Engine
    desk: FictionalDesk
    url: str
    store: ReceiverStore
    behaviour: Behaviour

    def worker(self, policy: DeliveryPolicy = FAST) -> OutboxDeliveryService:
        """A worker for the live receiver; zero backoff keeps tests fast."""
        return build_delivery_service(
            self.engine, UrllibWebhookSender(self.url), SECRET, policy
        )

    def actions(self, workflow_id: str) -> list[int]:
        """Receipt counts of the receiver's actions for one workflow."""
        return [
            a.receipts for a in self.store.actions() if a.workflow_id == workflow_id
        ]


@pytest.fixture
def line(engine: Engine, tmp_path: Path) -> Iterator[Line]:
    """A fictional desk on the test database and a receiver on loopback."""
    store = ReceiverStore(tmp_path / "receiver.sqlite3")
    behaviour = Behaviour()
    with serving(create_receiver(store, SECRET, behaviour)) as url:
        yield Line(engine, FictionalDesk(engine, "pytest"), url, store, behaviour)


def _api(line: Line) -> TestClient:
    """The HTTP API over the same database."""
    desk = line.desk
    services = Services(
        workflows=desk.workflows,
        documents=desk.documents,
        reconciliations=desk.reconciliations,
        users=desk.users,
        agent=build_agent_service(line.engine, DemoRulesProvider()),
        max_document_bytes=DocumentLimits().max_bytes,
    )
    return TestClient(create_app(Settings(), services=services))


def test_api_shows_pending_then_delivered_with_the_attempt(line: Line) -> None:
    """The decision's transaction creates the delivery; the worker finishes it."""
    workflow_id = line.desk.approved("A")
    token = line.desk.users.issue_token(
        line.desk.team.viewer.user_id, label="pytest"
    ).secret
    headers = {"Authorization": f"Bearer {token}"}
    with _api(line) as api:
        [event] = api.get(f"/workflows/{workflow_id}/outbox", headers=headers).json()
        assert (event["delivery"]["status"], event["attempts"]) == ("pending", [])
        [report] = line.worker().deliver_due()
        assert report.status is DeliveryStatus.DELIVERED
        [event] = api.get(f"/workflows/{workflow_id}/outbox", headers=headers).json()
    assert event["delivery"]["status"] == "delivered"
    assert event["delivery"]["delivered_at"] is not None
    [attempt] = event["attempts"]
    assert (attempt["seq"], attempt["http_status"], attempt["outcome"]) == (
        1,
        200,
        "delivered",
    )
    assert line.actions(workflow_id) == [1]


class _SendThenCrash:
    """Sends for real, then dies before the worker can commit."""

    def __init__(self, url: str) -> None:
        """Wrap the real sender."""
        self._sender = UrllibWebhookSender(url)

    def send(self, message: WebhookMessage, timeout_seconds: float) -> WebhookResponse:
        """Deliver, then fail as a crashed process would."""
        self._sender.send(message, timeout_seconds)
        raise RuntimeError("worker crashed after sending")


def test_a_crash_after_sending_loses_nothing_and_acts_once(line: Line) -> None:
    """The rolled-back attempt leaves the event due; the resend is a duplicate."""
    workflow_id = line.desk.approved("B")
    crashing = build_delivery_service(
        line.engine, _SendThenCrash(line.url), SECRET, FAST
    )
    with pytest.raises(RuntimeError):
        crashing.deliver_due()
    [entry] = line.desk.workflows.outbox_events(workflow_id)
    assert (entry.delivery.status, entry.delivery.attempts) == (
        DeliveryStatus.PENDING,
        0,
    )
    [report] = line.worker().deliver_due()
    assert (report.status, report.duplicate) == (DeliveryStatus.DELIVERED, True)
    assert line.actions(workflow_id) == [2]  # one action, received twice


def test_a_claimed_event_is_skipped_by_other_workers(line: Line) -> None:
    """SKIP LOCKED: a second worker does not wait for or take a locked row."""
    line.desk.approved("C")
    factory = create_session_factory(line.engine)
    with SqlAlchemyUnitOfWork(factory) as first:
        claimed = first.outbox.claim_due(_far_future())
        assert claimed is not None
        with SqlAlchemyUnitOfWork(factory) as second:
            assert second.outbox.claim_due(_far_future()) is None
    # The first transaction ended without commit: the event is due again.
    with SqlAlchemyUnitOfWork(factory) as third:
        again = third.outbox.claim_due(_far_future())
        assert again is not None and again[0].event_id == claimed[0].event_id


def _far_future() -> datetime:
    """A moment after every scheduled attempt."""
    return datetime.now(UTC) + timedelta(days=1)


def test_exhaustion_requeue_and_summary(line: Line) -> None:
    """Two failures exhaust; only an exhausted event can be requeued."""
    workflow_id = line.desk.approved("D")
    line.behaviour.set(Mode.FAIL)
    worker = line.worker()
    with pytest.raises(DomainValidationError):
        worker.requeue(line.desk.workflows.outbox_events(workflow_id)[0].event.event_id)
    statuses = [r.status for r in worker.deliver_due()]
    statuses += [r.status for r in worker.deliver_due()]
    assert statuses == [DeliveryStatus.RETRY_SCHEDULED, DeliveryStatus.EXHAUSTED]
    summary = worker.summary()
    assert summary["counts"] == {
        "pending": 0,
        "retry_scheduled": 0,
        "delivered": 0,
        "exhausted": 1,
    }
    listed = summary["exhausted"]
    assert isinstance(listed, list)
    [exhausted] = listed
    assert exhausted["workflow_id"] == workflow_id
    assert exhausted["last_error"] == "http_503"
    assert line.actions(workflow_id) == []
    line.behaviour.set(Mode.ACCEPT)
    event_id = line.desk.workflows.outbox_events(workflow_id)[0].event.event_id
    requeued = worker.requeue(event_id)
    assert (requeued.delivery.status, requeued.delivery.requeues) == (
        DeliveryStatus.PENDING,
        1,
    )
    [report] = worker.deliver_due()
    assert (report.seq, report.status) == (3, DeliveryStatus.DELIVERED)
    with pytest.raises(NotFoundError):
        worker.requeue(uuid4())


def test_attempt_rows_cannot_be_rewritten(line: Line) -> None:
    """The log is append-only like every other history table."""
    line.desk.approved("E")
    line.worker().deliver_due()
    with pytest.raises(Exception, match="append-only"), line.engine.begin() as conn:
        conn.execute(text("UPDATE outbox_attempts SET outcome = 'failed'"))


def test_upgrade_backfills_deliveries_for_older_events(
    line: Line, test_database_url: str
) -> None:
    """Events written before revision 0006 become pending deliveries."""
    workflow_id = line.desk.approved("F")
    [entry] = line.desk.workflows.outbox_events(workflow_id)
    config = alembic_config(test_database_url)
    command.downgrade(config, "0005")
    command.upgrade(config, "head")
    [after] = line.desk.workflows.outbox_events(workflow_id)
    assert after.delivery.status is DeliveryStatus.PENDING
    assert after.delivery.next_attempt_at == entry.event.created_at


def test_agent_quota_is_per_workflow_and_answers_429(line: Line) -> None:
    """The second run of one workflow is refused; another workflow still runs."""
    desk = line.desk
    first, second = desk.submit("Q1"), desk.submit("Q2")
    agent = build_agent_service(
        line.engine, DemoRulesProvider(), AgentLimits(max_runs_per_hour=1)
    )
    agent.run(first, actor=desk.team.approver)
    with pytest.raises(AgentRunLimitError):
        agent.run(first, actor=desk.team.approver)
    agent.run(second, actor=desk.team.approver)
    token = desk.users.issue_token(desk.team.approver.user_id, label="pytest").secret
    services = Services(
        workflows=desk.workflows,
        documents=desk.documents,
        reconciliations=desk.reconciliations,
        users=desk.users,
        agent=agent,
        max_document_bytes=DocumentLimits().max_bytes,
    )
    with TestClient(create_app(Settings(), services=services)) as api:
        answer = api.post(
            f"/workflows/{first}/agent-runs",
            headers={"Authorization": f"Bearer {token}"},
        )
    assert answer.status_code == 429
    assert answer.json()["error"]["code"] == "agent_run_limit"
