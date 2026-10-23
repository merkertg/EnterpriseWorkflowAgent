"""Deterministic basic invoice checks, independent of HTTP and persistence."""

from dataclasses import dataclass
from enum import StrEnum

from app.domain.errors import DomainValidationError
from app.domain.models import Invoice, PurchaseOrder, validate_text
from app.domain.money import Currency, Money


class IssueCode(StrEnum):
    """Stable reasons that require review instead of silent approval."""

    UNSUPPORTED_CURRENCY = "unsupported_currency"
    INVOICE_TOTAL_MISMATCH = "invoice_total_mismatch"
    MISSING_PURCHASE_ORDER = "missing_purchase_order"
    PURCHASE_ORDER_REFERENCE_MISMATCH = "purchase_order_reference_mismatch"
    SUPPLIER_MISMATCH = "supplier_mismatch"
    CURRENCY_MISMATCH = "currency_mismatch"
    PURCHASE_ORDER_TOTAL_EXCEEDED = "purchase_order_total_exceeded"


@dataclass(frozen=True, slots=True)
class ValidationPolicy:
    """Versioned demo currency allowlist; detailed tolerances belong to Stage 05."""

    policy_id: str = "basic-demo-v1"
    allowed_currencies: tuple[Currency, ...] = (
        Currency.USD,
        Currency.EUR,
        Currency.UZS,
    )

    def __post_init__(self) -> None:
        """Keep policy data immutable, explicit and free of ambiguous duplicates."""
        object.__setattr__(
            self, "policy_id", validate_text(self.policy_id, "policy_id")
        )
        if (
            not isinstance(self.allowed_currencies, tuple)
            or not self.allowed_currencies
            or any(not isinstance(item, Currency) for item in self.allowed_currencies)
        ):
            raise DomainValidationError(
                "invalid_policy", "allowed_currencies", "Use a nonempty currency tuple"
            )
        if len(set(self.allowed_currencies)) != len(self.allowed_currencies):
            raise DomainValidationError(
                "invalid_policy", "allowed_currencies", "Currencies must be unique"
            )


@dataclass(frozen=True, slots=True)
class RuleIssue:
    """A machine-readable reason plus printable expected and actual values."""

    code: IssueCode
    field: str
    message: str
    expected: str | None = None
    actual: str | None = None

    def __post_init__(self) -> None:
        """Keep findings immutable even when a caller constructs one directly."""
        if not isinstance(self.code, IssueCode):
            raise DomainValidationError("invalid_type", "code", "Use an IssueCode")
        object.__setattr__(self, "field", validate_text(self.field, "field"))
        object.__setattr__(self, "message", validate_text(self.message, "message", 500))
        for name in ("expected", "actual"):
            value = getattr(self, name)
            if value is not None and not isinstance(value, str):
                raise DomainValidationError(
                    "invalid_type", name, "Use a string or None for a finding value"
                )


@dataclass(frozen=True, slots=True)
class ValidationResult:
    """An in-memory snapshot of exact inputs and their ordered basic findings."""

    invoice: Invoice
    purchase_order: PurchaseOrder | None
    policy: ValidationPolicy
    issues: tuple[RuleIssue, ...]

    def __post_init__(self) -> None:
        """Require typed snapshots and an immutable collection of typed findings."""
        if not isinstance(self.invoice, Invoice):
            raise DomainValidationError("invalid_type", "invoice", "Use an Invoice")
        if self.purchase_order is not None and not isinstance(
            self.purchase_order, PurchaseOrder
        ):
            raise DomainValidationError(
                "invalid_type", "purchase_order", "Use a PurchaseOrder or None"
            )
        if not isinstance(self.policy, ValidationPolicy):
            raise DomainValidationError(
                "invalid_type", "policy", "Use a ValidationPolicy"
            )
        if not isinstance(self.issues, tuple) or any(
            not isinstance(issue, RuleIssue) for issue in self.issues
        ):
            raise DomainValidationError(
                "invalid_type", "issues", "Use a tuple of RuleIssue values"
            )

    @property
    def is_valid(self) -> bool:
        """Signal eligibility for a human decision, never automatic approval."""
        return not self.issues

    @property
    def calculated_total(self) -> Money:
        """Expose the sum of separately rounded invoice line extensions."""
        return self.invoice.calculated_total


