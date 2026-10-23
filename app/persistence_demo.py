"""Run a fictional Stage 03 scenario against PostgreSQL and print JSON.

``python -m app.persistence_demo`` creates a new run with unique fictional IDs.
``python -m app.persistence_demo show WORKFLOW_ID`` reads it back in a new
process. Both need DATABASE_URL and an upgraded schema.
"""

import argparse
import json
import sys
from collections.abc import Sequence
from dataclasses import replace
from decimal import Decimal
from uuid import uuid4

from sqlalchemy.exc import OperationalError, ProgrammingError

from app.application.errors import ApplicationError, VersionConflictError
from app.application.ports import (
    AuditEvent,
    CommandContext,
    CommandResult,
    StoredWorkflow,
    ValidationSnapshot,
    VersionRef,
)
from app.application.workflows import WorkflowService
from app.config import Settings
from app.demo import build_demo_documents
from app.domain.errors import DomainValidationError
from app.domain.money import Currency
from app.domain.rules import ValidationPolicy
from app.domain.workflow import HumanDecision
from app.infrastructure.db.session import build_workflow_service, create_database_engine

_SOURCE = "persistence-demo"


def _ref(ref: VersionRef | None) -> dict[str, object] | None:
    """Show a version reference with its full content hash."""
    if ref is None:
        return None
    return {
        "business_id": ref.business_id,
        "version_no": ref.version_no,
        "version_id": str(ref.version_id),
        "content_hash": ref.content_hash,
    }


def _step(result: CommandResult) -> dict[str, object]:
    """Summarize one command result, including whether it was a replay."""
    return {
        "command": result.command.value,
        "state": result.state.value,
        "version": result.version,
        "replayed": result.replayed,
        "snapshot_id": None if result.snapshot_id is None else str(result.snapshot_id),
    }


def _snapshot(snapshot: ValidationSnapshot) -> dict[str, object]:
    """Show which exact input versions a stored check used."""
    order = snapshot.result.purchase_order
    return {
        "snapshot_id": str(snapshot.snapshot_id),
        "workflow_version": snapshot.workflow_version,
        "invoice": _ref(snapshot.invoice_version),
        "purchase_order": _ref(snapshot.purchase_order_version),
        "policy": _ref(snapshot.policy_version),
        "purchase_order_total": None if order is None else str(order.total.amount),
        "issue_codes": [issue.code.value for issue in snapshot.result.issues],
    }


def _audit(events: tuple[AuditEvent, ...]) -> list[dict[str, object]]:
    """List audit entries; they contain IDs and codes, not document text."""
    return [
        {
            "event_id": event.event_id,
            "command": event.command.value,
            "previous_state": None
            if event.previous_state is None
            else event.previous_state.value,
            "new_state": event.new_state.value,
            "workflow_version": event.workflow_version,
            "issue_codes": list(event.issue_codes),
            "actor_id": event.actor_id,
            "source": event.source,
            "occurred_at": None
            if event.occurred_at is None
            else event.occurred_at.isoformat(),
        }
        for event in events
    ]


def describe_stored(service: WorkflowService, workflow_id: str) -> dict[str, object]:
    """Read a workflow, its pinned versions and audit trail from the database."""
    stored: StoredWorkflow = service.get_workflow(workflow_id)
    return {
        "workflow_id": stored.workflow.workflow_id,
        "state": stored.workflow.state.value,
        "version": stored.version,
        "invoice": _ref(stored.invoice_version),
        "purchase_order": _ref(stored.purchase_order_version),
        "policy": _ref(stored.policy_version),
        "last_snapshot_id": None
        if stored.last_snapshot_id is None
        else str(stored.last_snapshot_id),
        "audit_trail": _audit(service.audit_trail(workflow_id)),
    }


