"""Stored documents keep bytes, page texts, fields and verifiable evidence."""

import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

import pytest
from sqlalchemy import Engine, text

from app.application.documents import DocumentService, IngestResult
from app.application.errors import NotFoundError, StoredDataIntegrityError
from app.application.workflows import WorkflowService
from app.demo import build_demo_documents
from app.domain.documents import DocumentRejectedError, MediaType, RejectionCode
from app.domain.extraction import ExtractionResult, ExtractionStatus, FieldStatus
from app.domain.rules import ValidationPolicy
from app.domain.workflow import WorkflowState
from app.samples import sample_files
from tests.persistence.conftest import TABLES, context

pytestmark = pytest.mark.postgres
SAMPLES = sample_files()


def _counts(engine: Engine) -> dict[str, int]:
    """Count rows in every table."""
    with engine.connect() as connection:
        return {
            table: connection.execute(
                text(f"SELECT count(*) FROM {table}")
            ).scalar_one()
            for table in TABLES
        }


def _ingest(service: DocumentService, name: str) -> IngestResult:
    """Ingest one named sample."""
    return service.ingest(SAMPLES[name], name)


def test_clean_pdf_is_stored_with_an_invoice_version(
    document_service: DocumentService,
) -> None:
    """Bytes, two pages, complete fields and invoice DEMO-INV-001 version 1."""
    result = _ingest(document_service, "clean_invoice.pdf")
    document, extraction = result.document, result.extraction
    assert (document.media_type, document.page_count) == (MediaType.PDF, 2)
    assert document.declared_name == "clean_invoice.pdf"
    assert extraction.status is ExtractionStatus.COMPLETE
    assert extraction.invoice_version is not None
    assert (
        extraction.invoice_version.business_id,
        extraction.invoice_version.version_no,
    ) == (
        "DEMO-INV-001",
        1,
    )
    assert (
        document_service.get_content(document.document_id)
        == SAMPLES["clean_invoice.pdf"]
    )
    assert document_service.get_extraction(document.document_id) == extraction


def test_evidence_opens_the_page_and_line_of_each_key_field(
    document_service: DocumentService,
) -> None:
    """Total, currency, invoice number and supplier each cite their source."""
    document_id = _ingest(document_service, "clean_invoice.pdf").document.document_id
    expected = {
        "declared_total": (2, 1, "30.00", "Total due: 30.00"),
        "currency": (1, 5, "USD", "Currency: USD"),
        "invoice_id": (1, 2, "DEMO-INV-001", "Invoice number: DEMO-INV-001"),
        "supplier_id": (1, 3, "DEMO-SUPPLIER-001", "Supplier ID: DEMO-SUPPLIER-001"),
    }
    for name, (page, line_number, quote, line) in expected.items():
        evidence = document_service.get_evidence(document_id, name)
        [citation] = evidence.citations
        assert (citation.page, citation.line_number) == (page, line_number)
        assert (citation.quote, citation.line) == (quote, line)
        assert evidence.field.value == quote
    with pytest.raises(NotFoundError):
        document_service.get_evidence(document_id, "approval")


def test_same_bytes_are_stored_once(
    engine: Engine, document_service: DocumentService
) -> None:
    """A repeated upload returns the stored document and extraction."""
    first = _ingest(document_service, "clean_invoice.txt")
    counts = _counts(engine)
    again = _ingest(document_service, "clean_invoice.txt")
    assert (again.reused_document, again.reused_extraction) == (True, True)
    assert again.document == first.document
    assert again.extraction == first.extraction
    assert _counts(engine) == counts


def test_text_and_pdf_of_one_invoice_share_its_version(
    document_service: DocumentService,
) -> None:
    """Different documents, identical invoice content, one invoice version."""
    pdf = _ingest(document_service, "clean_invoice.pdf")
    txt = _ingest(document_service, "clean_invoice.txt")
    assert pdf.document.document_id != txt.document.document_id
    assert pdf.extraction.invoice_version == txt.extraction.invoice_version


