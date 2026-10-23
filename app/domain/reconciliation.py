"""Deterministic invoice-to-purchase-order reconciliation under versioned rules.

Line by line, the invoice is compared with its order: SKU, quantity, unit
price and totals, plus header fields and duplicate invoices. Tolerances and
rounding come from an explicit ReconciliationRules version; a currency without
a rule blocks automatic progress. Every amount is computed here with Decimal;
no model output takes part in the arithmetic.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

from app.domain.errors import DomainValidationError
from app.domain.extraction import Evidence, ExtractionResult, line_field
from app.domain.models import Invoice, PurchaseOrder, validate_text
from app.domain.money import Currency, Money, sum_money

# Identifies the comparison logic; a change in rules code needs a new value.
RECONCILIATION_ENGINE = "reconciliation.v1"
# The only rounding the money engine implements; rules must state it explicitly.
SUPPORTED_ROUNDING = "ROUND_HALF_UP"
SUPPORTED_DECIMAL_PLACES = 2


class DiscrepancyCode(StrEnum):
    """Stable reasons why a reconciliation needs a person."""

    MISSING_SOURCE_DOCUMENT = "missing_source_document"
    DUPLICATE_INVOICE = "duplicate_invoice"
    NO_RULE_FOR_CURRENCY = "no_rule_for_currency"
    MISSING_PURCHASE_ORDER = "missing_purchase_order"
    PURCHASE_ORDER_REFERENCE_MISMATCH = "purchase_order_reference_mismatch"
    SUPPLIER_MISMATCH = "supplier_mismatch"
    CURRENCY_MISMATCH = "currency_mismatch"
    UNKNOWN_LINE = "unknown_line"
    QUANTITY_EXCEEDS_ORDER = "quantity_exceeds_order"
    QUANTITY_BELOW_ORDER = "quantity_below_order"
    UNIT_PRICE_MISMATCH = "unit_price_mismatch"
    DECLARED_TOTAL_MISMATCH = "declared_total_mismatch"
    EXPECTED_TOTAL_MISMATCH = "expected_total_mismatch"


class ReconciliationStatus(StrEnum):
    """Matched means no discrepancy; it is still not an approval."""

    MATCHED = "matched"
    NEEDS_REVIEW = "needs_review"


def _require_money(value: object, currency: Currency, field: str) -> None:
    """Require a non-negative Money value in the rule's currency."""
    if type(value) is not Money or value.currency is not currency:
        raise DomainValidationError(
            "invalid_rule", field, f"{field} must be Money in {currency.value}."
        )


@dataclass(frozen=True, slots=True)
class CurrencyRule:
    """Tolerances and the declared rounding for one currency."""

    currency: Currency
    unit_price_tolerance: Money
    total_tolerance: Money
    decimal_places: int = SUPPORTED_DECIMAL_PLACES
    rounding: str = SUPPORTED_ROUNDING

    def __post_init__(self) -> None:
        """Accept only rounding the engine really applies, never a silent mismatch."""
        if type(self.currency) is not Currency:
            raise DomainValidationError("invalid_rule", "currency", "Use Currency.")
        _require_money(self.unit_price_tolerance, self.currency, "unit_price_tolerance")
        _require_money(self.total_tolerance, self.currency, "total_tolerance")
        if (
            type(self.decimal_places) is not int
            or self.decimal_places != SUPPORTED_DECIMAL_PLACES
            or self.rounding != SUPPORTED_ROUNDING
        ):
            raise DomainValidationError(
                "unsupported_rounding",
                "rounding",
                f"Only {SUPPORTED_DECIMAL_PLACES} decimal places with "
                f"{SUPPORTED_ROUNDING} are implemented.",
            )

    def label(self, name: str) -> str:
        """Name the applied rule with its parameter, e.g. USD.total_tolerance=0.10."""
        value: Money = getattr(self, name)
        return f"{self.currency.value}.{name}={value.amount}"


@dataclass(frozen=True, slots=True)
class ReconciliationRules:
    """A versioned rule set; currency rules are kept sorted for stable hashes."""

    rules_id: str
    currency_rules: tuple[CurrencyRule, ...]
    allow_partial_quantities: bool = True
    require_source_document: bool = True

    def __post_init__(self) -> None:
        """Validate types, forbid duplicate currencies and normalize order."""
        object.__setattr__(self, "rules_id", validate_text(self.rules_id, "rules_id"))
        if type(self.currency_rules) is not tuple or any(
            type(item) is not CurrencyRule for item in self.currency_rules
        ):
            raise DomainValidationError(
                "invalid_rule", "currency_rules", "Use a tuple of CurrencyRule."
            )
        currencies = [item.currency for item in self.currency_rules]
        if len(set(currencies)) != len(currencies):
            raise DomainValidationError(
                "invalid_rule", "currency_rules", "One rule per currency."
            )
        for name in ("allow_partial_quantities", "require_source_document"):
            if type(getattr(self, name)) is not bool:
                raise DomainValidationError("invalid_rule", name, f"{name} is a bool.")
        ordered = tuple(sorted(self.currency_rules, key=lambda item: item.currency))
        object.__setattr__(self, "currency_rules", ordered)

    def rule_for(self, currency: Currency) -> CurrencyRule | None:
        """Return the rule for a currency, or None when it is not configured."""
        return next((r for r in self.currency_rules if r.currency is currency), None)


