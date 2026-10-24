"""Delivery decisions without a database: signing, backoff and state changes."""

import hashlib
import hmac
import json
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from types import TracebackType
from typing import Self
from uuid import UUID, uuid4

import pytest

from app.application.delivery import (
    EVENT_HEADER,
    SIGNATURE_HEADER,
    TIMESTAMP_HEADER,
    DeliveryPolicy,
    OutboxDeliveryService,
    WebhookMessage,
    WebhookResponse,
    build_message,
    sign,
    webhook_body,
)
from app.application.errors import NotFoundError
from app.application.ports import (
    DeliveryAttempt,
    DeliveryStatus,
    DeliveryUpdate,
    OutboxDelivery,
    OutboxEntry,
    OutboxEvent,
    OutboxEventType,
)
from app.domain.errors import DomainValidationError

SECRET = "fictional-signing-secret-" + "x" * 16
START = datetime(2026, 10, 24, 9, 0, tzinfo=UTC)


def _event() -> OutboxEvent:
    """A fictional approval event."""
    return OutboxEvent(
        uuid4(),
        OutboxEventType.INVOICE_APPROVED,
        "WF-1",
        3,
        {"decided_by": "approver-1", "workflow_id": "WF-1"},
        START,
    )


@dataclass
class FakeOutbox:
    """In-memory delivery rows with the repository's state rules."""

    events: dict[UUID, OutboxEvent] = field(default_factory=dict)
    rows: dict[UUID, OutboxDelivery] = field(default_factory=dict)
    log: dict[UUID, list[DeliveryAttempt]] = field(default_factory=dict)

    def add(self, event: OutboxEvent) -> None:
        """Store an event with a pending delivery."""
        self.events[event.event_id] = event
        self.rows[event.event_id] = OutboxDelivery(
            event.event_id, DeliveryStatus.PENDING, 0, 0, 0, START, None, None, None
        )
        self.log[event.event_id] = []

    def claim_due(self, now: datetime) -> tuple[OutboxEvent, OutboxDelivery] | None:
        """The oldest due row."""
        due = [
            row
            for row in self.rows.values()
            if row.status in (DeliveryStatus.PENDING, DeliveryStatus.RETRY_SCHEDULED)
            and row.next_attempt_at <= now
        ]
        if not due:
            return None
        row = min(due, key=lambda item: item.next_attempt_at)
        return self.events[row.event_id], row

    def record_attempt(
        self, event_id: UUID, attempt: DeliveryAttempt, change: DeliveryUpdate
    ) -> None:
        """Append and update like the SQL adapter."""
        self.log[event_id].append(attempt)
        row = self.rows[event_id]
        self.rows[event_id] = replace(
            row,
            status=change.status,
            attempts=row.attempts + 1,
            total_attempts=row.total_attempts + 1,
            next_attempt_at=change.next_attempt_at,
            last_error=change.error_code,
            last_attempt_at=attempt.started_at,
            delivered_at=attempt.started_at
            if change.status is DeliveryStatus.DELIVERED
            else None,
        )

    def get_entry(self, event_id: UUID) -> OutboxEntry | None:
        """One entry or None."""
        if event_id not in self.events:
            return None
        return OutboxEntry(
            self.events[event_id], self.rows[event_id], tuple(self.log[event_id])
        )

    def requeue(self, event_id: UUID, now: datetime) -> bool:
        """Only exhausted rows restart."""
        row = self.rows[event_id]
        if row.status is not DeliveryStatus.EXHAUSTED:
            return False
        self.rows[event_id] = replace(
            row,
            status=DeliveryStatus.PENDING,
            attempts=0,
            requeues=row.requeues + 1,
            next_attempt_at=now,
        )
        return True

    def count_by_status(self) -> dict[DeliveryStatus, int]:
        """Counts per status."""
        counts = dict.fromkeys(DeliveryStatus, 0)
        for row in self.rows.values():
            counts[row.status] += 1
        return counts

    def list_by_status(
        self, status: DeliveryStatus, limit: int
    ) -> tuple[OutboxEntry, ...]:
        """Entries in a status."""
        return tuple(
            entry
            for event_id, row in self.rows.items()
            if row.status is status and (entry := self.get_entry(event_id)) is not None
        )[:limit]


