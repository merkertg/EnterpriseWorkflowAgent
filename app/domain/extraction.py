"""Strict invoice field schema, evidence verification and completeness rules.

Any extractor, deterministic now or a model later, only proposes fields. This
module decides whether the proposal is complete, verifies that each found value
literally appears at its cited location and builds an Invoice only when nothing
is missing, ambiguous or invalid.
"""

import re
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

from app.domain.errors import DomainValidationError
from app.domain.models import Invoice, LineItem, validate_text
from app.domain.money import Currency, Money

HEADER_FIELDS = (
    "invoice_id",
    "supplier_id",
    "purchase_order_id",
    "currency",
    "declared_total",
)
LINE_PARTS = ("sku", "description", "quantity", "unit_price")
_LINE_FIELD = re.compile(r"lines\[(?P<index>[1-9][0-9]{0,2})\]\.(?P<part>[a-z_]+)")
# Canonical text forms; "30,00", "$30" or "1e3" are invalid, never guessed.
_MONEY = re.compile(r"[0-9]{1,12}\.[0-9]{2}")
_QUANTITY = re.compile(r"[0-9]{1,6}(?:\.[0-9]{1,6})?")
_MAX_VALUE_LENGTH = 500


def line_field(index: int, part: str) -> str:
    """Name one part of a 1-based invoice line, for example lines[1].sku."""
    return f"lines[{index}].{part}"


class FieldStatus(StrEnum):
    """Whether an extractor located a single value for a field."""

    FOUND = "found"
    MISSING = "missing"
    AMBIGUOUS = "ambiguous"
    INVALID = "invalid"


@dataclass(frozen=True, slots=True)
class Evidence:
    """A character span [start, end) on a 1-based page of stored document text."""

    page: int
    start: int
    end: int

    def __post_init__(self) -> None:
        """Require integer coordinates that describe a nonempty span."""
        if any(type(value) is not int for value in (self.page, self.start, self.end)):
            raise DomainValidationError(
                "invalid_type", "evidence", "Evidence coordinates must be integers."
            )
        if self.page < 1 or self.start < 0 or self.end <= self.start:
            raise DomainValidationError(
                "invalid_evidence", "evidence", "Use page >= 1 and 0 <= start < end."
            )

    def quote(self, pages: tuple[str, ...]) -> str | None:
        """Return the cited text, or None when the span lies outside the document."""
        if self.page > len(pages) or self.end > len(pages[self.page - 1]):
            return None
        return pages[self.page - 1][self.start : self.end]


@dataclass(frozen=True, slots=True)
class ExtractedField:
    """One proposed field value with the spans that support it."""

    name: str
    status: FieldStatus
    value: str | None
    evidence: tuple[Evidence, ...]
    message: str | None = None

    def __post_init__(self) -> None:
        """Keep status, value and evidence consistent with each other."""
        object.__setattr__(self, "name", validate_text(self.name, "name", 64))
        if not isinstance(self.status, FieldStatus):
            raise DomainValidationError("invalid_type", "status", "Use FieldStatus.")
        if type(self.evidence) is not tuple or any(
            type(item) is not Evidence for item in self.evidence
        ):
            raise DomainValidationError(
                "invalid_type", "evidence", "Evidence must be a tuple of Evidence."
            )
        if self.value is not None and (
            type(self.value) is not str or len(self.value) > _MAX_VALUE_LENGTH
        ):
            raise DomainValidationError(
                "invalid_value", "value", "Use a string of at most 500 characters."
            )
        if self.status is FieldStatus.FOUND and (not self.value or not self.evidence):
            raise DomainValidationError(
                "invalid_field", self.name, "A found field needs a value and evidence."
            )
        if self.status is FieldStatus.MISSING and (self.value or self.evidence):
            raise DomainValidationError(
                "invalid_field", self.name, "A missing field has no value or evidence."
            )
        if self.status is FieldStatus.AMBIGUOUS and len(self.evidence) < 2:
            raise DomainValidationError(
                "invalid_field", self.name, "An ambiguous field cites each candidate."
            )


@dataclass(frozen=True, slots=True)
class ExtractionResult:
    """All fields proposed by one named and versioned extractor."""

    extractor: str
    fields: tuple[ExtractedField, ...]

    def __post_init__(self) -> None:
        """Require an extractor label and unique field names."""
        object.__setattr__(
            self, "extractor", validate_text(self.extractor, "extractor", 64)
        )
        if type(self.fields) is not tuple or any(
            type(item) is not ExtractedField for item in self.fields
        ):
            raise DomainValidationError(
                "invalid_type", "fields", "Fields must be a tuple of ExtractedField."
            )
        names = [item.name for item in self.fields]
        if len(set(names)) != len(names):
            raise DomainValidationError(
                "duplicate_field", "fields", "Each field name may appear once."
            )

    def field(self, name: str) -> ExtractedField | None:
        """Return a field by name, if the extractor reported it."""
        return next((item for item in self.fields if item.name == name), None)


class ExtractionStatus(StrEnum):
    """Complete extractions yield an invoice; others wait for a person."""

    COMPLETE = "complete"
    NEEDS_REVIEW = "needs_review"


class ProblemCode(StrEnum):
    """Reasons an extraction cannot become an invoice automatically."""

    MISSING_FIELD = "missing_field"
    AMBIGUOUS_FIELD = "ambiguous_field"
    INVALID_VALUE = "invalid_value"
    UNVERIFIED_EVIDENCE = "unverified_evidence"
    UNEXPECTED_FIELD = "unexpected_field"
    NO_LINE_ITEMS = "no_line_items"
    INVALID_INVOICE = "invalid_invoice"