@dataclass(frozen=True, slots=True)
class Discrepancy:
    """A reason with expected and actual values, the rule and its source spans."""

    code: DiscrepancyCode
    field: str
    message: str
    expected: str | None
    actual: str | None
    rule: str
    sources: tuple[Evidence, ...] = ()


@dataclass(frozen=True, slots=True)
class LineMatch:
    """How one invoice line compares with the order line of the same SKU."""

    invoice_line: int
    sku: str
    order_line: int | None
    invoiced_quantity: Decimal
    ordered_quantity: Decimal | None
    invoice_unit_price: Money
    order_unit_price: Money | None
    invoice_line_total: Money
    expected_line_total: Money | None


@dataclass(frozen=True, slots=True)
class ReconciliationResult:
    """Computed totals, line comparisons and every discrepancy in stable order."""

    status: ReconciliationStatus
    currency: Currency
    calculated_total: Money
    expected_total: Money | None
    order_total: Money | None
    lines: tuple[LineMatch, ...]
    discrepancies: tuple[Discrepancy, ...]


def _money(value: Money) -> str:
    """Format an exact amount with its currency."""
    return f"{value.amount:.2f} {value.currency.value}"


def _quantity(value: Decimal) -> str:
    """Print a quantity without insignificant zeros: 2.000000 becomes 2."""
    return format(value.normalize(), "f")


def sources_from_extraction(
    extraction: ExtractionResult | None,
) -> dict[str, tuple[Evidence, ...]]:
    """Map field names to evidence spans; an invoice without a document has none."""
    if extraction is None:
        return {}
    return {item.name: item.evidence for item in extraction.fields}


