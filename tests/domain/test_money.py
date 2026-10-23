"""Check exact arithmetic and independence from the caller's Decimal context."""

from dataclasses import FrozenInstanceError
from decimal import (
    ROUND_DOWN,
    Decimal,
    Inexact,
    InvalidOperation,
    Rounded,
    localcontext,
)
from typing import cast

import pytest

from app.domain.errors import DomainValidationError
from app.domain.money import Currency, Money, sum_money, validate_quantity


@pytest.mark.parametrize("currency", list(Currency))
def test_demo_currencies_use_exact_two_decimal_amounts(currency: Currency) -> None:
    """Accepted trailing zeroes normalize without rounding away actual information."""
    amount = Money(Decimal("1.23000"), currency)
    assert amount.amount == Decimal("1.23")
    assert amount.amount.as_tuple().exponent == -2
    assert amount.currency is currency


@pytest.mark.parametrize("amount", [Decimal("0"), Decimal("999999999999.99")])
def test_money_accepts_both_inclusive_amount_bounds(amount: Decimal) -> None:
    """Free products and the documented maximum are valid domain values."""
    assert Money(amount, Currency.USD).amount == amount


@pytest.mark.parametrize("amount", [1, 1.5, True, "1.23", None])
def test_money_rejects_implicit_decimal_conversions(amount: object) -> None:
    """Callers must choose Decimal parsing explicitly, without float contamination."""
    with pytest.raises(DomainValidationError) as caught:
        Money(cast(Decimal, amount), Currency.USD)
    assert caught.value.code == "invalid_type"
    assert caught.value.field == "amount"


@pytest.mark.parametrize(
    "amount, code",
    [
        ("NaN", "not_finite"),
        ("sNaN", "not_finite"),
        ("Infinity", "not_finite"),
        ("-Infinity", "not_finite"),
        ("-0.01", "out_of_range"),
        ("1000000000000.00", "out_of_range"),
        ("1.231", "invalid_precision"),
        ("0.0000000000000000000001", "invalid_precision"),
    ],
)
def test_money_rejects_nonfinite_out_of_range_or_lossy_values(
    amount: str, code: str
) -> None:
    """Invalid input fails deterministically before arithmetic reaches the rules."""
    with pytest.raises(DomainValidationError) as caught:
        Money(Decimal(amount), Currency.USD)
    assert caught.value.code == code
    assert caught.value.field == "amount"


@pytest.mark.parametrize("currency", ["USD", "GBP", None, 1])
def test_money_requires_currency_enum_members(currency: object) -> None:
    """A familiar spelling is still unvalidated boundary input, not an enum member."""
    with pytest.raises(DomainValidationError, match="Currency"):
        Money(Decimal("1.00"), cast(Currency, currency))


def test_multiplication_rounds_half_up_once_and_addition_stays_exact() -> None:
    """The line-total policy gives a predictable cent for a fractional quantity."""
    line_total = Money(Decimal("0.01"), Currency.USD).multiply(Decimal("0.5"))
    assert line_total.amount == Decimal("0.01")
    assert line_total.add(Money(Decimal("0.09"), Currency.USD)).amount == Decimal(
        "0.10"
    )


def test_all_arithmetic_ignores_callers_precision_rounding_and_traps() -> None:
    """An embedding adapter cannot change stored money by altering Decimal context."""
    with localcontext() as context:
        context.prec = 2
        context.rounding = ROUND_DOWN
        context.traps[Inexact] = True
        context.traps[Rounded] = True
        context.traps[InvalidOperation] = True
        amount = Money(Decimal("123456789.23000"), Currency.USD)
        quantity = validate_quantity(Decimal("2.0000000"))
        assert quantity == Decimal("2.000000")
        assert amount.multiply(quantity).amount == Decimal("246913578.46")
        assert amount.add(Money(Decimal("0.77"), Currency.USD)).amount == Decimal(
            "123456790.00"
        )
        assert sum_money((amount, amount), Currency.USD).amount == Decimal(
            "246913578.46"
        )
        assert Money(Decimal("0.01"), Currency.USD).multiply(Decimal("0.5")).amount == (
            Decimal("0.01")
        )
        assert not context.flags[Inexact]
        assert not context.flags[Rounded]


