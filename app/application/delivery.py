"""Deliver outbox events to the fixed sandbox receiver with bounded retries.

Delivery is at least once. One event is claimed per transaction with a row
lock, sent, and its attempt is written before the lock is released. If the
worker dies after sending but before committing, the event is sent again;
the receiver deduplicates by event ID, so the second send creates no second
action there. Exactly-once network delivery is not claimed.

The destination is not a parameter of anything here: the sender adapter is
built once from settings, and an event's payload holds only IDs and hashes.
"""

import hashlib
import hmac
import json
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol
from uuid import UUID

from app.application.errors import NotFoundError
from app.application.ports import (
    AttemptOutcome,
    DeliveryAttempt,
    DeliveryStatus,
    DeliveryUpdate,
    OutboxEntry,
    OutboxEvent,
    UnitOfWork,
)
from app.domain.errors import DomainValidationError

EVENT_HEADER = "X-EWA-Event-Id"
TIMESTAMP_HEADER = "X-EWA-Timestamp"
SIGNATURE_HEADER = "X-EWA-Signature"
SIGNATURE_VERSION = "v1"


@dataclass(frozen=True, slots=True)
class DeliveryPolicy:
    """Retry bounds; exponential backoff without jitter keeps runs reproducible."""

    max_attempts: int = 5
    base_delay_seconds: float = 2.0
    max_delay_seconds: float = 60.0
    timeout_seconds: float = 5.0

    def __post_init__(self) -> None:
        """Reject limits that would retry forever or never wait."""
        if type(self.max_attempts) is not int or not 1 <= self.max_attempts <= 20:
            raise DomainValidationError(
                "invalid_limit", "max_attempts", "Use 1–20 attempts."
            )
        if not 0 <= self.base_delay_seconds <= self.max_delay_seconds <= 3600:
            raise DomainValidationError(
                "invalid_limit",
                "base_delay_seconds",
                "Use 0 <= base delay <= max delay <= 3600 seconds.",
            )
        if not 0 < self.timeout_seconds <= 30:
            raise DomainValidationError(
                "invalid_limit", "timeout_seconds", "Use a timeout of 0–30 seconds."
            )

    def delay(self, failed_attempts: int) -> timedelta:
        """Wait base, 2×base, 4×base … after each failure, capped at the maximum."""
        seconds = self.base_delay_seconds * 2 ** max(failed_attempts - 1, 0)
        return timedelta(seconds=min(seconds, self.max_delay_seconds))


@dataclass(frozen=True, slots=True)
class WebhookMessage:
    """A signed request body and its headers; the URL is the sender's own."""

    event_id: UUID
    body: bytes
    headers: dict[str, str]


@dataclass(frozen=True, slots=True)
class WebhookResponse:
    """What came back: a status code, a transport error code, or both."""

    http_status: int | None
    error_code: str | None
    duplicate: bool | None = None

    @property
    def delivered(self) -> bool:
        """Only a 2xx answer counts as delivered."""
        return self.http_status is not None and 200 <= self.http_status < 300


class WebhookSender(Protocol):
    """Send one message to the configured receiver; never raises for I/O."""

    def send(self, message: WebhookMessage, timeout_seconds: float) -> WebhookResponse:
        """Return the receiver's status or a transport error code."""
        ...


