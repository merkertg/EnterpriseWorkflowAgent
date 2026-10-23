"""Check immutable document structure independently from the later business rules."""

from collections.abc import Callable
from dataclasses import FrozenInstanceError, replace
from decimal import ROUND_DOWN, Decimal, Inexact, Rounded, localcontext
from typing import cast

import pytest

from app.domain.errors import DomainValidationError
from app.domain.models import Invoice, LineItem, PurchaseOrder, validate_text
from app.domain.money import Currency, Money


def make_line(
    sku: str = "DEMO-SKU-01",
    *,
    currency: Currency = Currency.USD,
    quantity: str = "2",
    price: str = "12.50",
) -> LineItem:
    """Build a fictional product line while keeping test-specific changes readable."""
    return LineItem(
        sku,
        "Fictional office supplies",
        Decimal(quantity),
        Money(Decimal(price), currency),
    )


def make_invoice(lines: tuple[LineItem, ...]) -> Invoice:
    """Build a fictional invoice without hiding the declared total from assertions."""
    return Invoice(
        "DEMO-INV-01",
        "DEMO-SUPPLIER",
        "DEMO-PO-01",
        Currency.USD,
        lines,
        Money(Decimal("25.00"), Currency.USD),
    )


def test_document_ids_and_descriptions_are_trimmed() -> None:
    """Whitespace is normalized once, before rules compare supplier and product IDs."""
    line = LineItem(
        "  DEMO-SKU-01 ",
        " Fictional item\n",
        Decimal("2.0000000"),
        Money(Decimal("12.50"), Currency.USD),
    )
    invoice = Invoice(
        " DEMO-INV-01 ",
        " DEMO-SUPPLIER ",
        " DEMO-PO-01 ",
        Currency.USD,
        (line,),
        Money(Decimal("25.00"), Currency.USD),
    )
    order = PurchaseOrder(" DEMO-PO-01 ", " DEMO-SUPPLIER ", Currency.USD, (line,))
    assert line.sku == "DEMO-SKU-01"
    assert line.description == "Fictional item"
    assert line.quantity.as_tuple().exponent == -6
    assert invoice.invoice_id == "DEMO-INV-01"
    assert invoice.supplier_id == order.supplier_id == "DEMO-SUPPLIER"
    assert invoice.purchase_order_id == order.purchase_order_id == "DEMO-PO-01"
    assert (
        line.total.amount
        == invoice.calculated_total.amount
        == order.total.amount
        == Decimal("25.00")
    )


def test_invoice_retains_declared_total_mismatch_for_business_rules() -> None:
    """Structurally valid supplier data can still fail the separate rule evaluation."""
    invoice = replace(
        make_invoice((make_line(),)),
        declared_total=Money(Decimal("20.00"), Currency.USD),
    )
    assert invoice.declared_total.amount == Decimal("20.00")
    assert invoice.calculated_total.amount == Decimal("25.00")


def test_document_total_adds_rounded_lines_instead_of_rounding_whole_document() -> None:
    """Two half-cent line products each round to a cent under the demo's policy."""
    lines = (
        make_line("DEMO-A", quantity="0.5", price="0.01"),
        make_line("DEMO-B", quantity="0.5", price="0.01"),
    )
    assert make_invoice(lines).calculated_total.amount == Decimal("0.02")
    assert PurchaseOrder(
        "DEMO-PO-01", "DEMO-SUPPLIER", Currency.USD, lines
    ).total.amount == Decimal("0.02")


def test_line_and_document_totals_ignore_ambient_decimal_context() -> None:
    """An adapter's precision and traps cannot change invoice/order verification."""
    with localcontext() as context:
        context.prec = 2
        context.rounding = ROUND_DOWN
        context.traps[Inexact] = True
        context.traps[Rounded] = True
        line = make_line(price="123456789.23")
        invoice = make_invoice((line,))
        order = PurchaseOrder("DEMO-PO-01", "DEMO-SUPPLIER", Currency.USD, (line,))
        assert line.total.amount == Decimal("246913578.46")
        assert invoice.calculated_total == order.total == line.total
        assert not context.flags[Inexact]
        assert not context.flags[Rounded]


@pytest.mark.parametrize("value", ["", "  ", "\t\n", "x" * 129])
def test_required_ids_reject_empty_or_excessively_long_values(value: str) -> None:
    """The same bounded identifier contract applies to each document reference."""
    invoice = make_invoice((make_line(),))
    invalid_invoices: tuple[tuple[str, Callable[[], Invoice]], ...] = (
        ("invoice_id", lambda: replace(invoice, invoice_id=value)),
        ("supplier_id", lambda: replace(invoice, supplier_id=value)),
        ("purchase_order_id", lambda: replace(invoice, purchase_order_id=value)),
    )
    for field, create_invoice in invalid_invoices:
        with pytest.raises(DomainValidationError) as caught:
            create_invoice()
        assert caught.value.field == field
    with pytest.raises(DomainValidationError):
        make_line(value)
    order = PurchaseOrder("DEMO-PO-01", "DEMO-SUPPLIER", Currency.USD, invoice.lines)
    invalid_orders: tuple[Callable[[], PurchaseOrder], ...] = (
        lambda: replace(order, purchase_order_id=value),
        lambda: replace(order, supplier_id=value),
    )
    for create_order in invalid_orders:
        with pytest.raises(DomainValidationError):
            create_order()


