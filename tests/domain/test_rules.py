"""Check basic rule outcomes against explicit fictional business examples."""

from dataclasses import FrozenInstanceError, replace
from decimal import ROUND_DOWN, Decimal, localcontext
from typing import cast

import pytest

from app.domain.errors import DomainValidationError
from app.domain.models import Invoice, LineItem, PurchaseOrder
from app.domain.money import Currency, Money
from app.domain.rules import (
    IssueCode,
    ValidationPolicy,
    evaluate_invoice,
)


def test_matching_documents_have_no_issues(
    invoice: Invoice, purchase_order: PurchaseOrder
) -> None:
    """A matching total and headers allow a human decision without approving it."""
    result = evaluate_invoice(invoice, purchase_order)
    assert result.is_valid
    assert result.issues == ()
    assert result.calculated_total == Money(Decimal("30.00"), Currency.USD)
    assert result.invoice is invoice
    assert result.purchase_order is purchase_order
    assert result.policy.policy_id == "basic-demo-v1"


def test_missing_order_keeps_other_independent_issues(invoice: Invoice) -> None:
    """Absent order data never hides unsupported currency or a wrong declared total."""
    bad_invoice = replace(invoice, declared_total=Money(Decimal("29.00"), Currency.USD))
    policy = ValidationPolicy("eur-only-demo", (Currency.EUR,))
    result = evaluate_invoice(bad_invoice, None, policy)
    assert not result.is_valid
    assert [issue.code for issue in result.issues] == [
        IssueCode.UNSUPPORTED_CURRENCY,
        IssueCode.INVOICE_TOTAL_MISMATCH,
        IssueCode.MISSING_PURCHASE_ORDER,
    ]
    assert [(issue.expected, issue.actual) for issue in result.issues] == [
        ("EUR", "USD"),
        ("30.00 USD", "29.00 USD"),
        ("PO-DEMO-001", None),
    ]


def test_all_applicable_same_currency_issues_have_stable_order(
    invoice: Invoice, purchase_order: PurchaseOrder
) -> None:
    """Basic checks report all independent findings in the documented stable order."""
    bad_invoice = replace(
        invoice,
        supplier_id="SUPPLIER-DEMO-OTHER",
        purchase_order_id="PO-DEMO-OTHER",
        declared_total=Money(Decimal("30.01"), Currency.USD),
    )
    policy = ValidationPolicy("eur-only-demo", (Currency.EUR,))
    result = evaluate_invoice(bad_invoice, purchase_order, policy)
    assert [issue.code for issue in result.issues] == [
        IssueCode.UNSUPPORTED_CURRENCY,
        IssueCode.INVOICE_TOTAL_MISMATCH,
        IssueCode.PURCHASE_ORDER_REFERENCE_MISMATCH,
        IssueCode.SUPPLIER_MISMATCH,
        IssueCode.PURCHASE_ORDER_TOTAL_EXCEEDED,
    ]
    assert [(issue.field, issue.expected, issue.actual) for issue in result.issues] == [
        ("invoice.currency", "EUR", "USD"),
        ("invoice.declared_total", "30.00 USD", "30.01 USD"),
        ("invoice.purchase_order_id", "PO-DEMO-001", "PO-DEMO-OTHER"),
        ("invoice.supplier_id", "SUPPLIER-DEMO-001", "SUPPLIER-DEMO-OTHER"),
        ("invoice.declared_total", "30.00 USD", "30.01 USD"),
    ]
    assert all(issue.message for issue in result.issues)
    assert not result.is_valid


@pytest.mark.parametrize("amount", ["29.99", "30.00", "30.01"])
def test_order_total_boundary_uses_exact_amounts(
    invoice: Invoice, purchase_order: PurchaseOrder, amount: str
) -> None:
    """One cent over the order requires review, while equal or smaller totals do not."""
    total = Money(Decimal(amount), Currency.USD)
    changed = replace(
        invoice,
        lines=(LineItem("SKU-X", "Fictional item", Decimal("1"), total),),
        declared_total=total,
    )
    result = evaluate_invoice(changed, purchase_order)
    expected = [IssueCode.PURCHASE_ORDER_TOTAL_EXCEEDED] if amount == "30.01" else []
    assert [issue.code for issue in result.issues] == expected


