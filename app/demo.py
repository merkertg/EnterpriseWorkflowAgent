"""Run fictional in-memory Stage 02 scenarios using only the standard library."""

import json
from dataclasses import replace
from decimal import Decimal

from app.domain.models import Invoice, LineItem, PurchaseOrder
from app.domain.money import Currency, Money
from app.domain.workflow import (
    HumanDecision,
    Workflow,
    approve_workflow,
    check_workflow,
)


def build_demo_documents() -> tuple[Invoice, PurchaseOrder]:
    """Create fictional inputs whose independently stated line totals are 25 and 5."""
    lines = (
        LineItem(
            sku="DEMO-SEAL",
            description="Fictional seal kit",
            quantity=Decimal("2"),
            unit_price=Money(Decimal("12.50"), Currency.USD),
        ),
        LineItem(
            sku="DEMO-FILTER",
            description="Fictional filter",
            quantity=Decimal("1"),
            unit_price=Money(Decimal("5.00"), Currency.USD),
        ),
    )
    purchase_order = PurchaseOrder(
        purchase_order_id="DEMO-PO-001",
        supplier_id="DEMO-SUPPLIER-001",
        currency=Currency.USD,
        lines=lines,
    )
    invoice = Invoice(
        invoice_id="DEMO-INV-001",
        supplier_id="DEMO-SUPPLIER-001",
        purchase_order_id="DEMO-PO-001",
        currency=Currency.USD,
        lines=lines,
        declared_total=Money(Decimal("30.00"), Currency.USD),
    )
    return invoice, purchase_order


def describe_workflow(workflow: Workflow) -> dict[str, object]:
    """Serialize a small public demo summary with decimal amounts kept as strings."""
    result = workflow.last_check
    return {
        "workflow_id": workflow.workflow_id,
        "state": workflow.state.value,
        "invoice_id": workflow.invoice.invoice_id,
        "declared_total": str(workflow.invoice.declared_total.amount),
        "calculated_total": str(workflow.invoice.calculated_total.amount),
        "currency": workflow.invoice.currency.value,
        "policy_id": workflow.policy.policy_id,
        "issues": []
        if result is None
        else [
            {
                "code": issue.code.value,
                "field": issue.field,
                "expected": issue.expected,
                "actual": issue.actual,
            }
            for issue in result.issues
        ],
        "decision": None
        if workflow.decision is None
        else {
            "actor_id": workflow.decision.actor_id,
            "reason": workflow.decision.reason,
        },
    }


def run_demo() -> dict[str, object]:
    """Show explicit demo approval, a total mismatch and a missing purchase order."""
    invoice, purchase_order = build_demo_documents()
    received = Workflow("DEMO-WF-001", invoice, purchase_order)
    checked = check_workflow(received)
    approved = approve_workflow(
        checked,
        HumanDecision("demo-approver-1", "Explicit decision for a fictional example"),
    )

    mismatched_invoice = replace(
        invoice, declared_total=Money(Decimal("31.00"), Currency.USD)
    )
    mismatch = check_workflow(
        Workflow("DEMO-WF-002", mismatched_invoice, purchase_order)
    )
    missing_order = check_workflow(Workflow("DEMO-WF-003", invoice, None))
    return {
        "stage": "02",
        "mode": "fictional_in_memory_demo",
        "identity_verified": False,
        "happy_path": {
            "history": [
                received.state.value,
                checked.state.value,
                approved.state.value,
            ],
            "result": describe_workflow(approved),
        },
        "mismatch": describe_workflow(mismatch),
        "missing_order": describe_workflow(missing_order),
    }


def main() -> None:
    """Write deterministic JSON without files, network, environment settings or keys."""
    print(json.dumps(run_demo(), indent=2))


if __name__ == "__main__":
    main()
