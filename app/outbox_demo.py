"""Run the fictional Stage 09 delivery scenario against PostgreSQL, offline.

``python -m app.outbox_demo`` starts the sandbox receiver on a free loopback
port inside this process, approves fictional invoices and delivers their
events through each failure on purpose: a receiver that is down for a while,
an answer lost after the receiver acted, a timeout, a wrong signing secret,
attempts running out followed by a requeue, and two workers at once.

Instead of sleeping between retries, the worker and the receiver share a
clock that the demo moves forward. Nothing leaves 127.0.0.1.
"""

import argparse
import json
import secrets
import sys
import tempfile
import threading
import time
from collections.abc import Sequence
from pathlib import Path

from sqlalchemy import Engine
from sqlalchemy.exc import OperationalError, ProgrammingError

from app.application.delivery import (
    AttemptReport,
    DeliveryPolicy,
    OutboxDeliveryService,
)
from app.application.errors import ApplicationError
from app.config import Settings
from app.domain.errors import DomainValidationError
from app.fictional_desk import FictionalDesk, ShiftedClock
from app.infrastructure.db.session import build_delivery_service, create_database_engine
from app.infrastructure.webhook.sender import UrllibWebhookSender
from app.outbox_worker import describe_attempt
from sandbox.receiver import Behaviour, Mode, ReceiverStore, create_receiver, serving

_SOURCE = "outbox-demo"
# Short bounds keep the demo quick; the slow receiver answers after 1.5 s.
DEMO_POLICY = DeliveryPolicy(
    max_attempts=4, base_delay_seconds=2.0, max_delay_seconds=60.0, timeout_seconds=1.0
)


def _attempts(reports: Sequence[AttemptReport]) -> list[dict[str, object]]:
    """Attempts as printed by the worker."""
    return [describe_attempt(report) for report in reports]