def webhook_body(event: OutboxEvent) -> bytes:
    """Canonical JSON: sorted keys and no spaces, so both sides hash the same bytes."""
    created = None if event.created_at is None else event.created_at.isoformat()
    document = {
        "event_id": str(event.event_id),
        "event_type": event.event_type.value,
        "workflow_id": event.workflow_id,
        "workflow_version": event.workflow_version,
        "created_at": created,
        "payload": event.payload,
    }
    return json.dumps(
        document, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def sign(secret: bytes, timestamp: int, body: bytes) -> str:
    """HMAC-SHA256 over ``timestamp.body``; the timestamp limits replays."""
    digest = hmac.new(secret, f"{timestamp}.".encode() + body, hashlib.sha256)
    return f"{SIGNATURE_VERSION}={digest.hexdigest()}"


def build_message(event: OutboxEvent, secret: bytes, timestamp: int) -> WebhookMessage:
    """Sign the event's body; the event ID travels in a header and in the body."""
    body = webhook_body(event)
    return WebhookMessage(
        event_id=event.event_id,
        body=body,
        headers={
            "Content-Type": "application/json",
            EVENT_HEADER: str(event.event_id),
            TIMESTAMP_HEADER: str(timestamp),
            SIGNATURE_HEADER: sign(secret, timestamp, body),
        },
    )


@dataclass(frozen=True, slots=True)
class AttemptReport:
    """One attempt as the worker reports it."""

    event_id: UUID
    workflow_id: str
    seq: int
    status: DeliveryStatus
    http_status: int | None
    error_code: str | None
    duplicate: bool | None


class OutboxDeliveryService:
    """Claim due events one by one, send them and record every attempt."""

    def __init__(
        self,
        unit_of_work: Callable[[], UnitOfWork],
        sender: WebhookSender,
        secret: str,
        policy: DeliveryPolicy | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        """Receive storage, transport, the signing secret, bounds and a clock."""
        if len(secret) < 32:
            raise DomainValidationError(
                "invalid_secret", "secret", "Use a secret of at least 32 characters."
            )
        self._unit_of_work = unit_of_work
        self._sender = sender
        self._secret = secret.encode("utf-8")
        self._policy = DeliveryPolicy() if policy is None else policy
        self._clock = clock or (lambda: datetime.now(UTC))

    @property
    def policy(self) -> DeliveryPolicy:
        """The retry bounds in use."""
        return self._policy

    def deliver_one(self) -> AttemptReport | None:
        """Deliver the oldest due event, or return None when nothing is due.

        The row lock is held during the send. Other workers skip this event,
        and the attempt is committed with the new state, or not at all.
        """
        with self._unit_of_work() as unit:
            now = self._clock()
            claimed = unit.outbox.claim_due(now)
            if claimed is None:
                return None
            event, delivery = claimed
            message = build_message(event, self._secret, int(now.timestamp()))
            started = time.monotonic()
            response = self._sender.send(message, self._policy.timeout_seconds)
            duration_ms = int((time.monotonic() - started) * 1000)
            attempts = delivery.attempts + 1
            if response.delivered:
                status = DeliveryStatus.DELIVERED
                error_code = None
                next_at = now
            else:
                error_code = response.error_code or f"http_{response.http_status}"
                if attempts >= self._policy.max_attempts:
                    status = DeliveryStatus.EXHAUSTED
                    next_at = now
                else:
                    status = DeliveryStatus.RETRY_SCHEDULED
                    next_at = now + self._policy.delay(attempts)
            seq = delivery.total_attempts + 1
            unit.outbox.record_attempt(
                event.event_id,
                DeliveryAttempt(
                    seq=seq,
                    started_at=now,
                    duration_ms=duration_ms,
                    outcome=AttemptOutcome.DELIVERED
                    if status is DeliveryStatus.DELIVERED
                    else AttemptOutcome.FAILED,
                    http_status=response.http_status,
                    error_code=error_code,
                    duplicate=response.duplicate,
                ),
                DeliveryUpdate(status, next_at, error_code),
            )
            unit.commit()
        return AttemptReport(
            event_id=event.event_id,
            workflow_id=event.workflow_id,
            seq=seq,
            status=status,
            http_status=response.http_status,
            error_code=error_code,
            duplicate=response.duplicate,
        )

    def deliver_due(self, limit: int = 100) -> tuple[AttemptReport, ...]:
        """Deliver due events until none is left or ``limit`` attempts were made."""
        if type(limit) is not int or limit < 1:
            raise DomainValidationError("invalid_limit", "limit", "Use limit >= 1.")
        reports: list[AttemptReport] = []
        while len(reports) < limit:
            report = self.deliver_one()
            if report is None:
                break
            reports.append(report)
        return tuple(reports)

    def requeue(self, event_id: UUID) -> OutboxEntry:
        """Give an exhausted event a new round of attempts; the log is kept."""
        with self._unit_of_work() as unit:
            if unit.outbox.get_entry(event_id) is None:
                raise NotFoundError("Outbox event was not found.")
            if not unit.outbox.requeue(event_id, self._clock()):
                raise DomainValidationError(
                    "invalid_transition",
                    "status",
                    "Only an exhausted delivery can be requeued.",
                )
            unit.commit()
            entry = unit.outbox.get_entry(event_id)
        if entry is None:  # pragma: no cover - the row was just updated
            raise NotFoundError("Outbox event was not found.")
        return entry

    def summary(self, limit: int = 20) -> dict[str, object]:
        """Counts per status and the exhausted events an operator should see."""
        with self._unit_of_work() as unit:
            counts = unit.outbox.count_by_status()
            exhausted = unit.outbox.list_by_status(DeliveryStatus.EXHAUSTED, limit)
        return {
            "counts": {status.value: counts[status] for status in DeliveryStatus},
            "exhausted": [
                {
                    "event_id": str(entry.event.event_id),
                    "workflow_id": entry.event.workflow_id,
                    "event_type": entry.event.event_type.value,
                    "attempts": entry.delivery.attempts,
                    "last_error": entry.delivery.last_error,
                }
                for entry in exhausted
            ],
        }