def reconcile(
    invoice: Invoice,
    purchase_order: PurchaseOrder | None,
    rules: ReconciliationRules,
    *,
    has_source_document: bool,
    sources: Mapping[str, tuple[Evidence, ...]] | None = None,
    duplicate_workflow_ids: tuple[str, ...] = (),
) -> ReconciliationResult:
    """Compare an invoice with its order; equal inputs always give equal results."""
    if type(invoice) is not Invoice or type(rules) is not ReconciliationRules:
        raise DomainValidationError(
            "invalid_type", "invoice", "Use an Invoice and ReconciliationRules."
        )
    if purchase_order is not None and type(purchase_order) is not PurchaseOrder:
        raise DomainValidationError(
            "invalid_type", "purchase_order", "Use a PurchaseOrder or None."
        )
    cited = sources or {}
    found: list[Discrepancy] = []

    def add(
        code: DiscrepancyCode,
        field: str,
        message: str,
        expected: str | None,
        actual: str | None,
        rule: str,
        evidence: tuple[Evidence, ...] | None = None,
    ) -> None:
        """Record a discrepancy with the source spans of its field."""
        spans = cited.get(field, ()) if evidence is None else evidence
        found.append(
            Discrepancy(code, field, message, expected, actual, rule, tuple(spans))
        )

    calculated = invoice.calculated_total
    if rules.require_source_document and not has_source_document:
        add(
            DiscrepancyCode.MISSING_SOURCE_DOCUMENT,
            "source_document",
            "The invoice was not produced by a complete document extraction.",
            "complete extraction",
            None,
            "require_source_document=true",
        )
    if duplicate_workflow_ids:
        add(
            DiscrepancyCode.DUPLICATE_INVOICE,
            "invoice_id",
            "The same supplier invoice number is already in other workflows.",
            None,
            ", ".join(sorted(duplicate_workflow_ids)),
            "unique supplier_id + invoice_id among open and approved workflows",
        )
    rule = rules.rule_for(invoice.currency)
    if rule is None:
        add(
            DiscrepancyCode.NO_RULE_FOR_CURRENCY,
            "currency",
            "No tolerance rule exists for this currency.",
            ", ".join(item.currency.value for item in rules.currency_rules) or None,
            invoice.currency.value,
            f"{rules.rules_id}.currency_rules",
        )

    def unmatched_lines() -> tuple[LineMatch, ...]:
        """Describe invoice lines when no order comparison is possible."""
        return tuple(
            LineMatch(
                index,
                line.sku,
                None,
                line.quantity,
                None,
                line.unit_price,
                None,
                line.total,
                None,
            )
            for index, line in enumerate(invoice.lines, start=1)
        )

    def finish(
        lines: tuple[LineMatch, ...],
        expected_total: Money | None,
        order_total: Money | None,
    ) -> ReconciliationResult:
        """Build the result; any discrepancy means review."""
        status = (
            ReconciliationStatus.NEEDS_REVIEW if found else ReconciliationStatus.MATCHED
        )
        return ReconciliationResult(
            status,
            invoice.currency,
            calculated,
            expected_total,
            order_total,
            lines,
            tuple(found),
        )

    if purchase_order is None:
        add(
            DiscrepancyCode.MISSING_PURCHASE_ORDER,
            "purchase_order_id",
            "No purchase order is available for comparison.",
            invoice.purchase_order_id,
            None,
            "purchase order required",
        )
        return finish(unmatched_lines(), None, None)

    if invoice.purchase_order_id != purchase_order.purchase_order_id:
        add(
            DiscrepancyCode.PURCHASE_ORDER_REFERENCE_MISMATCH,
            "purchase_order_id",
            "The invoice references a different purchase order.",
            purchase_order.purchase_order_id,
            invoice.purchase_order_id,
            "exact match",
        )
    if invoice.supplier_id != purchase_order.supplier_id:
        add(
            DiscrepancyCode.SUPPLIER_MISMATCH,
            "supplier_id",
            "Invoice and order suppliers differ.",
            purchase_order.supplier_id,
            invoice.supplier_id,
            "exact match",
        )
    if invoice.currency is not purchase_order.currency:
        add(
            DiscrepancyCode.CURRENCY_MISMATCH,
            "currency",
            "Currencies differ; amounts are not converted or compared.",
            purchase_order.currency.value,
            invoice.currency.value,
            "same currency, no conversion",
        )
        return finish(unmatched_lines(), None, None)

    ordered = {
        line.sku: (index, line)
        for index, line in enumerate(purchase_order.lines, start=1)
    }
    matches: list[LineMatch] = []
    expected_lines: list[Money] = []
    matched_lines: list[Money] = []
    price_spans: list[Evidence] = []
    for index, line in enumerate(invoice.lines, start=1):
        order_match = ordered.get(line.sku)
        if order_match is None:
            add(
                DiscrepancyCode.UNKNOWN_LINE,
                line_field(index, "sku"),
                "This SKU is not on the purchase order.",
                ", ".join(ordered),
                line.sku,
                "every invoiced SKU is on the order",
            )
            matches.append(
                LineMatch(
                    index,
                    line.sku,
                    None,
                    line.quantity,
                    None,
                    line.unit_price,
                    None,
                    line.total,
                    None,
                )
            )
            continue
        order_index, order_line = order_match
        if line.quantity > order_line.quantity:
            add(
                DiscrepancyCode.QUANTITY_EXCEEDS_ORDER,
                line_field(index, "quantity"),
                "More units are invoiced than were ordered.",
                f"<= {_quantity(order_line.quantity)}",
                _quantity(line.quantity),
                "invoiced quantity <= ordered quantity",
            )
        elif line.quantity < order_line.quantity and not rules.allow_partial_quantities:
            add(
                DiscrepancyCode.QUANTITY_BELOW_ORDER,
                line_field(index, "quantity"),
                "Partial invoicing is not allowed by these rules.",
                _quantity(order_line.quantity),
                _quantity(line.quantity),
                f"{rules.rules_id}.allow_partial_quantities=false",
            )
        if line.unit_price != order_line.unit_price:
            price_spans += cited.get(line_field(index, "unit_price"), ())
        if (
            rule is not None
            and line.unit_price.distance(order_line.unit_price)
            > rule.unit_price_tolerance.amount
        ):
            add(
                DiscrepancyCode.UNIT_PRICE_MISMATCH,
                line_field(index, "unit_price"),
                "The unit price differs from the order beyond the tolerance.",
                f"{_money(order_line.unit_price)} ± {rule.unit_price_tolerance.amount}",
                _money(line.unit_price),
                rule.label("unit_price_tolerance"),
            )
        # Expected = ordered price × invoiced quantity, rounded once per line.
        expected_line = order_line.unit_price.multiply(line.quantity)
        expected_lines.append(expected_line)
        matched_lines.append(line.total)
        matches.append(
            LineMatch(
                index,
                line.sku,
                order_index,
                line.quantity,
                order_line.quantity,
                line.unit_price,
                order_line.unit_price,
                line.total,
                expected_line,
            )
        )

    expected_total = sum_money(tuple(expected_lines), invoice.currency)
    # Unknown lines are reported on their own; only matched lines are compared.
    matched_total = sum_money(tuple(matched_lines), invoice.currency)
    if rule is not None:
        if invoice.declared_total.distance(calculated) > rule.total_tolerance.amount:
            add(
                DiscrepancyCode.DECLARED_TOTAL_MISMATCH,
                "declared_total",
                "The declared total differs from the sum of the invoice lines.",
                f"{_money(calculated)} ± {rule.total_tolerance.amount}",
                _money(invoice.declared_total),
                rule.label("total_tolerance"),
            )
        if matched_total.distance(expected_total) > rule.total_tolerance.amount:
            # Per-unit differences inside the price tolerance can still add up.
            add(
                DiscrepancyCode.EXPECTED_TOTAL_MISMATCH,
                "matched_lines_total",
                "Matched lines differ from order prices beyond the total tolerance.",
                f"{_money(expected_total)} ± {rule.total_tolerance.amount}",
                _money(matched_total),
                rule.label("total_tolerance"),
                tuple(price_spans),
            )
    return finish(tuple(matches), expected_total, purchase_order.total)
