"""Run the fictional Stage 06 decision scenario against PostgreSQL.

``python -m app.decisions_demo`` creates employees and tokens, signs in with
those tokens and walks through every refusal and both decisions.
``python -m app.decisions_demo show WORKFLOW_ID`` reads a workflow, its audit
trail and outbox events back in a new process. Tokens are never printed.
"""

import argparse
import json
import sys
from collections.abc import Callable, Sequence
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import Engine
from sqlalchemy.exc import OperationalError, ProgrammingError

from app.application.access import Principal
from app.application.errors import ApplicationError, StaleReconciliationError
from app.application.ports import CommandContext, CommandResult
from app.application.workflows import WorkflowService
from app.config import Settings
from app.domain.errors import DomainValidationError
from app.domain.models import LineItem, PurchaseOrder
from app.domain.money import Currency, Money
from app.domain.reconciliation import ReconciliationRules
from app.domain.rules import ValidationPolicy
from app.infrastructure.db.session import (
    build_document_service,
    build_reconciliation_service,
    build_user_service,
    build_workflow_service,
    create_database_engine,
)
from app.reconciliation_cli import currency_rule
from app.samples import invoice_text

_SOURCE = "decisions-demo"
_UNIT = "DEMO-UNIT-A"
_OTHER_UNIT = "DEMO-UNIT-B"
_SEAL = ("DEMO-SEAL", "Fictional seal kit")
_FILTER = ("DEMO-FILTER", "Fictional filter")


def _outcome(step: str, actor: str, action: Callable[[], object]) -> dict[str, object]:
    """Run one action and record its result or its stable error code."""
    entry: dict[str, object] = {"step": step, "actor": actor}
    try:
        result = action()
    except (ApplicationError, DomainValidationError) as error:
        entry["error"] = error.code
        if isinstance(error, StaleReconciliationError):
            entry["reasons"] = list(error.reasons)
        return entry
    if isinstance(result, CommandResult):
        entry.update(
            state=result.state.value,
            version=result.version,
            replayed=result.replayed,
        )
    return entry


def describe_decisions(
    workflows: WorkflowService, workflow_id: str
) -> dict[str, object]:
    """Read state, decision, audit trail and outbox events of a workflow."""
    stored = workflows.get_workflow(workflow_id)
    decision = stored.workflow.decision
    return {
        "workflow_id": workflow_id,
        "business_unit": stored.business_unit,
        "created_by": stored.created_by,
        "state": stored.workflow.state.value,
        "version": stored.version,
        "decision": None
        if decision is None
        else {
            "decided_by": decision.actor_id,
            "reason": decision.reason,
            "reconciliation_snapshot_id": None
            if stored.decision_reconciliation_id is None
            else str(stored.decision_reconciliation_id),
        },
        "audit_trail": [
            {
                "command": event.command.value,
                "new_state": event.new_state.value,
                "workflow_version": event.workflow_version,
                "actor_id": event.actor_id,
                "reconciliation_snapshot_id": None
                if event.reconciliation_snapshot_id is None
                else str(event.reconciliation_snapshot_id),
                "outbox_event_id": None
                if event.outbox_event_id is None
                else str(event.outbox_event_id),
            }
            for event in workflows.audit_trail(workflow_id)
        ],
        "outbox": [
            {
                "event_id": str(event.event_id),
                "event_type": event.event_type.value,
                "workflow_version": event.workflow_version,
                "payload": event.payload,
            }
            for event in workflows.outbox_events(workflow_id)
        ],
    }


