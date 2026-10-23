"""Reconciliation compares lines under explicit tolerances; expected values are
stated by hand, not computed with the code under test."""

from dataclasses import replace
from decimal import Decimal

import pytest

from app.domain.content_hash import content_hash, extraction_hash
from app.domain.errors import DomainValidationError
from app.domain.extraction import (
    Evidence,
    ExtractedField,
    ExtractionResult,
    FieldStatus,
)
from app.domain.models import Invoice, LineItem, PurchaseOrder
from app.domain.money import Currency, Money
from app.domain.reconciliation import (
    CurrencyRule,
    DiscrepancyCode,
    ReconciliationRules,
    ReconciliationStatus,
    reconcile,
)

USD = Currency.USD


def usd(amount: str) -> Money:
    """Build an exact USD amount."""
    return Money(Decimal(amount), USD)


def rule(unit: str = "0.05", total: str = "0.10") -> CurrencyRule:
    """A USD tolerance rule."""
    return CurrencyRule(USD, usd(unit), usd(total))


def rules(**changes: object) -> ReconciliationRules:
    """USD rules without a source-document requirement unless changed."""
    base = ReconciliationRules("test-rules", (rule(),), require_source_document=False)
    return replace(base, **changes)  # type: ignore[arg-type]


ORDER = PurchaseOrder(
    "PO-1",
    "SUP-1",
    USD,
    (
        LineItem("SEAL", "Seal kit", Decimal("2"), usd("12.50")),
        LineItem("FILTER", "Filter", Decimal("1"), usd("5.00")),
    ),
)


def invoice(
    seal: str = "12.50",
    filter_price: str = "5.00",
    filter_quantity: str = "1",
    total: str | None = None,
    **changes: object,
) -> Invoice:
    """An invoice for ORDER; total defaults to the hand-computed line sum."""
    lines = (
        LineItem("SEAL", "Seal kit", Decimal("2"), usd(seal)),
        LineItem("FILTER", "Filter", Decimal(filter_quantity), usd(filter_price)),
    )
    computed = Decimal(seal) * 2 + Decimal(filter_price) * Decimal(filter_quantity)
    declared = usd(total or f"{computed:.2f}")
    base = Invoice("INV-1", "SUP-1", "PO-1", USD, lines, declared)
    return replace(base, **changes)  # type: ignore[arg-type]


def codes(result: object) -> list[tuple[str, str]]:
    """List (code, field) pairs of a result."""
    return [(d.code.value, d.field) for d in result.discrepancies]  # type: ignore[attr-defined]


def test_exact_invoice_matches_with_hand_computed_totals() -> None:
    """2 × 12.50 + 1 × 5.00 = 30.00 on both sides."""
    result = reconcile(invoice(), ORDER, rules(), has_source_document=False)
    assert result.status is ReconciliationStatus.MATCHED
    assert result.discrepancies == ()
    assert result.calculated_total == usd("30.00")
    assert result.expected_total == usd("30.00")
    assert result.order_total == usd("30.00")
    assert [line.order_line for line in result.lines] == [1, 2]


def test_unit_price_tolerance_is_inclusive() -> None:
    """A 0.05 difference passes a 0.05 tolerance; 0.06 does not."""
    at_limit = reconcile(
        invoice(filter_price="5.05"), ORDER, rules(), has_source_document=False
    )
    assert at_limit.status is ReconciliationStatus.MATCHED
    over = reconcile(
        invoice(filter_price="5.06"), ORDER, rules(), has_source_document=False
    )
    [item] = over.discrepancies
    assert (item.code, item.field) == (
        DiscrepancyCode.UNIT_PRICE_MISMATCH,
        "lines[2].unit_price",
    )
    assert (item.expected, item.actual) == ("5.00 USD ± 0.05", "5.06 USD")
    assert item.rule == "USD.unit_price_tolerance=0.05"