def test_cross_currency_does_not_compare_unrelated_amounts(
    invoice: Invoice, purchase_order: PurchaseOrder
) -> None:
    """Different currencies require review without an invented exchange conversion."""
    euro_order = replace(
        purchase_order,
        currency=Currency.EUR,
        lines=(
            LineItem(
                "SKU-A",
                "Fictional item",
                Decimal("1"),
                Money(Decimal("0.01"), Currency.EUR),
            ),
        ),
    )
    result = evaluate_invoice(invoice, euro_order)
    assert [issue.code for issue in result.issues] == [IssueCode.CURRENCY_MISMATCH]
    assert result.issues[0].expected == "EUR"
    assert result.issues[0].actual == "USD"


def test_rounded_line_extensions_are_summed_before_total_check(
    invoice: Invoice, purchase_order: PurchaseOrder
) -> None:
    """Two half-cent extensions become two cents, not a once-rounded single cent."""
    rounded_invoice = replace(
        invoice,
        lines=(
            LineItem(
                "SKU-A",
                "Fictional fraction A",
                Decimal("0.5"),
                Money(Decimal("0.01"), Currency.USD),
            ),
            LineItem(
                "SKU-B",
                "Fictional fraction B",
                Decimal("0.5"),
                Money(Decimal("0.01"), Currency.USD),
            ),
        ),
        declared_total=Money(Decimal("0.02"), Currency.USD),
    )
    result = evaluate_invoice(rounded_invoice, purchase_order)
    assert result.is_valid
    assert result.calculated_total.amount == Decimal("0.02")
    wrong = replace(
        rounded_invoice, declared_total=Money(Decimal("0.01"), Currency.USD)
    )
    assert [issue.code for issue in evaluate_invoice(wrong, purchase_order).issues] == [
        IssueCode.INVOICE_TOTAL_MISMATCH
    ]


def test_repeated_checks_ignore_the_callers_decimal_context(
    invoice: Invoice, purchase_order: PurchaseOrder
) -> None:
    """A caller's low precision and rounding mode cannot change the rule snapshot."""
    first = evaluate_invoice(invoice, purchase_order)
    with localcontext() as context:
        context.prec = 2
        context.rounding = ROUND_DOWN
        repeated = evaluate_invoice(invoice, purchase_order)
    assert first == repeated
    assert first.calculated_total.amount == Decimal("30.00")


def test_result_preserves_immutable_input_snapshot(
    invoice: Invoice, purchase_order: PurchaseOrder
) -> None:
    """A later replacement document does not rewrite an earlier check's inputs."""
    result = evaluate_invoice(invoice, purchase_order)
    revised = replace(invoice, supplier_id="SUPPLIER-DEMO-OTHER")
    later = evaluate_invoice(revised, purchase_order)
    assert result.invoice.supplier_id == "SUPPLIER-DEMO-001"
    assert later.invoice.supplier_id == "SUPPLIER-DEMO-OTHER"
    assert result.is_valid
    assert not later.is_valid
    with pytest.raises(FrozenInstanceError):
        result.issues = ()  # type: ignore[misc]


@pytest.mark.parametrize("field", ["invoice", "purchase_order", "policy"])
def test_rule_entry_point_rejects_untyped_inputs(
    invoice: Invoice, purchase_order: PurchaseOrder, field: str
) -> None:
    """Runtime adapters cannot replace typed document and policy values with strings."""
    bad_invoice = cast(Invoice, "unparsed") if field == "invoice" else invoice
    bad_order = (
        cast(PurchaseOrder, "unparsed") if field == "purchase_order" else purchase_order
    )
    bad_policy = cast(ValidationPolicy, "unparsed") if field == "policy" else None
    with pytest.raises(DomainValidationError) as error:
        evaluate_invoice(bad_invoice, bad_order, bad_policy)
    assert error.value.code == "invalid_type"
    assert error.value.field == field


@pytest.mark.parametrize(
    "currencies",
    [(), (Currency.USD, Currency.USD), cast(tuple[Currency, ...], ("USD",))],
)
def test_policy_rejects_empty_duplicate_or_untyped_currency_lists(
    currencies: tuple[Currency, ...],
) -> None:
    """An explicit policy has at least one unique supported currency enum member."""
    with pytest.raises(DomainValidationError) as error:
        ValidationPolicy(allowed_currencies=currencies)
    assert error.value.code == "invalid_policy"