@dataclass(frozen=True, slots=True)
class ExtractionProblem:
    """One machine-readable reason, the field it concerns and an explanation."""

    code: ProblemCode
    field: str
    message: str


@dataclass(frozen=True, slots=True)
class Assessment:
    """The decision on an extraction; invoice is set only when it is complete."""

    status: ExtractionStatus
    problems: tuple[ExtractionProblem, ...]
    invoice: Invoice | None


def _line_indexes(result: ExtractionResult) -> tuple[list[int], list[str]]:
    """Collect line numbers and names that match neither header nor line schema."""
    indexes: set[int] = set()
    unexpected: list[str] = []
    for item in result.fields:
        match = _LINE_FIELD.fullmatch(item.name)
        if match is not None and match["part"] in LINE_PARTS:
            indexes.add(int(match["index"]))
        elif item.name not in HEADER_FIELDS:
            unexpected.append(item.name)
    return sorted(indexes), unexpected


def _field_problems(
    result: ExtractionResult, required: list[str]
) -> list[ExtractionProblem]:
    """Report absent, missing, ambiguous, invalid and badly formatted values."""
    problems: list[ExtractionProblem] = []
    for name in required:
        item = result.field(name)
        if item is None or item.status is FieldStatus.MISSING:
            problems.append(
                ExtractionProblem(ProblemCode.MISSING_FIELD, name, "Value not found.")
            )
        elif item.status is FieldStatus.AMBIGUOUS:
            problems.append(
                ExtractionProblem(
                    ProblemCode.AMBIGUOUS_FIELD, name, "Several different values found."
                )
            )
        elif item.status is FieldStatus.INVALID:
            problems.append(
                ExtractionProblem(
                    ProblemCode.INVALID_VALUE, name, item.message or "Unreadable value."
                )
            )
        elif (format_error := _format_error(name, item.value or "")) is not None:
            problems.append(
                ExtractionProblem(ProblemCode.INVALID_VALUE, name, format_error)
            )
    return problems


def _format_error(name: str, value: str) -> str | None:
    """Check canonical text forms; values are never reinterpreted or guessed."""
    if name == "currency" and value not in {item.value for item in Currency}:
        return "Use USD, EUR or UZS."
    if (name == "declared_total" or name.endswith(".unit_price")) and not (
        _MONEY.fullmatch(value)
    ):
        return "Use digits with a dot and two decimals, for example 30.00."
    if name.endswith(".quantity") and not _QUANTITY.fullmatch(value):
        return "Use a plain decimal quantity, for example 2 or 0.5."
    return None


def assess_extraction(result: ExtractionResult, pages: tuple[str, ...]) -> Assessment:
    """Verify evidence, check completeness, then build an Invoice or explain why not.

    A found value is trusted only if the text at each cited span equals it
    exactly. This applies equally to a deterministic parser and a future model.
    All problems are reported together, so a reviewer sees the full list.
    """
    if type(result) is not ExtractionResult:
        raise DomainValidationError("invalid_type", "result", "Use ExtractionResult.")
    indexes, unexpected = _line_indexes(result)
    problems = [
        ExtractionProblem(ProblemCode.UNEXPECTED_FIELD, name, "Not in the schema.")
        for name in unexpected
    ]
    for item in result.fields:
        if item.status is FieldStatus.FOUND and any(
            evidence.quote(pages) != item.value for evidence in item.evidence
        ):
            problems.append(
                ExtractionProblem(
                    ProblemCode.UNVERIFIED_EVIDENCE,
                    item.name,
                    "The cited text does not match the value.",
                )
            )
    if not indexes:
        problems.append(
            ExtractionProblem(ProblemCode.NO_LINE_ITEMS, "lines", "No line items.")
        )
    elif indexes != list(range(1, len(indexes) + 1)):
        problems.append(
            ExtractionProblem(
                ProblemCode.INVALID_VALUE, "lines", "Line numbers must be 1, 2, 3..."
            )
        )
    required = [
        *HEADER_FIELDS,
        *(line_field(index, part) for index in indexes for part in LINE_PARTS),
    ]
    problems.extend(_field_problems(result, required))
    if problems:
        return Assessment(ExtractionStatus.NEEDS_REVIEW, tuple(problems), None)

    values = {item.name: item.value or "" for item in result.fields}
    currency = Currency(values["currency"])
    try:
        invoice = Invoice(
            invoice_id=values["invoice_id"],
            supplier_id=values["supplier_id"],
            purchase_order_id=values["purchase_order_id"],
            currency=currency,
            lines=tuple(
                LineItem(
                    sku=values[line_field(index, "sku")],
                    description=values[line_field(index, "description")],
                    quantity=Decimal(values[line_field(index, "quantity")]),
                    unit_price=Money(
                        Decimal(values[line_field(index, "unit_price")]), currency
                    ),
                )
                for index in indexes
            ),
            declared_total=Money(Decimal(values["declared_total"]), currency),
        )
    except DomainValidationError as error:
        # Well-formed values can still break invoice rules, e.g. a repeated SKU.
        problem = ExtractionProblem(
            ProblemCode.INVALID_INVOICE, error.field, str(error)
        )
        return Assessment(ExtractionStatus.NEEDS_REVIEW, (problem,), None)
    return Assessment(ExtractionStatus.COMPLETE, (), invoice)
