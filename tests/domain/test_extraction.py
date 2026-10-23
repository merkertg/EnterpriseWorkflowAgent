"""The field schema decides completeness and never trusts unverified evidence."""

from collections.abc import Callable
from decimal import Decimal

import pytest

from app.domain.errors import DomainValidationError
from app.domain.extraction import (
    Evidence,
    ExtractedField,
    ExtractionResult,
    ExtractionStatus,
    FieldStatus,
    assess_extraction,
)
from app.domain.money import Currency

DEFAULTS = {
    "invoice_id": "INV-1",
    "supplier_id": "SUP-1",
    "purchase_order_id": "PO-1",
    "currency": "USD",
    "lines[1].sku": "SKU-1",
    "lines[1].description": "Fictional item",
    "lines[1].quantity": "2",
    "lines[1].unit_price": "12.50",
    "declared_total": "25.00",
}
# (prefix, field) in page order; None starts a new row.
LAYOUT = (
    ("Invoice number: ", "invoice_id"),
    None,
    ("Supplier: ", "supplier_id"),
    None,
    ("PO: ", "purchase_order_id"),
    None,
    ("Currency: ", "currency"),
    None,
    ("Line: ", "lines[1].sku"),
    (" | ", "lines[1].description"),
    (" | ", "lines[1].quantity"),
    (" | ", "lines[1].unit_price"),
    None,
    ("Total: ", "declared_total"),
    None,
)


def build_page(
    overrides: dict[str, str] | None = None,
) -> tuple[str, dict[str, Evidence]]:
    """Assemble a page and record the span of every value as it is written."""
    values = {**DEFAULTS, **(overrides or {})}
    text = ""
    spans: dict[str, Evidence] = {}
    for item in LAYOUT:
        if item is None:
            text += "\n"
            continue
        prefix, name = item
        text += prefix
        spans[name] = Evidence(1, len(text), len(text) + len(values[name]))
        text += values[name]
    return text, spans


def result_for(
    page: str, spans: dict[str, Evidence], *replace: ExtractedField
) -> ExtractionResult:
    """Every schema field found at its span, except the given replacements."""
    replaced = {item.name: item for item in replace}
    fields = [
        replaced[name]
        if name in replaced
        else ExtractedField(name, FieldStatus.FOUND, page[e.start : e.end], (e,))
        for name, e in spans.items()
    ]
    extra = [item for name, item in replaced.items() if name not in spans]
    return ExtractionResult("test-extractor/1", (*fields, *extra))


def codes(result: ExtractionResult, page: str) -> list[tuple[str, str]]:
    """Assess and list (code, field) pairs in reported order."""
    return [
        (problem.code.value, problem.field)
        for problem in assess_extraction(result, (page,)).problems
    ]


def test_builder_offsets_match_hand_counted_positions() -> None:
    """Independent anchor: offsets counted by hand for the default page."""
    page, spans = build_page()
    assert (spans["invoice_id"].start, spans["invoice_id"].end) == (16, 21)
    assert (spans["currency"].start, spans["currency"].end) == (57, 60)
    assert (spans["declared_total"].start, spans["declared_total"].end) == (109, 114)
    assert page[109:114] == "25.00"


def test_complete_extraction_builds_the_invoice() -> None:
    """Every field found and verified: an Invoice with exact Decimal values."""
    page, spans = build_page()
    assessment = assess_extraction(result_for(page, spans), (page,))
    assert assessment.status is ExtractionStatus.COMPLETE
    assert assessment.problems == ()
    invoice = assessment.invoice
    assert invoice is not None
    assert (invoice.invoice_id, invoice.currency) == ("INV-1", Currency.USD)
    assert invoice.lines[0].quantity == Decimal("2.000000")
    assert invoice.declared_total.amount == Decimal("25.00")


def test_value_not_at_its_cited_span_is_unverified() -> None:
    """A proposal of 0.00 that cites the text 25.00 is not trusted."""
    page, spans = build_page()
    fabricated = ExtractedField(
        "declared_total", FieldStatus.FOUND, "0.00", (spans["declared_total"],)
    )
    assessment = assess_extraction(result_for(page, spans, fabricated), (page,))
    assert assessment.status is ExtractionStatus.NEEDS_REVIEW
    assert assessment.invoice is None
    assert codes(result_for(page, spans, fabricated), page) == [
        ("unverified_evidence", "declared_total")
    ]


