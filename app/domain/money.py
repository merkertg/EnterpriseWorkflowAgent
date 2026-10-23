"""Exact, bounded money arithmetic for fictional two-decimal demo currencies."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Context, Decimal, localcontext
from enum import StrEnum

from app.domain.errors import DomainValidationError

_MONEY_QUANTUM = Decimal("0.01")
_QUANTITY_QUANTUM = Decimal("0.000001")
_MAX_AMOUNT = Decimal("999999999999.99")
_MAX_QUANTITY = Decimal("999999.999999")
# Copy this context for every operation; caller precision, rounding and traps cannot
# change a document's result. Precision 40 covers all bounded input products/sums.
_ARITHMETIC_CONTEXT = Context(prec=40, rounding=ROUND_HALF_UP, traps=[])


class Currency(StrEnum):
    """List the demo's supported currencies, all with two decimal places; no FX."""

    USD = "USD"
    EUR = "EUR"
    UZS = "UZS"


def _validate_currency(currency: Currency) -> None:
    """Require an explicit supported enum member instead of silently parsing text."""
    if type(currency) is not Currency:
        raise DomainValidationError(
            "invalid_currency",
            "currency",
            "Currency must be a supported Currency member.",
        )


def _normalize_decimal(
    value: Decimal,
    *,
    field: str,
    maximum: Decimal,
    quantum: Decimal,
    positive: bool,
) -> Decimal:
    """Validate a Decimal and discard only insignificant trailing decimal zeroes."""
    if type(value) is not Decimal:
        raise DomainValidationError(
            "invalid_type", field, f"{field} must be a Decimal; conversion is explicit."
        )
    if not value.is_finite():
        raise DomainValidationError(
            "not_finite", field, f"{field} must be a finite Decimal."
        )
    if value < 0 or (positive and value == 0) or value > maximum:
        lower_bound = "positive" if positive else "nonnegative"
        raise DomainValidationError(
            "out_of_range",
            field,
            f"{field} must be {lower_bound} and at most {maximum}.",
        )
    with localcontext(_ARITHMETIC_CONTEXT):
        normalized = value.quantize(quantum)
    if normalized != value:
        raise DomainValidationError(
            "invalid_precision",
            field,
            f"{field} must be exactly representable in increments of {quantum}.",
        )
    return normalized


def validate_quantity(value: Decimal, field: str = "quantity") -> Decimal:
    """Return a positive bounded quantity with at most six decimal places."""
    return _normalize_decimal(
        value,
        field=field,
        maximum=_MAX_QUANTITY,
        quantum=_QUANTITY_QUANTUM,
        positive=True,
    )


@dataclass(frozen=True, slots=True)
class Money:
    """Store an immutable exact amount with a required supported currency."""

    amount: Decimal
    currency: Currency

    def __post_init__(self) -> None:
        """Reject lossy input and normalize accepted values to two decimal places."""
        _validate_currency(self.currency)
        normalized = _normalize_decimal(
            self.amount,
            field="amount",
            maximum=_MAX_AMOUNT,
            quantum=_MONEY_QUANTUM,
            positive=False,
        )
        object.__setattr__(self, "amount", normalized)

    def multiply(self, quantity: Decimal) -> Money:
        """Round a line's exact bounded-quantity product once, using HALF_UP."""
        checked_quantity = validate_quantity(quantity)
        with localcontext(_ARITHMETIC_CONTEXT):
            amount = (self.amount * checked_quantity).quantize(_MONEY_QUANTUM)
        return Money(amount, self.currency)

    def add(self, other: Money) -> Money:
        """Add same-currency amounts without implicit conversion or exchange rates."""
        if type(other) is not Money:
            raise DomainValidationError(
                "invalid_type", "other", "The other amount must be Money."
            )
        if self.currency is not other.currency:
            raise DomainValidationError(
                "currency_mismatch", "currency", "Money currencies must match."
            )
        with localcontext(_ARITHMETIC_CONTEXT):
            amount = self.amount + other.amount
        return Money(amount, self.currency)


def sum_money(values: tuple[Money, ...], currency: Currency) -> Money:
    """Sum a tuple in order, validating each currency and allowing an empty total."""
    _validate_currency(currency)
    if type(values) is not tuple:
        raise DomainValidationError(
            "invalid_type", "values", "Money values must be an immutable tuple."
        )
    result = Money(Decimal("0.00"), currency)
    for value in values:
        result = result.add(value)
    return result