def test_declared_total_tolerance_is_inclusive() -> None:
    """Lines sum to 30.00: declaring 30.10 passes 0.10, declaring 30.11 does not."""
    assert (
        reconcile(
            invoice(total="30.10"), ORDER, rules(), has_source_document=False
        ).discrepancies
        == ()
    )
    over = reconcile(invoice(total="30.11"), ORDER, rules(), has_source_document=False)
    assert codes(over) == [("declared_total_mismatch", "declared_total")]
    assert over.discrepancies[0].expected == "30.00 USD ± 0.10"


def test_small_price_differences_add_up_to_a_total_mismatch() -> None:
    """Each unit 0.04 high (inside 0.05), but 2 × 0.04 + 0.04 = 0.12 > 0.10."""
    sources = {
        "lines[1].unit_price": (Evidence(1, 10, 15),),
        "lines[2].unit_price": (Evidence(1, 30, 34),),
    }
    result = reconcile(
        invoice(seal="12.54", filter_price="5.04"),
        ORDER,
        rules(),
        has_source_document=False,
        sources=sources,
    )
    [item] = result.discrepancies
    assert (item.code, item.field) == (
        DiscrepancyCode.EXPECTED_TOTAL_MISMATCH,
        "matched_lines_total",
    )
    assert (item.expected, item.actual) == ("30.00 USD ± 0.10", "30.12 USD")
    assert item.sources == (Evidence(1, 10, 15), Evidence(1, 30, 34))


def test_line_rounding_is_half_up_once_per_line() -> None:
    """0.01 × 0.5 = 0.005 → 0.01 on each side, so the lines still match."""
    order = PurchaseOrder(
        "PO-1", "SUP-1", USD, (LineItem("BOLT", "Bolt", Decimal("1"), usd("0.01")),)
    )
    half = (LineItem("BOLT", "Bolt", Decimal("0.5"), usd("0.01")),)
    bill = Invoice("INV-1", "SUP-1", "PO-1", USD, half, usd("0.01"))
    result = reconcile(bill, order, rules(), has_source_document=False)
    assert result.status is ReconciliationStatus.MATCHED
    assert result.lines[0].expected_line_total == usd("0.01")


def test_quantity_above_order_and_partial_rules() -> None:
    """More than ordered always fails; fewer fails only without partial billing."""
    over = reconcile(
        invoice(filter_quantity="2"), ORDER, rules(), has_source_document=False
    )
    assert ("quantity_exceeds_order", "lines[2].quantity") in codes(over)
    assert over.discrepancies[0].expected == "<= 1"
    half_order = replace(
        ORDER, lines=(ORDER.lines[0], replace(ORDER.lines[1], quantity=Decimal("3")))
    )
    partial = reconcile(invoice(), half_order, rules(), has_source_document=False)
    assert partial.status is ReconciliationStatus.MATCHED
    strict = reconcile(
        invoice(),
        half_order,
        rules(allow_partial_quantities=False),
        has_source_document=False,
    )
    assert codes(strict) == [("quantity_below_order", "lines[2].quantity")]


def test_unknown_lines_are_reported_alone() -> None:
    """An extra SKU is unknown_line; it does not also break the matched total."""
    extra = LineItem("GASKET", "Gasket", Decimal("1"), usd("4.00"))
    bill = invoice(total="34.00")
    bill = replace(bill, lines=(*bill.lines, extra))
    result = reconcile(bill, ORDER, rules(), has_source_document=False)
    assert codes(result) == [("unknown_line", "lines[3].sku")]
    assert result.calculated_total == usd("34.00")
    assert result.expected_total == usd("30.00")


def test_header_mismatches_and_currency_stop() -> None:
    """Supplier and reference are compared; another currency stops amount checks."""
    result = reconcile(
        invoice(supplier_id="SUP-2", purchase_order_id="PO-9"),
        ORDER,
        rules(),
        has_source_document=False,
    )
    assert codes(result) == [
        ("purchase_order_reference_mismatch", "purchase_order_id"),
        ("supplier_mismatch", "supplier_id"),
    ]
    eur_order = replace(
        ORDER,
        currency=Currency.EUR,
        lines=tuple(
            replace(line, unit_price=Money(line.unit_price.amount, Currency.EUR))
            for line in ORDER.lines
        ),
    )
    crossed = reconcile(invoice(), eur_order, rules(), has_source_document=False)
    assert codes(crossed) == [("currency_mismatch", "currency")]
    assert crossed.expected_total is None
    assert {line.order_line for line in crossed.lines} == {None}