def test_evidence_outside_the_document_is_unverified() -> None:
    """Spans on a missing page cannot be quoted, so they verify nothing."""
    page, spans = build_page()
    outside = ExtractedField("currency", FieldStatus.FOUND, "USD", (Evidence(2, 0, 3),))
    assert codes(result_for(page, spans, outside), page) == [
        ("unverified_evidence", "currency")
    ]


def test_missing_ambiguous_and_invalid_fields_are_all_reported() -> None:
    """Each non-found status has its own code, and every problem is listed."""
    page, spans = build_page()
    result = result_for(
        page,
        spans,
        ExtractedField("currency", FieldStatus.MISSING, None, ()),
        ExtractedField(
            "supplier_id",
            FieldStatus.AMBIGUOUS,
            None,
            (spans["supplier_id"], spans["purchase_order_id"]),
        ),
        ExtractedField(
            "invoice_id", FieldStatus.INVALID, None, (Evidence(1, 0, 21),), "Empty"
        ),
    )
    assert sorted(codes(result, page)) == [
        ("ambiguous_field", "supplier_id"),
        ("invalid_value", "invoice_id"),
        ("missing_field", "currency"),
    ]
    without_order = ExtractionResult(
        "x/1",
        tuple(
            f for f in result_for(page, spans).fields if f.name != "purchase_order_id"
        ),
    )
    assert codes(without_order, page) == [("missing_field", "purchase_order_id")]


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("declared_total", "25,00"),
        ("declared_total", "$25.00"),
        ("lines[1].quantity", "1e3"),
        ("lines[1].unit_price", "12.5"),
        ("currency", "GBP"),
    ],
)
def test_noncanonical_values_are_invalid_not_guessed(name: str, value: str) -> None:
    """Commas, symbols, exponents, one decimal and unknown currencies fail."""
    page, spans = build_page({name: value})
    assert codes(result_for(page, spans), page) == [("invalid_value", name)]


def test_schema_rejects_unknown_fields_and_bad_line_numbers() -> None:
    """Strict schema: extra names and gaps in line numbering need review."""
    page, spans = build_page()
    extra = ExtractedField(
        "approval", FieldStatus.FOUND, "INV-1", (spans["invoice_id"],)
    )
    assert codes(result_for(page, spans, extra), page) == [
        ("unexpected_field", "approval")
    ]
    headers = tuple(f for f in result_for(page, spans).fields if "lines" not in f.name)
    assert codes(ExtractionResult("x/1", headers), page) == [("no_line_items", "lines")]
    shifted = tuple(
        ExtractedField(f.name.replace("[1]", "[2]"), f.status, f.value, f.evidence)
        for f in result_for(page, spans).fields
    )
    assert ("invalid_value", "lines") in codes(ExtractionResult("x/1", shifted), page)


def test_invoice_rules_still_apply_after_extraction() -> None:
    """A zero quantity is well formed text but breaks the LineItem rule."""
    page, spans = build_page({"lines[1].quantity": "0"})
    assert codes(result_for(page, spans), page) == [("invalid_invoice", "quantity")]


INCONSISTENT: list[Callable[[], object]] = [
    lambda: ExtractedField("a", FieldStatus.FOUND, "v", ()),
    lambda: ExtractedField("a", FieldStatus.FOUND, None, (Evidence(1, 0, 1),)),
    lambda: ExtractedField("a", FieldStatus.MISSING, "v", ()),
    lambda: ExtractedField("a", FieldStatus.AMBIGUOUS, None, (Evidence(1, 0, 1),)),
    lambda: ExtractedField("a", FieldStatus.FOUND, "v" * 501, (Evidence(1, 0, 1),)),
    lambda: Evidence(0, 0, 1),
    lambda: Evidence(1, 5, 5),
    lambda: Evidence(1, True, 2),
    lambda: ExtractionResult(
        "x/1",
        (
            ExtractedField("a", FieldStatus.MISSING, None, ()),
            ExtractedField("a", FieldStatus.MISSING, None, ()),
        ),
    ),
]


@pytest.mark.parametrize("build", INCONSISTENT)
def test_inconsistent_proposals_cannot_be_constructed(
    build: Callable[[], object],
) -> None:
    """Found needs value and evidence; spans are positive; names are unique."""
    with pytest.raises(DomainValidationError):
        build()