def run_decisions_demo(engine: Engine) -> dict[str, object]:
    """Refusals by role, unit, four eyes, staleness and state; then decisions."""
    users = build_user_service(engine)
    documents = build_document_service(engine)
    workflows = build_workflow_service(engine)
    reconciliations = build_reconciliation_service(engine)
    run = uuid4().hex[:8].upper()

    def employee(name: str, roles: tuple[str, ...], unit: str) -> tuple[str, str]:
        """Create an employee and a token; return the user ID and secret."""
        user_id = f"demo-{name}-{run.lower()}"
        users.create_user(
            user_id, display_name=f"Fictional {name}", roles=roles, units=(unit,)
        )
        return user_id, users.issue_token(user_id, label="decisions-demo").secret

    tokens = {
        "lead": employee("lead", ("clerk", "approver"), _UNIT),
        "approver": employee("approver", ("approver",), _UNIT),
        "viewer": employee("viewer", ("viewer",), _UNIT),
        "outsider": employee("outsider", ("approver",), _OTHER_UNIT),
    }
    # Every later call uses the rights resolved from a token, as the API does.
    who: dict[str, Principal] = {
        name: users.authenticate(secret) for name, (_, secret) in tokens.items()
    }

    order_id = f"DEMO-PO-D{run}"
    order = workflows.record_purchase_order(
        PurchaseOrder(
            order_id,
            "DEMO-SUPPLIER-001",
            Currency.USD,
            (
                LineItem(*_SEAL, Decimal("2"), Money(Decimal("12.50"), Currency.USD)),
                LineItem(*_FILTER, Decimal("1"), Money(Decimal("5.00"), Currency.USD)),
            ),
        )
    )
    policy = workflows.record_policy(ValidationPolicy("demo-decisions-policy"))
    eur = currency_rule(Currency.EUR, "0.05", "0.10")
    rules_id = f"demo-decision-rules-{run}"
    rules_v1 = reconciliations.record_rules(
        ReconciliationRules(
            rules_id, (currency_rule(Currency.USD, "0.05", "0.10"), eur)
        )
    )

    def context(key: str) -> CommandContext:
        """One idempotency key per user intent."""
        return CommandContext(f"{run}:{key}", _SOURCE)

    def submit(name: str, lines: list[tuple[str, str, str, str]], total: str) -> str:
        """The lead uploads an invoice, starts a workflow and runs the check."""
        uploaded = documents.ingest(
            invoice_text(f"DEMO-INV-D{run}-{name}", order_id, lines, total).encode(),
            f"{name}.txt",
            actor=who["lead"],
        )
        version = uploaded.extraction.invoice_version
        if version is None:
            raise RuntimeError("Demo invoices must extract completely.")
        workflow_id = f"DEMO-WF-D{run}-{name}"
        workflows.start(
            workflow_id,
            invoice_version_id=version.version_id,
            purchase_order_version_id=order.version_id,
            policy_version_id=policy.version_id,
            business_unit=_UNIT,
            context=context(f"{name}:start"),
            actor=who["lead"],
        )
        workflows.check(
            workflow_id,
            expected_version=1,
            context=context(f"{name}:check"),
            actor=who["lead"],
        )
        return workflow_id

    clean = submit("A", [(*_SEAL, "2", "12.50"), (*_FILTER, "1", "5.00")], "30.00")
    first = reconciliations.reconcile(
        clean, rules_version_id=rules_v1.version_id, actor=who["lead"]
    )

    def approve(actor: str, snapshot_id: UUID, version: int, key: str) -> object:
        """Ask the service to approve the clean workflow as one employee."""
        return workflows.approve(
            clean,
            expected_version=version,
            reconciliation_snapshot_id=snapshot_id,
            reason="Fictional invoice matches the order",
            actor=who[actor],
            context=context(key),
        )

    steps = [
        _outcome(
            "viewer approves",
            "viewer",
            lambda: approve("viewer", first.snapshot_id, 2, "A:viewer"),
        ),
        _outcome(
            "approver from another unit approves",
            "outsider",
            lambda: approve("outsider", first.snapshot_id, 2, "A:outsider"),
        ),
        _outcome(
            "starter approves own workflow",
            "lead",
            lambda: approve("lead", first.snapshot_id, 2, "A:lead"),
        ),
    ]
    # The tolerance changes after the reconciliation: the snapshot is stale.
    rules_v2 = reconciliations.record_rules(
        ReconciliationRules(
            rules_id, (currency_rule(Currency.USD, "0.05", "0.20"), eur)
        )
    )
    steps.append(
        _outcome(
            "approve on stale reconciliation",
            "approver",
            lambda: approve("approver", first.snapshot_id, 2, "A:stale"),
        )
    )
    fresh = reconciliations.reconcile(
        clean, rules_version_id=rules_v2.version_id, actor=who["approver"]
    )
    steps += [
        _outcome(
            "approve with stale workflow version",
            "approver",
            lambda: approve("approver", fresh.snapshot_id, 1, "A:old-version"),
        ),
        _outcome(
            "approve on fresh matched reconciliation",
            "approver",
            lambda: approve("approver", fresh.snapshot_id, 2, "A:approve"),
        ),
        _outcome(
            "same request repeated",
            "approver",
            lambda: approve("approver", fresh.snapshot_id, 2, "A:approve"),
        ),
        _outcome(
            "reject after approval",
            "approver",
            lambda: workflows.reject(
                clean,
                expected_version=3,
                reason="Too late",
                actor=who["approver"],
                context=context("A:reject"),
            ),
        ),
    ]

    variance = submit("B", [(*_SEAL, "2", "12.54"), (*_FILTER, "1", "5.10")], "30.18")
    mismatch = reconciliations.reconcile(
        variance, rules_version_id=rules_v2.version_id, actor=who["lead"]
    )
    variance_version = workflows.get_workflow(variance).version
    steps += [
        _outcome(
            "approve with discrepancies",
            "approver",
            lambda: workflows.approve(
                variance,
                expected_version=variance_version,
                reconciliation_snapshot_id=mismatch.snapshot_id,
                reason="Fictional approval attempt",
                actor=who["approver"],
                context=context("B:approve"),
            ),
        ),
        _outcome(
            "reject with discrepancies",
            "approver",
            lambda: workflows.reject(
                variance,
                expected_version=variance_version,
                reason="Fictional prices exceed the tolerance",
                actor=who["approver"],
                context=context("B:reject"),
            ),
        ),
    ]

    # Account changes take effect on the next request.
    viewer_token = users.issue_token(tokens["viewer"][0], label="revoked-demo")
    users.revoke_token(viewer_token.token_id)
    users.set_active(tokens["outsider"][0], active=False)
    steps += [
        _outcome(
            "revoked token signs in",
            "viewer",
            lambda: users.authenticate(viewer_token.secret),
        ),
        _outcome(
            "deactivated user signs in",
            "outsider",
            lambda: users.authenticate(tokens["outsider"][1]),
        ),
    ]
    return {
        "stage": "06",
        "mode": "fictional_decisions_demo",
        "run": run,
        "employees": {name: user_id for name, (user_id, _) in tokens.items()},
        "reconciliations": {
            "first": {
                "snapshot_id": str(first.snapshot_id),
                "status": first.result.status.value,
            },
            "after_rules_change": {
                "snapshot_id": str(fresh.snapshot_id),
                "status": fresh.result.status.value,
            },
            "variance": {
                "snapshot_id": str(mismatch.snapshot_id),
                "status": mismatch.result.status.value,
                "codes": [item.code.value for item in mismatch.result.discrepancies],
            },
        },
        "steps": steps,
        "approved": describe_decisions(workflows, clean),
        "rejected": describe_decisions(workflows, variance),
    }


def _engine() -> Engine | None:
    """Create an engine from DATABASE_URL, or report why it cannot."""
    settings = Settings()
    if settings.database_url is None:
        print("Set DATABASE_URL to run the decisions demo.", file=sys.stderr)
        return None
    try:
        return create_database_engine(settings.database_url.get_secret_value())
    except ValueError as error:
        print(str(error), file=sys.stderr)
        return None


def main(argv: Sequence[str] | None = None) -> int:
    """Parse a command, run it and print JSON or a one-line error."""
    parser = argparse.ArgumentParser(
        prog="python -m app.decisions_demo",
        description="Fictional Stage 06 employee decisions.",
    )
    commands = parser.add_subparsers(dest="command")
    commands.add_parser("run", help="run the fictional scenario (default)")
    show = commands.add_parser("show", help="read a workflow's decisions")
    show.add_argument("workflow_id")
    arguments = parser.parse_args(argv)

    engine = _engine()
    if engine is None:
        return 2
    try:
        if arguments.command == "show":
            output = describe_decisions(
                build_workflow_service(engine), arguments.workflow_id
            )
        else:
            output = run_decisions_demo(engine)
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
