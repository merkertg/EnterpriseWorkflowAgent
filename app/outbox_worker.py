"""Deliver outbox events to the sandbox receiver and inspect delivery state.

``python -m app.outbox_worker run`` delivers due events in a loop; ``--once``
delivers what is due now and exits. ``status`` prints counts per status and
the exhausted events. ``requeue EVENT_ID`` gives an exhausted event a new
round of attempts. The destination is ``SANDBOX_WEBHOOK_URL`` (loopback only)
and every request is signed with ``SANDBOX_WEBHOOK_SECRET``.
"""

import argparse
import json
import sys
import time
from collections.abc import Sequence
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import Engine
from sqlalchemy.exc import OperationalError, ProgrammingError

from app.application.delivery import AttemptReport, OutboxDeliveryService
from app.application.errors import ApplicationError
from app.config import Settings
from app.domain.errors import DomainValidationError
from app.infrastructure.db.session import build_delivery_service, create_database_engine
from app.infrastructure.webhook.sender import UrllibWebhookSender


def describe_attempt(report: AttemptReport) -> dict[str, object]:
    """One attempt as a JSON line."""
    return {
        "event_id": str(report.event_id),
        "workflow_id": report.workflow_id,
        "attempt": report.seq,
        "status": report.status.value,
        "http_status": report.http_status,
        "error": report.error_code,
        "duplicate": report.duplicate,
    }


def _service(settings: Settings, engine: Engine) -> OutboxDeliveryService | None:
    """Build the delivery service, or report the missing secret."""
    if settings.sandbox_webhook_secret is None:
        print("Set SANDBOX_WEBHOOK_SECRET (32+ characters).", file=sys.stderr)
        return None
    return build_delivery_service(
        engine,
        UrllibWebhookSender(settings.sandbox_webhook_url),
        settings.sandbox_webhook_secret.get_secret_value(),
    )


def _run(service: OutboxDeliveryService, once: bool, interval: float) -> int:
    """Deliver due events; print each attempt; Ctrl+C stops the loop."""
    try:
        while True:
            for report in service.deliver_due():
                print(json.dumps(describe_attempt(report)), flush=True)
            if once:
                return 0
            time.sleep(interval)
    except KeyboardInterrupt:
        return 0


def main(argv: Sequence[str] | None = None) -> int:
    """Parse a command, run it and print JSON or a one-line error."""
    parser = argparse.ArgumentParser(
        prog="python -m app.outbox_worker",
        description="Deliver outbox events to the local sandbox receiver.",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run", help="deliver due events")
    run.add_argument("--once", action="store_true", help="deliver once and exit")
    run.add_argument("--interval", type=float, default=2.0, help="seconds to wait")
    status = commands.add_parser("status", help="counts and exhausted events")
    status.add_argument("--limit", type=int, default=20)
    requeue = commands.add_parser("requeue", help="retry an exhausted event")
    requeue.add_argument("event_id", type=UUID)
    arguments = parser.parse_args(argv)

    try:
        settings = Settings()
    except ValidationError as error:
        fields = ", ".join(str(item["loc"][0]) for item in error.errors())
        print(f"Invalid settings: {fields}.", file=sys.stderr)
        return 2
    if settings.database_url is None:
        print("Set DATABASE_URL to deliver outbox events.", file=sys.stderr)
        return 2
    try:
        engine = create_database_engine(settings.database_url.get_secret_value())
    except ValueError as error:
        print(str(error), file=sys.stderr)
        return 2
    try:
        service = _service(settings, engine)
        if service is None:
            return 2
        if arguments.command == "run":
            return _run(service, arguments.once, max(arguments.interval, 0.1))
        if arguments.command == "status":
            output: object = service.summary(arguments.limit)
        else:
            entry = service.requeue(arguments.event_id)
            output = {
                "event_id": str(entry.event.event_id),
                "status": entry.delivery.status.value,
                "requeues": entry.delivery.requeues,
            }
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
    print(json.dumps(output, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
