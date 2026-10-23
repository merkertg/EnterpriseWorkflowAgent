"""Stage 05 command line: a reconciliation demo and snapshot reports.

``demo`` builds fictional documents with run-specific numbers, so repeated
runs never see each other as duplicates. ``show SNAPSHOT_ID`` prints a stored
snapshot with freshness, a reproduction check and quoted sources.
"""

import argparse
import json
import sys
from collections.abc import Sequence
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import Engine
from sqlalchemy.exc import OperationalError, ProgrammingError

from app.application.errors import ApplicationError
from app.application.ports import CommandContext
from app.application.reconciliation import ReconciliationReport, ReconciliationService
from app.config import Settings
from app.domain.errors import DomainValidationError
from app.domain.models import LineItem, PurchaseOrder
from app.domain.money import Currency, Money
from app.domain.reconciliation import CurrencyRule, ReconciliationRules
from app.domain.rules import ValidationPolicy
from app.domain.workflow import HumanDecision
from app.infrastructure.db.session import (
    build_document_service,
    build_reconciliation_service,
    build_workflow_service,
    create_database_engine,
)
from app.samples import invoice_text

_SOURCE = "reconciliation-demo"
_SEAL = ("DEMO-SEAL", "Fictional seal kit")
_FILTER = ("DEMO-FILTER", "Fictional filter")


def currency_rule(currency: Currency, unit: str, total: str) -> CurrencyRule:
    """Build a tolerance rule from decimal strings."""
    return CurrencyRule(
        currency, Money(Decimal(unit), currency), Money(Decimal(total), currency)
    )


def describe_report(report: ReconciliationReport) -> dict[str, object]:
    """Show status, totals, discrepancies with sources, and freshness."""
    snapshot, result = report.snapshot, report.snapshot.result
    inputs = snapshot.inputs

    def amount(value: Money | None) -> str | None:
        """Print an amount as a decimal string."""
        return None if value is None else str(value.amount)

    return {
        "snapshot_id": str(snapshot.snapshot_id),
        "workflow_id": snapshot.workflow_id,
        "status": result.status.value,
        "current": report.freshness.is_current,
        "stale_reasons": [reason.value for reason in report.freshness.reasons],
        "reproducible": report.reproducible,
        "inputs": {
            "invoice": f"{inputs.invoice_version.business_id} "
            f"v{inputs.invoice_version.version_no}",
            "purchase_order": None
            if inputs.purchase_order_version is None
            else f"{inputs.purchase_order_version.business_id} "
            f"v{inputs.purchase_order_version.version_no}",
            "rules": f"{inputs.rules_version.business_id} "
            f"v{inputs.rules_version.version_no}",
            "source_document_id": None
            if inputs.source_document_id is None
            else str(inputs.source_document_id),
            "duplicates": list(inputs.duplicate_workflow_ids),
        },
        "totals": {
            "currency": result.currency.value,
            "calculated": amount(result.calculated_total),
            "expected_from_order_prices": amount(result.expected_total),
            "order": amount(result.order_total),
        },
        "discrepancies": [
            {
                "code": item.code.value,
                "field": item.field,
                "expected": item.expected,
                "actual": item.actual,
                "rule": item.rule,
                "sources": [
                    {"page": c.page, "line_number": c.line_number, "quote": c.quote}
                    for c in report.citations.get(item.field, ())
                ],
            }
            for item in result.discrepancies
        ],
    }