def run_outbox_demo(engine: Engine) -> dict[str, object]:
    """Approve fictional invoices and deliver their events in every mode."""
    desk = FictionalDesk(engine, _SOURCE)
    secret = secrets.token_urlsafe(32)
    clock = ShiftedClock()
    behaviour = Behaviour()
    scenarios: dict[str, object] = {}
    with tempfile.TemporaryDirectory(prefix="ewa-receiver-") as folder:
        store = ReceiverStore(Path(folder) / "receiver.sqlite3")
        receiver = create_receiver(store, secret, behaviour, clock.now)
        with serving(receiver) as url:

            def worker(key: str = secret) -> OutboxDeliveryService:
                """A worker over the demo receiver with the given secret."""
                return build_delivery_service(
                    engine, UrllibWebhookSender(url), key, DEMO_POLICY, clock.now
                )

            def receipts(workflow_id: str) -> int:
                """How often the receiver got this workflow's event (0: no action)."""
                return sum(
                    action.receipts
                    for action in store.actions()
                    if action.workflow_id == workflow_id
                )

            def actions(workflow_id: str) -> int:
                """How many actions the receiver took for this workflow."""
                return sum(1 for a in store.actions() if a.workflow_id == workflow_id)

            def status(workflow_id: str) -> str:
                """Delivery status as the viewer sees it through the service."""
                [entry] = desk.workflows.outbox_events(
                    workflow_id, actor=desk.team.viewer
                )
                return entry.delivery.status.value

            # Events of earlier runs that are still pending in this database
            # go to this temporary receiver first, so every scenario below is
            # about its own event. Their count is reported.
            scenarios["earlier_events_sent_to_demo_receiver"] = len(
                worker().deliver_due(limit=1000)
            )

            first = desk.approved("A")
            scenarios["A: delivered on the first attempt"] = {
                "attempts": _attempts(worker().deliver_due()),
                "status": status(first),
                "receiver_actions": actions(first),
            }

            down = desk.approved("B")
            behaviour.set(Mode.FAIL, remaining=2)
            steps = [_attempts(worker().deliver_due())]
            steps.append(_attempts(worker().deliver_due()))  # not due yet
            clock.advance(2)
            steps.append(_attempts(worker().deliver_due()))
            clock.advance(4)
            steps.append(_attempts(worker().deliver_due()))
            scenarios["B: receiver down twice, backoff 2 s then 4 s"] = {
                "rounds": steps,
                "status": status(down),
                "receiver_actions": actions(down),
            }

            lost = desk.approved("C")
            behaviour.set(Mode.LOSE_ACK, remaining=1)
            steps = [_attempts(worker().deliver_due())]
            clock.advance(2)
            steps.append(_attempts(worker().deliver_due()))
            scenarios["C: answer lost after the receiver acted"] = {
                "rounds": steps,
                "status": status(lost),
                "receiver_actions": actions(lost),
                "receiver_receipts": receipts(lost),
            }

            slow = desk.approved("D")
            behaviour.set(Mode.SLOW, remaining=1, delay_seconds=1.5)
            steps = [_attempts(worker().deliver_due())]
            # The receiver is still working after the worker gave up; let it
            # finish, so the retry deterministically finds the action.
            time.sleep(1.0)
            clock.advance(2)
            steps.append(_attempts(worker().deliver_due()))
            scenarios["D: timeout while the receiver was still working"] = {
                "rounds": steps,
                "status": status(slow),
                "receiver_actions": actions(slow),
                "receiver_receipts": receipts(slow),
            }

            forged = desk.approved("E")
            wrong = worker(secrets.token_urlsafe(32))
            steps = [_attempts(wrong.deliver_due())]
            clock.advance(2)
            steps.append(_attempts(worker().deliver_due()))
            scenarios["E: wrong secret refused, right secret accepted"] = {
                "rounds": steps,
                "status": status(forged),
                "receiver_actions": actions(forged),
            }

            exhausted = desk.approved("F")
            behaviour.set(Mode.FAIL)
            steps = []
            for delay in (0, 2, 4, 8):
                clock.advance(delay)
                steps.append(_attempts(worker().deliver_due()))
            seen = status(exhausted)
            behaviour.set(Mode.ACCEPT)
            [entry] = desk.workflows.outbox_events(exhausted)
            requeued = worker().requeue(entry.event.event_id)
            after = _attempts(worker().deliver_due())
            scenarios["F: attempts exhausted, visible, then requeued"] = {
                "rounds": steps,
                "status_seen_by_viewer": seen,
                "status_after_requeue": requeued.delivery.status.value,
                "after_requeue": after,
                "status": status(exhausted),
                "receiver_actions": actions(exhausted),
            }

            many = [desk.approved(f"G{index}") for index in range(6)]
            results: list[tuple[AttemptReport, ...]] = []
            threads = [
                threading.Thread(target=lambda: results.append(worker().deliver_due()))
                for _ in range(2)
            ]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
            scenarios["G: two workers at once"] = {
                "attempts_per_worker": [len(batch) for batch in results],
                "statuses": sorted({status(item) for item in many}),
                "receiver_actions": sum(actions(item) for item in many),
                "receiver_receipts": sum(receipts(item) for item in many),
            }
    return {
        "run": desk.run,
        "policy": describe_policy(DEMO_POLICY),
        "scenarios": scenarios,
    }


def describe_policy(policy: DeliveryPolicy) -> dict[str, object]:
    """The retry bounds used, for the printed report."""
    return {
        "max_attempts": policy.max_attempts,
        "base_delay_seconds": policy.base_delay_seconds,
        "max_delay_seconds": policy.max_delay_seconds,
        "timeout_seconds": policy.timeout_seconds,
    }


def main(argv: Sequence[str] | None = None) -> int:
    """Run the demo and print JSON, or a one-line error."""
    parser = argparse.ArgumentParser(
        prog="python -m app.outbox_demo",
        description="Fictional Stage 09 outbox delivery, loopback only.",
    )
    parser.parse_args(argv)
    settings = Settings()
    if settings.database_url is None:
        print("Set DATABASE_URL to run the outbox demo.", file=sys.stderr)
        return 2
    try:
        engine = create_database_engine(settings.database_url.get_secret_value())
    except ValueError as error:
        print(str(error), file=sys.stderr)
        return 2
    try:
        output = run_outbox_demo(engine)
    except (ApplicationError, DomainValidationError) as error:
        print(f"{error.code}: {error}", file=sys.stderr)
        return 1
    except OperationalError:
        print("Cannot connect to the database in DATABASE_URL.", file=sys.stderr)
        return 1
    except ProgrammingError:
        print("Schema is missing: run `alembic upgrade head` first.", file=sys.stderr)
        return 1
    finally:
        engine.dispose()
    print(json.dumps(output, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