@dataclass
class FakeUnit:
    """A unit of work exposing only the outbox."""

    outbox: FakeOutbox
    commits: int = 0

    def __enter__(self) -> Self:
        """Open."""
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Close."""

    def commit(self) -> None:
        """Count commits."""
        self.commits += 1


class Answers:
    """A sender that replies from a list and records every message."""

    def __init__(self, *answers: WebhookResponse) -> None:
        """Keep the answers in order."""
        self.answers = list(answers)
        self.sent: list[WebhookMessage] = []

    def send(self, message: WebhookMessage, timeout_seconds: float) -> WebhookResponse:
        """Return the next answer."""
        self.sent.append(message)
        return self.answers.pop(0)


class Clock:
    """A clock tests move by hand."""

    def __init__(self) -> None:
        """Start at a fixed moment."""
        self.value = START

    def now(self) -> datetime:
        """Current fake time."""
        return self.value

    def advance(self, seconds: float) -> None:
        """Move forward."""
        self.value += timedelta(seconds=seconds)


def _service(
    sender: Answers, policy: DeliveryPolicy | None = None
) -> tuple[OutboxDeliveryService, FakeOutbox, Clock, OutboxEvent]:
    """A service over one pending event."""
    outbox, clock, event = FakeOutbox(), Clock(), _event()
    outbox.add(event)
    unit = FakeUnit(outbox)
    service = OutboxDeliveryService(
        lambda: unit,  # type: ignore[arg-type,return-value]
        sender,
        SECRET,
        policy or DeliveryPolicy(max_attempts=3, base_delay_seconds=2.0),
        clock.now,
    )
    return service, outbox, clock, event


OK = WebhookResponse(200, None, False)
DOWN = WebhookResponse(503, "http_503")


def test_body_is_canonical_and_signature_is_hmac_of_timestamp_and_body() -> None:
    """Sorted keys, no spaces; the receiver can recompute the same HMAC."""
    event = _event()
    body = webhook_body(event)
    assert (
        body
        == json.dumps(json.loads(body), sort_keys=True, separators=(",", ":")).encode()
    )
    assert json.loads(body)["event_id"] == str(event.event_id)
    message = build_message(event, SECRET.encode(), 1_700_000_000)
    expected = hmac.new(
        SECRET.encode(), b"1700000000." + message.body, hashlib.sha256
    ).hexdigest()
    assert message.headers[SIGNATURE_HEADER] == f"v1={expected}"
    assert message.headers[EVENT_HEADER] == str(event.event_id)
    assert message.headers[TIMESTAMP_HEADER] == "1700000000"
    assert sign(b"other-secret", 1_700_000_000, body) != f"v1={expected}"


def test_backoff_doubles_and_is_capped() -> None:
    """2, 4, 8 … seconds, never above the maximum."""
    policy = DeliveryPolicy(base_delay_seconds=2, max_delay_seconds=10)
    assert [policy.delay(n).total_seconds() for n in (1, 2, 3, 4, 5)] == [
        2,
        4,
        8,
        10,
        10,
    ]


@pytest.mark.parametrize(
    "values",
    [
        {"max_attempts": 0},
        {"max_attempts": 21},
        {"base_delay_seconds": 5, "max_delay_seconds": 1},
        {"timeout_seconds": 0},
        {"timeout_seconds": 31},
    ],
)
def test_unsafe_policies_are_refused(values: dict[str, float]) -> None:
    """No endless retries, no zero or huge timeouts."""
    with pytest.raises(DomainValidationError):
        DeliveryPolicy(**values)  # type: ignore[arg-type]


def test_short_secrets_are_refused() -> None:
    """The service will not sign with a guessable secret."""
    with pytest.raises(DomainValidationError):
        OutboxDeliveryService(lambda: None, Answers(), "short")  # type: ignore[arg-type,return-value]


def test_success_is_logged_and_marks_the_event_delivered() -> None:
    """One attempt, status delivered, the receiver's duplicate flag kept."""
    service, outbox, _clock, event = _service(Answers(OK))
    [report] = service.deliver_due()
    assert (report.seq, report.status) == (1, DeliveryStatus.DELIVERED)
    entry = outbox.get_entry(event.event_id)
    assert entry is not None and entry.delivery.status is DeliveryStatus.DELIVERED
    assert entry.attempts[0].error_code is None
    assert entry.attempts[0].duplicate is False
    assert service.deliver_due() == ()


