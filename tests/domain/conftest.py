"""Small fictional purchasing documents shared by rule and workflow tests."""

from decimal import Decimal

import pytest

from app.domain.models import Invoice, LineItem, PurchaseOrder
from app.domain.money import Currency, Money


@pytest.fixture
def purchase_order() -> PurchaseOrder:
    """Build a two-line fictional order whose independently known total is 30 USD."""
    return PurchaseOrder(
        purchase_order_id="PO-DEMO-001",
        supplier_id="SUPPLIER-DEMO-001",
        currency=Currency.USD,
        lines=(
            LineItem(
                "SKU-A",
                "Fictional item A",
                Decimal("2"),
                Money(Decimal("12.50"), Currency.USD),
            ),
            LineItem(
                "SKU-B",
                "Fictional item B",
                Decimal("1"),
                Money(Decimal("5.00"), Currency.USD),
            ),
        ),
    )


@pytest.fixture
def invoice(purchase_order: PurchaseOrder) -> Invoice:
    """Build a structurally valid invoice that agrees with the fictional order."""
    return Invoice(
        invoice_id="INV-DEMO-001",
        supplier_id=purchase_order.supplier_id,
        purchase_order_id=purchase_order.purchase_order_id,
        currency=Currency.USD,
        lines=purchase_order.lines,
        declared_total=Money(Decimal("30.00"), Currency.USD),
    )