def run_persistence_demo(service: WorkflowService, run_id: str) -> dict[str, object]:
    """Store, check, replay, conflict, change inputs, re-check and reject."""
    base_invoice, base_order = build_demo_documents()
    order_id = f"DEMO-PO-{run_id}"
    workflow_id = f"DEMO-WF-{run_id}"
    invoice = replace(
        base_invoice, invoice_id=f"DEMO-INV-{run_id}", purchase_order_id=order_id
    )
    order = replace(base_order, purchase_order_id=order_id)
    policy = ValidationPolicy(policy_id=f"demo-policy-{run_id}")

    def context(step: str) -> CommandContext:
        """Give each user intent its own key, derived from the run ID."""
        return CommandContext(f"{run_id}:{step}", _SOURCE)

    invoice_v1 = service.record_invoice(invoice)
    order_v1 = service.record_purchase_order(order)
    policy_v1 = service.record_policy(policy)
    steps = [
        _step(
            service.start(
                workflow_id,
                invoice_version_id=invoice_v1.version_id,
                purchase_order_version_id=order_v1.version_id,
                policy_version_id=policy_v1.version_id,
                context=context("start"),
            )
        )
    ]
    first_check = service.check(
        workflow_id, expected_version=1, context=context("check-1")
    )
    steps.append(_step(first_check))
    # A network retry repeats the same key: the stored outcome is returned.
    steps.append(
        _step(
            service.check(workflow_id, expected_version=1, context=context("check-1"))
        )
    )
    decision = HumanDecision("demo-reviewer-1", "Fictional demo decision")
    try:
        service.approve(
            workflow_id,
            expected_version=1,
            decision=decision,
            context=context("stale-approve"),
        )
    except VersionConflictError as error:
        steps.append({"command": "approve", "expected_version": 1, "error": error.code})

    # The order shrinks to one seal kit and the policy drops UZS: new versions.
    changed_order = replace(
        order, lines=(replace(order.lines[0], quantity=Decimal("1")), order.lines[1])
    )
    order_v2 = service.record_purchase_order(changed_order)
    policy_v2 = service.record_policy(
        ValidationPolicy(policy.policy_id, (Currency.USD, Currency.EUR))
    )
    steps.append(
        _step(
            service.revise(
                workflow_id,
                expected_version=2,
                invoice_version_id=invoice_v1.version_id,
                purchase_order_version_id=order_v2.version_id,
                policy_version_id=policy_v2.version_id,
                context=context("revise"),
            )
        )
    )
    second_check = service.check(
        workflow_id, expected_version=3, context=context("check-2")
    )
    steps.append(_step(second_check))
    steps.append(
        _step(
            service.reject(
                workflow_id,
                expected_version=4,
                decision=HumanDecision(
                    "demo-reviewer-1", "Fictional order no longer covers the invoice"
                ),
                context=context("reject"),
            )
        )
    )
    if first_check.snapshot_id is None or second_check.snapshot_id is None:
        raise RuntimeError("Check commands must record snapshots.")
    return {
        "stage": "03",
        "mode": "fictional_postgresql_demo",
        "identity_verified": False,
        "run_id": run_id,
        "steps": steps,
        "snapshots": {
            "first_check": _snapshot(service.get_snapshot(first_check.snapshot_id)),
            "after_revision": _snapshot(service.get_snapshot(second_check.snapshot_id)),
        },
        "stored": describe_stored(service, workflow_id),
    }


def main(argv: Sequence[str] | None = None) -> int:
    """Parse the command, connect using DATABASE_URL and print JSON or an error."""
    parser = argparse.ArgumentParser(
        prog="python -m app.persistence_demo",
        description="Fictional Stage 03 PostgreSQL demonstration.",
    )
    commands = parser.add_subparsers(dest="command")
    commands.add_parser("run", help="create a new fictional run (default)")
    show = commands.add_parser("show", help="read a stored workflow")
    show.add_argument("workflow_id")
    arguments = parser.parse_args(argv)

    settings = Settings()
    if settings.database_url is None:
        print("Set DATABASE_URL to run the persistence demo.", file=sys.stderr)
        return 2
    try:
        engine = create_database_engine(settings.database_url.get_secret_value())
    except ValueError as error:
        print(str(error), file=sys.stderr)
        return 2
    try:
        service = build_workflow_service(engine)
        if arguments.command == "show":
            output = describe_stored(service, arguments.workflow_id)
        else:
            output = run_persistence_demo(service, uuid4().hex[:12])
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