def _money_label(value: Money) -> str:
    """Format an exact decimal amount and its currency without float conversion."""
    return f"{value.amount:.2f} {value.currency.value}"


def evaluate_invoice(
    invoice: Invoice,
    purchase_order: PurchaseOrder | None,
    policy: ValidationPolicy | None = None,
) -> ValidationResult:
    """Return all applicable basic findings in a stable order without side effects.

    This stage checks references, header consistency and basic totals. It does
    not claim line-by-line reconciliation, duplicate detection or tax handling.
    """
    if not isinstance(invoice, Invoice):
        raise DomainValidationError("invalid_type", "invoice", "Use an Invoice")
    if purchase_order is not None and not isinstance(purchase_order, PurchaseOrder):
        raise DomainValidationError(
            "invalid_type", "purchase_order", "Use a PurchaseOrder or None"
        )
    resolved_policy = ValidationPolicy() if policy is None else policy
    if not isinstance(resolved_policy, ValidationPolicy):
        raise DomainValidationError("invalid_type", "policy", "Use a ValidationPolicy")

    issues: list[RuleIssue] = []
    if invoice.currency not in resolved_policy.allowed_currencies:
        issues.append(
            RuleIssue(
                IssueCode.UNSUPPORTED_CURRENCY,
                "invoice.currency",
                "Invoice currency is not allowed by this demo policy",
                ",".join(item.value for item in resolved_policy.allowed_currencies),
                invoice.currency.value,
            )
        )
    calculated_total = invoice.calculated_total
    if invoice.declared_total != calculated_total:
        issues.append(
            RuleIssue(
                IssueCode.INVOICE_TOTAL_MISMATCH,
                "invoice.declared_total",
                "Declared total differs from the sum of rounded line totals",
                _money_label(calculated_total),
                _money_label(invoice.declared_total),
            )
        )
    if purchase_order is None:
        issues.append(
            RuleIssue(
                IssueCode.MISSING_PURCHASE_ORDER,
                "purchase_order",
                "A purchase order is required for this check",
                invoice.purchase_order_id,
                None,
            )
        )
        return ValidationResult(invoice, purchase_order, resolved_policy, tuple(issues))

    if invoice.purchase_order_id != purchase_order.purchase_order_id:
        issues.append(
            RuleIssue(
                IssueCode.PURCHASE_ORDER_REFERENCE_MISMATCH,
                "invoice.purchase_order_id",
                "Invoice references a different purchase order",
                purchase_order.purchase_order_id,
                invoice.purchase_order_id,
            )
        )
    if invoice.supplier_id != purchase_order.supplier_id:
        issues.append(
            RuleIssue(
                IssueCode.SUPPLIER_MISMATCH,
                "invoice.supplier_id",
                "Invoice and purchase order suppliers differ",
                purchase_order.supplier_id,
                invoice.supplier_id,
            )
        )
    if invoice.currency != purchase_order.currency:
        issues.append(
            RuleIssue(
                IssueCode.CURRENCY_MISMATCH,
                "invoice.currency",
                "Cross-currency comparison is not supported",
                purchase_order.currency.value,
                invoice.currency.value,
            )
        )
    elif invoice.declared_total.amount > purchase_order.total.amount:
        # An invoice is not converted to another currency or netted with other invoices.
        issues.append(
            RuleIssue(
                IssueCode.PURCHASE_ORDER_TOTAL_EXCEEDED,
                "invoice.declared_total",
                "Invoice total exceeds the total of this purchase order",
                _money_label(purchase_order.total),
                _money_label(invoice.declared_total),
            )
        )
    return ValidationResult(invoice, purchase_order, resolved_policy, tuple(issues))
