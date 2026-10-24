"""SQLAlchemy storage for outbox delivery state and the attempt log."""

from collections import defaultdict
from datetime import datetime, timedelta
from typing import Any, cast
from uuid import UUID

from sqlalchemy import CursorResult, select, update
from sqlalchemy import func as sql_func
from sqlalchemy.orm import Session

from app.application.ports import (
    AttemptOutcome,
    DeliveryAttempt,
    DeliveryStatus,
    DeliveryUpdate,
    OutboxDelivery,
    OutboxEntry,
    OutboxEvent,
    OutboxEventType,
)
from app.infrastructure.db.inserts import insert_rows
from app.infrastructure.db.models import (
    OutboxAttemptRow,
    OutboxDeliveryRow,
    OutboxEventRow,
)
from app.infrastructure.db.repository import reconstructing

_DUE = (DeliveryStatus.PENDING.value, DeliveryStatus.RETRY_SCHEDULED.value)


def _event(row: OutboxEventRow) -> OutboxEvent:
    """Copy a stored event."""
    return OutboxEvent(
        event_id=row.id,
        event_type=OutboxEventType(row.event_type),
        workflow_id=row.workflow_id,
        workflow_version=row.workflow_version,
        payload=dict(row.payload),
        created_at=row.created_at,
    )


def _delivery(row: OutboxDeliveryRow) -> OutboxDelivery:
    """Copy a delivery row."""
    return OutboxDelivery(
        event_id=row.event_id,
        status=DeliveryStatus(row.status),
        attempts=row.attempts,
        total_attempts=row.total_attempts,
        requeues=row.requeues,
        next_attempt_at=row.next_attempt_at,
        last_error=row.last_error,
        last_attempt_at=row.last_attempt_at,
        delivered_at=row.delivered_at,
    )


def _attempt(row: OutboxAttemptRow) -> DeliveryAttempt:
    """Copy one logged attempt."""
    return DeliveryAttempt(
        seq=row.seq,
        started_at=row.started_at,
        duration_ms=row.duration_ms,
        outcome=AttemptOutcome(row.outcome),
        http_status=row.http_status,
        error_code=row.error_code,
        duplicate=row.duplicate,
    )