def test_failures_back_off_then_exhaust() -> None:
    """Retries wait 2 s then 4 s; the third failure exhausts the event."""
    sender = Answers(DOWN, DOWN, DOWN)
    service, outbox, clock, event = _service(sender)
    assert [r.status for r in service.deliver_due()] == [DeliveryStatus.RETRY_SCHEDULED]
    assert service.deliver_due() == ()  # not due yet
    clock.advance(2)
    assert service.deliver_due()[0].status is DeliveryStatus.RETRY_SCHEDULED
    clock.advance(3)
    assert service.deliver_due() == ()  # 4 s backoff, only 3 s passed
    clock.advance(1)
    [last] = service.deliver_due()
    assert (last.seq, last.status, last.error_code) == (
        3,
        DeliveryStatus.EXHAUSTED,
        "http_503",
    )
    clock.advance(3600)
    assert service.deliver_due() == ()  # exhausted events are never retried
    assert service.summary()["counts"] == {
        "pending": 0,
        "retry_scheduled": 0,
        "delivered": 0,
        "exhausted": 1,
    }
    assert len(sender.sent) == 3
    # Every attempt is signed again with its own timestamp.
    assert len({m.headers[TIMESTAMP_HEADER] for m in sender.sent}) == 3


def test_transport_errors_and_redirects_count_as_failures() -> None:
    """No status code still fails; the code says why."""
    service, outbox, _clock, event = _service(Answers(WebhookResponse(None, "timeout")))
    [report] = service.deliver_due()
    assert (report.status, report.error_code) == (
        DeliveryStatus.RETRY_SCHEDULED,
        "timeout",
    )
    service, outbox, _clock, event = _service(
        Answers(WebhookResponse(302, "redirect_refused"))
    )
    assert service.deliver_due()[0].error_code == "redirect_refused"


def test_requeue_starts_a_new_round_and_keeps_numbering() -> None:
    """Only exhausted events restart; attempt numbers continue."""
    service, outbox, clock, event = _service(
        Answers(DOWN, OK), DeliveryPolicy(max_attempts=1)
    )
    with pytest.raises(DomainValidationError, match="exhausted"):
        service.requeue(event.event_id)  # still pending
    assert service.deliver_due()[0].status is DeliveryStatus.EXHAUSTED
    entry = service.requeue(event.event_id)
    assert (entry.delivery.status, entry.delivery.requeues) == (
        DeliveryStatus.PENDING,
        1,
    )
    [report] = service.deliver_due()
    assert (report.seq, report.status) == (2, DeliveryStatus.DELIVERED)
    with pytest.raises(NotFoundError):
        service.requeue(uuid4())


def test_limit_bounds_one_call() -> None:
    """deliver_due stops after ``limit`` attempts even if more are due."""
    service, outbox, _clock, _event_ = _service(Answers(OK, OK))
    outbox.add(_event())
    assert len(service.deliver_due(limit=1)) == 1
    assert len(service.deliver_due(limit=1)) == 1
    with pytest.raises(DomainValidationError):
        service.deliver_due(limit=0)