@pytest.mark.parametrize("quantity", [Decimal("0.000001"), Decimal("999999.999999")])
def test_quantity_accepts_the_smallest_unit_and_maximum(quantity: Decimal) -> None:
    """Quantity bounds and six-place normalization are explicit domain choices."""
    assert validate_quantity(quantity) == quantity


@pytest.mark.parametrize("quantity", [1, True, 0.5, "2", None])
def test_quantity_rejects_implicit_conversion(quantity: object) -> None:
    """Runtime validation also applies to dynamically typed adapter input."""
    with pytest.raises(DomainValidationError) as caught:
        validate_quantity(cast(Decimal, quantity), "requested_quantity")
    assert caught.value.field == "requested_quantity"
    assert caught.value.code == "invalid_type"


@pytest.mark.parametrize(
    "quantity", ["0", "-1", "NaN", "sNaN", "Infinity", "1000000", "0.0000001"]
)
def test_quantity_rejects_unusable_values(quantity: str) -> None:
    """Orders require positive finite quantities with no hidden precision loss."""
    with pytest.raises(DomainValidationError):
        validate_quantity(Decimal(quantity))
    with pytest.raises(DomainValidationError):
        Money(Decimal("1.00"), Currency.USD).multiply(Decimal(quantity))


def test_money_addition_rejects_mixed_currencies_and_invalid_values() -> None:
    """Arithmetic never invents an exchange rate or accepts an unvalidated number."""
    amount = Money(Decimal("1.00"), Currency.USD)
    with pytest.raises(DomainValidationError) as caught:
        amount.add(Money(Decimal("1.00"), Currency.EUR))
    assert caught.value.code == "currency_mismatch"
    with pytest.raises(DomainValidationError):
        amount.add(cast(Money, Decimal("2.00")))


def test_arithmetic_rejects_totals_above_the_money_limit() -> None:
    """Representable input values cannot create an out-of-range resulting amount."""
    maximum = Money(Decimal("999999999999.99"), Currency.USD)
    with pytest.raises(DomainValidationError):
        maximum.multiply(Decimal("2"))
    with pytest.raises(DomainValidationError):
        maximum.add(Money(Decimal("0.01"), Currency.USD))
    with pytest.raises(DomainValidationError):
        sum_money((maximum, maximum), Currency.USD)


def test_sum_money_handles_empty_tuples_and_rejects_mutable_or_mixed_input() -> None:
    """The empty aggregate is a typed zero, while other input still needs validation."""
    amount = Money(Decimal("1.00"), Currency.USD)
    assert sum_money((), Currency.EUR) == Money(Decimal("0.00"), Currency.EUR)
    with pytest.raises(DomainValidationError):
        sum_money(cast(tuple[Money, ...], [amount]), Currency.USD)
    with pytest.raises(DomainValidationError):
        sum_money((amount,), Currency.EUR)
    with pytest.raises(DomainValidationError):
        sum_money((), cast(Currency, "EUR"))


def test_money_is_an_immutable_snapshot() -> None:
    """An approval can safely keep the original amount reference for later checks."""
    amount = Money(Decimal("1.00"), Currency.USD)
    field = "amount"
    with pytest.raises(FrozenInstanceError):
        setattr(amount, field, Decimal("2.00"))
    assert not hasattr(amount, "__dict__")


def test_domain_error_exposes_code_field_and_human_message() -> None:
    """Adapters can map a domain error without parsing human-readable text."""
    error = DomainValidationError("invalid_type", "amount", "Use Decimal.")
    assert isinstance(error, ValueError)
    assert error.code == "invalid_type"
    assert error.field == "amount"
    assert str(error) == "Use Decimal."