def test_missing_rule_order_document_and_duplicates() -> None:
    """Blocking reasons appear together and in a stable order."""
    eur_only = ReconciliationRules(
        "eur",
        (
            CurrencyRule(
                Currency.EUR,
                Money(Decimal("0.05"), Currency.EUR),
                Money(Decimal("0.10"), Currency.EUR),
            ),
        ),
    )
    result = reconcile(
        invoice(filter_price="9.00"),
        None,
        eur_only,
        has_source_document=False,
        duplicate_workflow_ids=("WF-B", "WF-A"),
    )
    assert codes(result) == [
        ("missing_source_document", "source_document"),
        ("duplicate_invoice", "invoice_id"),
        ("no_rule_for_currency", "currency"),
        ("missing_purchase_order", "purchase_order_id"),
    ]
    assert result.discrepancies[1].actual == "WF-A, WF-B"
    assert result.order_total is None
    no_rule = reconcile(
        invoice(filter_price="9.00"), ORDER, eur_only, has_source_document=True
    )
    # Without a USD rule the price cannot be judged, so only the rule is reported.
    assert codes(no_rule) == [("no_rule_for_currency", "currency")]


def test_result_is_deterministic() -> None:
    """Equal inputs always give an equal result."""
    first = reconcile(
        invoice(filter_price="5.10"), ORDER, rules(), has_source_document=False
    )
    assert first == reconcile(
        invoice(filter_price="5.10"), ORDER, rules(), has_source_document=False
    )


def test_rules_are_validated_and_normalized() -> None:
    """One rule per currency, supported rounding only, order does not matter."""
    eur = CurrencyRule(
        Currency.EUR,
        Money(Decimal("0.05"), Currency.EUR),
        Money(Decimal("0.10"), Currency.EUR),
    )
    forward = ReconciliationRules("r", (rule(), eur))
    backward = ReconciliationRules("r", (eur, rule()))
    assert forward == backward
    assert content_hash(forward) == content_hash(backward)
    assert content_hash(forward) != content_hash(
        ReconciliationRules("r", (rule("0.06"), eur))
    )
    assert forward.rule_for(USD) == rule() and forward.rule_for(Currency.UZS) is None
    with pytest.raises(DomainValidationError):
        ReconciliationRules("r", (rule(), rule()))
    with pytest.raises(
        DomainValidationError, check=lambda e: e.code == "unsupported_rounding"
    ):
        CurrencyRule(USD, usd("0.05"), usd("0.10"), rounding="ROUND_HALF_EVEN")
    with pytest.raises(
        DomainValidationError, check=lambda e: e.code == "unsupported_rounding"
    ):
        CurrencyRule(USD, usd("0.05"), usd("0.10"), decimal_places=3)
    with pytest.raises(DomainValidationError):
        CurrencyRule(USD, Money(Decimal("0.05"), Currency.EUR), usd("0.10"))
    with pytest.raises(DomainValidationError):
        ReconciliationRules("r", (rule(),), allow_partial_quantities=1)  # type: ignore[arg-type]


def test_money_distance_and_extraction_hash() -> None:
    """Distance is absolute and same-currency; field hashes follow evidence."""
    assert usd("5.00").distance(usd("5.10")) == Decimal("0.10")
    with pytest.raises(DomainValidationError):
        usd("1.00").distance(Money(Decimal("1.00"), Currency.EUR))
    field = ExtractedField("currency", FieldStatus.FOUND, "USD", (Evidence(1, 0, 3),))
    moved = replace(field, evidence=(Evidence(1, 4, 7),))
    assert extraction_hash(ExtractionResult("x/1", (field,))) != extraction_hash(
        ExtractionResult("x/1", (moved,))
    )