def run_reconciliation_demo(engine: Engine) -> dict[str, object]:
    """Match, price and quantity problems, a duplicate, staleness and a fix."""
    documents = build_document_service(engine)
    workflows = build_workflow_service(engine)
    service: ReconciliationService = build_reconciliation_service(engine)
    run = uuid4().hex[:8].upper()
    order_id = f"DEMO-PO-R{run}"
    order = PurchaseOrder(
        order_id,
        "DEMO-SUPPLIER-001",
        Currency.USD,
        (
            LineItem(*_SEAL, Decimal("2"), Money(Decimal("12.50"), Currency.USD)),
            LineItem(*_FILTER, Decimal("1"), Money(Decimal("5.00"), Currency.USD)),
        ),
    )
    order_v1 = workflows.record_purchase_order(order)
    policy = workflows.record_policy(ValidationPolicy("demo-reconciliation-policy"))
    usd = currency_rule(Currency.USD, "0.05", "0.10")
    eur = currency_rule(Currency.EUR, "0.05", "0.10")
    rules_v1 = service.record_rules(
        ReconciliationRules(f"demo-rules-{run}", (usd, eur))
    )

    def context(key: str) -> CommandContext:
        """Each user intent gets its own idempotency key."""
        return CommandContext(f"{run}:{key}", _SOURCE)

    def start(
        name: str,
        lines: list[tuple[str, str, str, str]],
        total: str,
        *,
        invoice_name: str | None = None,
    ) -> str:
        """Ingest an invoice document, start a workflow and run the basic check."""
        number = f"DEMO-INV-R{run}-{invoice_name or name}"
        ingested = documents.ingest(
            invoice_text(number, order_id, lines, total).encode(), f"{name}.txt"
        )
        version = ingested.extraction.invoice_version
        if version is None:
            raise RuntimeError("Demo invoices must extract completely.")
        workflow_id = f"DEMO-WF-R{run}-{name}"
        workflows.start(
            workflow_id,
            invoice_version_id=version.version_id,
            purchase_order_version_id=order_v1.version_id,
            policy_version_id=policy.version_id,
            context=context(f"{name}:start"),
        )
        workflows.check(
            workflow_id, expected_version=1, context=context(f"{name}:check")
        )
        return workflow_id

    exact = [(*_SEAL, "2", "12.50"), (*_FILTER, "1", "5.00")]
    matched = start("A", exact, "30.00")
    snapshot_a = service.reconcile(matched, rules_version_id=rules_v1.version_id)
    price = start("B", [(*_SEAL, "2", "12.54"), (*_FILTER, "1", "5.10")], "30.18")
    extra = ("DEMO-GASKET", "Fictional gasket", "1", "4.00")
    quantity = start(
        "C", [(*_SEAL, "2", "12.50"), (*_FILTER, "3", "5.00"), extra], "44.00"
    )
    # The same invoice number and bytes submitted again in a second workflow.
    duplicate = start("D", exact, "30.00", invoice_name="A")
    snapshots = {
        "matched": snapshot_a,
        "price_variance": service.reconcile(
            price, rules_version_id=rules_v1.version_id
        ),
        "quantity_and_unknown_line": service.reconcile(
            quantity, rules_version_id=rules_v1.version_id
        ),
        "duplicate": service.reconcile(duplicate, rules_version_id=rules_v1.version_id),
    }

    initial = {
        name: describe_report(service.report(snapshot.snapshot_id))
        for name, snapshot in snapshots.items()
    }

    # Rules and order change after A was reconciled: its snapshot goes stale.
    rules_v2 = service.record_rules(
        ReconciliationRules(
            f"demo-rules-{run}", (currency_rule(Currency.USD, "0.05", "0.20"), eur)
        )
    )
    order_v2 = workflows.record_purchase_order(
        PurchaseOrder(
            order_id,
            order.supplier_id,
            order.currency,
            (
                LineItem(*_SEAL, Decimal("3"), Money(Decimal("12.50"), Currency.USD)),
                order.lines[1],
            ),
        )
    )
    stale_a = describe_report(service.report(snapshot_a.snapshot_id))

    # Resolve: reject the duplicate, pin A to order v2, re-check, re-reconcile.
    workflows.reject(
        duplicate,
        expected_version=2,
        decision=HumanDecision("demo-reviewer-1", "Duplicate submission"),
        context=context("D:reject"),
    )
    workflows.revise(
        matched,
        expected_version=2,
        invoice_version_id=snapshot_a.inputs.invoice_version.version_id,
        purchase_order_version_id=order_v2.version_id,
        policy_version_id=policy.version_id,
        context=context("A:revise"),
    )
    workflows.check(matched, expected_version=3, context=context("A:recheck"))
    refreshed = service.reconcile(matched, rules_version_id=rules_v2.version_id)
    eur_only = service.record_rules(ReconciliationRules(f"demo-eur-only-{run}", (eur,)))
    no_rule = service.reconcile(matched, rules_version_id=eur_only.version_id)
    return {
        "stage": "05",
        "mode": "fictional_reconciliation_demo",
        "run": run,
        "reconciliations": initial,
        "after_inputs_changed": {"snapshot_a": stale_a},
        "after_fix": describe_report(service.report(refreshed.snapshot_id)),
        "no_rule_for_currency": describe_report(service.report(no_rule.snapshot_id)),
    }


def _engine() -> Engine | None:
    """Create an engine from DATABASE_URL, or report why it cannot."""
    settings = Settings()
    if settings.database_url is None:
        print("Set DATABASE_URL to run reconciliation commands.", file=sys.stderr)
        return None
    try:
        return create_database_engine(settings.database_url.get_secret_value())
    except ValueError as error:
        print(str(error), file=sys.stderr)
        return None


def main(argv: Sequence[str] | None = None) -> int:
    """Parse a command, run it and print JSON or a one-line error."""
    parser = argparse.ArgumentParser(
        prog="python -m app.reconciliation_cli",
        description="Fictional Stage 05 reconciliation.",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("demo", help="run the fictional reconciliation scenario")
    show = commands.add_parser("show", help="report a stored snapshot")
    show.add_argument("snapshot_id", type=UUID)
    arguments = parser.parse_args(argv)

    engine = _engine()
    if engine is None:
        return 2
    try:
        if arguments.command == "show":
            service = build_reconciliation_service(engine)
            output = describe_report(service.report(arguments.snapshot_id))
        else:
            output = run_reconciliation_demo(engine)
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
