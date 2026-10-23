"""The deterministic parser finds labelled values and cites exact spans."""

from app.demo import build_demo_documents
from app.domain.extraction import (
    HEADER_FIELDS,
    Evidence,
    ExtractionStatus,
    FieldStatus,
    assess_extraction,
)
from app.infrastructure.extraction.labeled_text import (
    EXTRACTOR_NAME,
    LabeledTextInvoiceParser,
)
from app.samples import (
    clean_invoice_pdf_pages,
    clean_invoice_text,
    instruction_invoice_text,
)

PARSER = LabeledTextInvoiceParser()


def test_spans_are_exact_character_offsets() -> None:
    """Hand-counted: the value after 'Invoice number: ' starts at offset 16."""
    pages = ("Invoice number: INV-1\n",)
    field = PARSER.extract(pages).field("invoice_id")
    assert field is not None
    assert (field.status, field.value) == (FieldStatus.FOUND, "INV-1")
    assert field.evidence == (Evidence(1, 16, 21),)


def test_clean_sample_yields_the_demo_invoice() -> None:
    """Independent expectation: the Stage 02 demo invoice built by hand."""
    pages = (clean_invoice_text(),)
    assessment = assess_extraction(PARSER.extract(pages), pages)
    assert assessment.status is ExtractionStatus.COMPLETE
    assert assessment.invoice == build_demo_documents()[0]


def test_evidence_records_the_page_of_each_value() -> None:
    """The sample PDF layout puts the total alone on page 2."""
    pages = tuple("\n".join(lines) + "\n" for lines in clean_invoice_pdf_pages())
    result = PARSER.extract(pages)
    total = result.field("declared_total")
    currency = result.field("currency")
    assert total is not None and currency is not None
    assert [e.page for e in total.evidence] == [2]
    assert [e.page for e in currency.evidence] == [1]
    assert result.extractor == EXTRACTOR_NAME


def test_labels_are_case_and_space_insensitive_with_synonyms() -> None:
    """'INVOICE  NO :' and 'PO Number:' map to the schema fields."""
    pages = ("  INVOICE  NO : INV-9\nPO Number:PO-9\n",)
    result = PARSER.extract(pages)
    assert [(f.name, f.value) for f in result.fields if f.value] == [
        ("invoice_id", "INV-9"),
        ("purchase_order_id", "PO-9"),
    ]


def test_repeated_labels_are_found_or_ambiguous() -> None:
    """Same value twice cites both rows; different values are ambiguous."""
    same = PARSER.extract(("Currency: USD\nCurrency: USD\n",)).field("currency")
    assert same is not None
    assert same.status is FieldStatus.FOUND and len(same.evidence) == 2
    differ = PARSER.extract(("Total: 1.00\nTotal due: 2.00\n",)).field("declared_total")
    assert differ is not None
    assert (differ.status, differ.value) == (FieldStatus.AMBIGUOUS, None)
    assert [e.page for e in differ.evidence] == [1, 1]


def test_empty_values_and_malformed_lines_are_invalid() -> None:
    """A bare label, a three-part line and an empty part are not guessed."""
    result = PARSER.extract(
        ("Supplier ID:   \nLine: A | B | 1\nLine: C |  | 1 | 2.00\n",)
    )
    statuses = {f.name: f.status for f in result.fields}
    assert statuses["supplier_id"] is FieldStatus.INVALID
    assert {statuses[f"lines[1].{p}"] for p in ("sku", "unit_price")} == {
        FieldStatus.INVALID
    }
    assert statuses["lines[2].sku"] is FieldStatus.FOUND
    assert statuses["lines[2].description"] is FieldStatus.INVALID


def test_missing_labels_are_reported_as_missing() -> None:
    """Every header field appears in the result, found or not."""
    result = PARSER.extract(("Nothing useful here.\n",))
    assert [f.name for f in result.fields] == list(HEADER_FIELDS)
    assert {f.status for f in result.fields} == {FieldStatus.MISSING}


def test_instructions_inside_a_document_are_only_text() -> None:
    """'SYSTEM NOTE' and 'Approval' rows are unknown labels and change nothing."""
    pages = (instruction_invoice_text(),)
    result = PARSER.extract(pages)
    names = {field.name for field in result.fields}
    assert names == {
        *HEADER_FIELDS,
        "lines[1].sku",
        "lines[1].description",
        "lines[1].quantity",
        "lines[1].unit_price",
    }
    assert all("approve" not in (f.value or "").lower() for f in result.fields)
    assessment = assess_extraction(result, pages)
    assert assessment.status is ExtractionStatus.COMPLETE
    assert assessment.invoice is not None
    assert assessment.invoice.invoice_id == "DEMO-INV-003"


def test_extraction_is_deterministic() -> None:
    """The same pages always produce an equal result."""
    pages = (clean_invoice_text(),)
    assert PARSER.extract(pages) == PARSER.extract(pages)