class SqlAlchemyOutboxRepository:
    """Outbox delivery storage bound to the unit of work's session."""

    def __init__(self, session: Session) -> None:
        """Use the shared session; this class never commits."""
        self._session = session

    def claim_due(self, now: datetime) -> tuple[OutboxEvent, OutboxDelivery] | None:
        """Lock the oldest due delivery row until commit.

        ``SKIP LOCKED`` lets several workers run at once: each one takes a
        different event, and a crashed worker's lock disappears with its
        connection, so the event is picked up again.
        """
        found = self._session.execute(
            select(OutboxDeliveryRow, OutboxEventRow)
            .join(OutboxEventRow, OutboxEventRow.id == OutboxDeliveryRow.event_id)
            .where(
                OutboxDeliveryRow.status.in_(_DUE),
                OutboxDeliveryRow.next_attempt_at <= now,
            )
            .order_by(OutboxDeliveryRow.next_attempt_at, OutboxDeliveryRow.event_id)
            .limit(1)
            .with_for_update(skip_locked=True, of=OutboxDeliveryRow)
        ).first()
        if found is None:
            return None
        delivery_row, event_row = found
        with reconstructing("outbox delivery"):
            return _event(event_row), _delivery(delivery_row)

    def record_attempt(
        self, event_id: UUID, attempt: DeliveryAttempt, change: DeliveryUpdate
    ) -> None:
        """Append the attempt, then move the claimed row to its new state."""
        insert_rows(
            self._session,
            OutboxAttemptRow(
                event_id=event_id,
                seq=attempt.seq,
                started_at=attempt.started_at,
                duration_ms=attempt.duration_ms,
                outcome=attempt.outcome.value,
                http_status=attempt.http_status,
                error_code=attempt.error_code,
                duplicate=attempt.duplicate,
            ),
        )
        finished = attempt.started_at + timedelta(milliseconds=attempt.duration_ms)
        delivered = change.status is DeliveryStatus.DELIVERED
        self._session.execute(
            update(OutboxDeliveryRow)
            .where(OutboxDeliveryRow.event_id == event_id)
            .values(
                status=change.status.value,
                attempts=OutboxDeliveryRow.attempts + 1,
                total_attempts=OutboxDeliveryRow.total_attempts + 1,
                next_attempt_at=change.next_attempt_at,
                last_error=change.error_code,
                last_attempt_at=attempt.started_at,
                delivered_at=finished if delivered else None,
                updated_at=sql_func.now(),
            )
            .execution_options(synchronize_session=False)
        )

    def _entries(self, events: list[OutboxEventRow]) -> tuple[OutboxEntry, ...]:
        """Attach delivery rows and attempt logs to the given events."""
        ids = [row.id for row in events]
        if not ids:
            return ()
        deliveries = {
            row.event_id: row
            for row in self._session.scalars(
                select(OutboxDeliveryRow).where(OutboxDeliveryRow.event_id.in_(ids))
            )
        }
        attempts: dict[UUID, list[OutboxAttemptRow]] = defaultdict(list)
        for row in self._session.scalars(
            select(OutboxAttemptRow)
            .where(OutboxAttemptRow.event_id.in_(ids))
            .order_by(OutboxAttemptRow.event_id, OutboxAttemptRow.seq)
        ):
            attempts[row.event_id].append(row)
        with reconstructing("outbox delivery"):
            return tuple(
                OutboxEntry(
                    _event(row),
                    _delivery(deliveries[row.id]),
                    tuple(_attempt(item) for item in attempts[row.id]),
                )
                for row in events
            )

    def list_entries(self, workflow_id: str) -> tuple[OutboxEntry, ...]:
        """A workflow's events oldest first, each with state and attempts."""
        events = list(
            self._session.scalars(
                select(OutboxEventRow)
                .where(OutboxEventRow.workflow_id == workflow_id)
                .order_by(OutboxEventRow.created_at, OutboxEventRow.id)
            )
        )
        return self._entries(events)

    def get_entry(self, event_id: UUID) -> OutboxEntry | None:
        """One event with state and attempts, or None."""
        row = self._session.get(OutboxEventRow, event_id)
        return None if row is None else self._entries([row])[0]

    def requeue(self, event_id: UUID, now: datetime) -> bool:
        """Reset the round counter of an exhausted delivery; history stays."""
        result = cast(
            CursorResult[Any],
            self._session.execute(
                update(OutboxDeliveryRow)
                .where(
                    OutboxDeliveryRow.event_id == event_id,
                    OutboxDeliveryRow.status == DeliveryStatus.EXHAUSTED.value,
                )
                .values(
                    status=DeliveryStatus.PENDING.value,
                    attempts=0,
                    requeues=OutboxDeliveryRow.requeues + 1,
                    next_attempt_at=now,
                    updated_at=sql_func.now(),
                )
                .execution_options(synchronize_session=False)
            ),
        )
        return result.rowcount == 1

    def count_by_status(self) -> dict[DeliveryStatus, int]:
        """Every status with its count, zero included."""
        counts = dict.fromkeys(DeliveryStatus, 0)
        for status, count in self._session.execute(
            select(OutboxDeliveryRow.status, sql_func.count()).group_by(
                OutboxDeliveryRow.status
            )
        ):
            counts[DeliveryStatus(status)] = int(count)
        return counts

    def list_by_status(
        self, status: DeliveryStatus, limit: int
    ) -> tuple[OutboxEntry, ...]:
        """The longest-waiting entries in one status."""
        events = list(
            self._session.scalars(
                select(OutboxEventRow)
                .join(
                    OutboxDeliveryRow, OutboxDeliveryRow.event_id == OutboxEventRow.id
                )
                .where(OutboxDeliveryRow.status == status.value)
                .order_by(OutboxDeliveryRow.updated_at, OutboxEventRow.id)
                .limit(limit)
            )
        )
        return self._entries(events)
