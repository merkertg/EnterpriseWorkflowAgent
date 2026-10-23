"""Immutable fictional purchasing documents with deterministic line totals."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from app.domain.errors import DomainValidationError
from app.domain.money import Currency, Money, sum_money, validate_quantity

_MAX_LINES = 100


def validate_text(value: str, field: str, max_length: int = 128) -> str:
    """Trim required text and reject values too long for this learning project."""
    if type(value) is not str:
        raise DomainValidationError("invalid_type", field, f"{field} must be a string.")
    normalized = value.strip()
    if not normalized or len(normalized) > max_length:
        raise DomainValidationError(
            "invalid_text", field, f"{field} must contain 1 to {max_length} characters."
        )
    return normalized


def _validate_document_currency(currency: Currency) -> None:
    """Keep document currency explicit, without accepting strings or other enums."""
    if type(currency) is not Currency:
        raise DomainValidationError(
            "invalid_currency",
            "currency",
            "Currency must be a supported Currency member.",
        )


@dataclass(frozen=True, slots=True)
class LineItem:
    """Describe one uniquely identified product and its exact fictional price."""

    sku: str
    description: str
    quantity: Decimal
    unit_price: Money

    def __post_init__(self) -> None:
        """Validate and normalize a line, including the representable total bound."""
        object.__setattr__(self, "sku", validate_text(self.sku, "sku"))
        object.__setattr__(
            self, "description", validate_text(self.description, "description", 500)
        )
        object.__setattr__(self, "quantity", validate_quantity(self.quantity))
        if type(self.unit_price) is not Money:
            raise DomainValidationError(
                "invalid_type", "unit_price", "Unit price must be Money."
            )
        # Fail at the boundary, instead of creating a line whose total later fails.
        _ = self.total

    @property
    def total(self) -> Money:
        """Return the quantity times unit price, rounded once to two decimal places."""
        return self.unit_price.multiply(self.quantity)


def _validate_lines(lines: tuple[LineItem, ...], currency: Currency) -> None:
    """Require a bounded immutable collection with unique same-currency SKUs."""
    if type(lines) is not tuple:
        raise DomainValidationError(
            "invalid_type", "lines", "Document lines must be an immutable tuple."
        )
    if not 1 <= len(lines) <= _MAX_LINES:
        raise DomainValidationError(
            "invalid_lines",
            "lines",
            f"A document must contain 1 to {_MAX_LINES} lines.",
        )
    seen_skus: set[str] = set()
    for line in lines:
        if type(line) is not LineItem:
            raise DomainValidationError(
                "invalid_type", "lines", "Every document line must be a LineItem."
            )
        if line.sku in seen_skus:
            raise DomainValidationError(
                "duplicate_sku", "lines", f"Duplicate SKU: {line.sku}."
            )
        if line.unit_price.currency is not currency:
            raise DomainValidationError(
                "currency_mismatch", "lines", "Line and document currencies must match."
            )
        seen_skus.add(line.sku)


@dataclass(frozen=True, slots=True)
class Invoice:
    """Store a supplier's invoice while preserving a potentially incorrect total."""

    invoice_id: str
    supplier_id: str
    purchase_order_id: str
    currency: Currency
    lines: tuple[LineItem, ...]
    declared_total: Money

    def __post_init__(self) -> None:
        """Validate structure; declared-versus-calculated differences are rule input."""
        for field in ("invoice_id", "supplier_id", "purchase_order_id"):
            object.__setattr__(self, field, validate_text(getattr(self, field), field))
        _validate_document_currency(self.currency)
        _validate_lines(self.lines, self.currency)
        if type(self.declared_total) is not Money:
            raise DomainValidationError(
                "invalid_type", "declared_total", "Declared total must be Money."
            )
        if self.declared_total.currency is not self.currency:
            raise DomainValidationError(
                "currency_mismatch",
                "declared_total",
                "Declared total currency must match.",
            )
        _ = self.calculated_total

    @property
    def calculated_total(self) -> Money:
        """Sum individually rounded line totals without trusting the declared total."""
        return sum_money(tuple(line.total for line in self.lines), self.currency)


@dataclass(frozen=True, slots=True)
class PurchaseOrder:
    """Store the immutable fictional order used to check a supplier invoice."""

    purchase_order_id: str
    supplier_id: str
    currency: Currency
    lines: tuple[LineItem, ...]

    def __post_init__(self) -> None:
        """Validate order identifiers, line currencies and the aggregate money bound."""
        for field in ("purchase_order_id", "supplier_id"):
            object.__setattr__(self, field, validate_text(getattr(self, field), field))
        _validate_document_currency(self.currency)
        _validate_lines(self.lines, self.currency)
        _ = self.total

    @property
    def total(self) -> Money:
        """Sum individually rounded line totals using the order's explicit currency."""
        return sum_money(tuple(line.total for line in self.lines), self.currency)