def test_incomplete_extraction_waits_for_review(
    engine: Engine, document_service: DocumentService
) -> None:
    """No invoice version is created; problems and missing fields are stored."""
    result = _ingest(document_service, "incomplete_invoice.txt")
    assert result.extraction.status is ExtractionStatus.NEEDS_REVIEW
    assert result.extraction.invoice_version is None
    assert [(p.code.value, p.field) for p in result.extraction.problems] == [
        ("missing_field", "currency"),
        ("invalid_value", "declared_total"),
    ]
    missing = document_service.get_evidence(result.document.document_id, "currency")
    assert (missing.field.status, missing.citations) == (FieldStatus.MISSING, ())
    total = document_service.get_evidence(result.document.document_id, "declared_total")
    assert total.citations[0].quote == "25,00"
    assert _counts(engine)["invoice_versions"] == 0


def test_rejected_input_writes_nothing(
    engine: Engine, document_service: DocumentService
) -> None:
    """Refusal happens before the transaction opens."""
    with pytest.raises(
        DocumentRejectedError, check=lambda e: e.code is RejectionCode.UNSUPPORTED_TYPE
    ):
        document_service.ingest(b"\x89PNG\r\n\x1a\nfictional", "picture.png")
    assert set(_counts(engine).values()) == {0}


def test_extracted_invoice_can_start_a_workflow(
    document_service: DocumentService, service: WorkflowService
) -> None:
    """Stage 03 commands accept the invoice version that Stage 04 produced."""
    invoice_version = _ingest(
        document_service, "clean_invoice.pdf"
    ).extraction.invoice_version
    assert invoice_version is not None
    _, order = build_demo_documents()
    order_version = service.record_purchase_order(order)
    policy_version = service.record_policy(ValidationPolicy("documents-test"))
    service.start(
        "WF-DOC-1",
        invoice_version_id=invoice_version.version_id,
        purchase_order_version_id=order_version.version_id,
        policy_version_id=policy_version.version_id,
        context=context("doc-start"),
    )
    checked = service.check(
        "WF-DOC-1", expected_version=1, context=context("doc-check")
    )
    assert checked.state is WorkflowState.AWAITING_APPROVAL


def test_edited_page_text_or_bytes_are_detected(
    engine: Engine, document_service: DocumentService
) -> None:
    """With triggers bypassed, edits still fail evidence and hash checks."""
    document_id = _ingest(document_service, "clean_invoice.txt").document.document_id
    with engine.begin() as connection:
        for table in ("document_pages", "source_documents"):
            connection.execute(
                text(f"ALTER TABLE {table} DISABLE TRIGGER trg_{table}_append_only")
            )
        connection.execute(
            text("UPDATE document_pages SET text = replace(text, '30.00', '99.00')")
        )
        # One statement: the size CHECK alone would already refuse a partial edit.
        connection.execute(
            text(
                "UPDATE source_documents SET content = content || '\\x0a'::bytea, "
                "size_bytes = size_bytes + 1"
            )
        )
        for table in ("document_pages", "source_documents"):
            connection.execute(
                text(f"ALTER TABLE {table} ENABLE TRIGGER trg_{table}_append_only")
            )
    with pytest.raises(StoredDataIntegrityError):
        document_service.get_evidence(document_id, "declared_total")
    with pytest.raises(StoredDataIntegrityError):
        document_service.get_content(document_id)


def test_evidence_always_refers_to_stored_pages(
    document_service: DocumentService,
) -> None:
    """If a later extraction differs, fields are recomputed from stored text."""
    first = document_service.prepare(SAMPLES["clean_invoice.txt"], "a.txt")
    document_service.store(first)
    shifted = replace(
        first,
        pages=("  " + first.pages[0],),
        extraction=ExtractionResult("labeled-text-parser/2", first.extraction.fields),
    )
    result = document_service.store(shifted)
    assert result.reused_document is True
    # The service parser re-read the stored pages; the shifted proposal was unused.
    assert result.extraction.result.extractor == "labeled-text-parser/1"


def test_concurrent_uploads_of_new_bytes_store_one_document(
    document_service: DocumentService,
) -> None:
    """Racing writers retry after the unique hash wins; all see one document."""
    prepared = document_service.prepare(SAMPLES["clean_invoice.txt"], "race.txt")
    barrier = threading.Barrier(3)

    def store() -> IngestResult:
        """Start all writers together to make the race likely."""
        barrier.wait()
        return document_service.store(prepared)

    with ThreadPoolExecutor(3) as pool:
        results = list(pool.map(lambda _: store(), range(3)))
    assert len({r.document.document_id for r in results}) == 1
    assert len({r.extraction.extraction_id for r in results}) == 1
    assert sum(not r.reused_extraction for r in results) == 1