def test_text_length_limits_are_inclusive_after_trimming() -> None:
    """The helper is shared by workflows so each boundary uses the same rule."""
    assert validate_text(" " + "x" * 128 + " ", "id") == "x" * 128
    assert validate_text("x" * 2000, "reason", max_length=2000) == "x" * 2000
    assert replace(make_line(), description="x" * 500).description == "x" * 500
    for value in ("", " ", "x" * 501):
        with pytest.raises(DomainValidationError) as caught:
            replace(make_line(), description=value)
        assert caught.value.field == "description"


@pytest.mark.parametrize("value", [1, True, None, Decimal("1")])
def test_required_text_rejects_nonstring_runtime_values(value: object) -> None:
    """The domain never accepts an adapter's implicit identifier conversion."""
    with pytest.raises(DomainValidationError):
        validate_text(cast(str, value), "invoice_id")
    with pytest.raises(DomainValidationError):
        replace(make_line(), description=cast(str, value))


@pytest.mark.parametrize("quantity", ["0", "-1", "NaN", "Infinity", "1.0000001"])
def test_line_rejects_invalid_quantities(quantity: str) -> None:
    """Invalid quantities fail while creating the immutable line snapshot."""
    with pytest.raises(DomainValidationError):
        make_line(quantity=quantity)


def test_line_rejects_unvalidated_price_and_overflowing_total() -> None:
    """A valid unit price does not make an excessively large line total valid."""
    with pytest.raises(DomainValidationError) as caught:
        replace(make_line(), unit_price=cast(Money, Decimal("12.50")))
    assert caught.value.field == "unit_price"
    with pytest.raises(DomainValidationError):
        make_line(price="999999999999.99")


@pytest.mark.parametrize("lines", [(), [make_line()], ("line",)])
def test_documents_require_nonempty_validated_immutable_lines(lines: object) -> None:
    """Lists cannot remain mutable inside an otherwise frozen document snapshot."""
    checked_lines = cast(tuple[LineItem, ...], lines)
    with pytest.raises(DomainValidationError):
        make_invoice(checked_lines)
    with pytest.raises(DomainValidationError):
        PurchaseOrder("DEMO-PO-01", "DEMO-SUPPLIER", Currency.USD, checked_lines)


def test_documents_limit_lines_and_reject_duplicate_normalized_skus() -> None:
    """Bounded unique line references make later comparisons unambiguous."""
    lines = tuple(make_line(f"DEMO-{index}") for index in range(100))
    assert len(make_invoice(lines).lines) == 100
    with pytest.raises(DomainValidationError) as caught:
        make_invoice((*lines, make_line("DEMO-101")))
    assert caught.value.code == "invalid_lines"
    duplicate_lines = (make_line("DEMO-A"), make_line(" DEMO-A "))
    with pytest.raises(DomainValidationError) as caught:
        make_invoice(duplicate_lines)
    assert caught.value.code == "duplicate_sku"
    with pytest.raises(DomainValidationError):
        PurchaseOrder("DEMO-PO-01", "DEMO-SUPPLIER", Currency.USD, duplicate_lines)


def test_documents_require_matching_explicit_currencies() -> None:
    """A mixed-currency document cannot reach monetary comparisons or exchange rates."""
    line = make_line()
    for currency in ("USD", "GBP", None):
        with pytest.raises(DomainValidationError):
            replace(make_invoice((line,)), currency=cast(Currency, currency))
        with pytest.raises(DomainValidationError):
            PurchaseOrder(
                "DEMO-PO-01", "DEMO-SUPPLIER", cast(Currency, currency), (line,)
            )
    with pytest.raises(DomainValidationError) as caught:
        make_invoice((make_line(currency=Currency.EUR),))
    assert caught.value.field == "lines"
    with pytest.raises(DomainValidationError) as caught:
        replace(
            make_invoice((line,)), declared_total=Money(Decimal("25"), Currency.EUR)
        )
    assert caught.value.field == "declared_total"
    with pytest.raises(DomainValidationError):
        replace(make_invoice((line,)), declared_total=cast(Money, Decimal("25")))


def test_documents_reject_aggregate_totals_above_the_money_limit() -> None:
    """A hundred individually valid lines must also form a representable total."""
    lines = (
        make_line("DEMO-A", quantity="1", price="999999999999.99"),
        make_line("DEMO-B", quantity="1", price="0.01"),
    )
    with pytest.raises(DomainValidationError):
        make_invoice(lines)
    with pytest.raises(DomainValidationError):
        PurchaseOrder("DEMO-PO-01", "DEMO-SUPPLIER", Currency.USD, lines)


def test_documents_and_nested_values_are_frozen_slotted_snapshots() -> None:
    """Workflow evidence can retain references without callers changing nested state."""
    line = make_line()
    invoice = make_invoice((line,))
    order = PurchaseOrder("DEMO-PO-01", "DEMO-SUPPLIER", Currency.USD, (line,))
    for value, field, replacement in (
        (line, "quantity", Decimal("9")),
        (invoice, "supplier_id", "OTHER"),
        (order, "lines", ()),
        (invoice.lines[0].unit_price, "amount", Decimal("0")),
    ):
        with pytest.raises(FrozenInstanceError):
            setattr(value, field, replacement)
        assert not hasattr(value, "__dict__")
    assert invoice.lines[0] is line
    assert invoice.calculated_total.amount == Decimal("25.00")
