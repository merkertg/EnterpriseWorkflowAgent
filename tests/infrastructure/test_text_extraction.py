"""PDF text comes from an isolated worker that is limited in pages and time."""

import io
import sys
import time

import pytest
from pypdf import PdfReader, PdfWriter

from app.domain.documents import (
    DocumentLimits,
    DocumentRejectedError,
    MediaType,
    RejectionCode,
)
from app.infrastructure.documents.pdf_worker import read_pages
from app.infrastructure.documents.text_extraction import (
    LimitedTextExtractor,
    default_pdf_command,
)
from app.samples import build_text_pdf, clean_invoice_pdf_pages

LIMITS = DocumentLimits()


def _rejected(code: RejectionCode) -> pytest.RaisesExc[DocumentRejectedError]:
    """Expect a rejection with a specific code."""
    return pytest.raises(DocumentRejectedError, check=lambda e: e.code is code)


def test_sample_pdf_pages_contain_their_lines_in_order() -> None:
    """Two pages come back separately, each with its written lines."""
    pages = LimitedTextExtractor().extract_pages(
        build_text_pdf(clean_invoice_pdf_pages()), MediaType.PDF, LIMITS
    )
    assert [page.splitlines() for page in pages] == [
        list(lines) for lines in clean_invoice_pdf_pages()
    ]


def test_worker_runs_isolated_from_the_environment() -> None:
    """-I ignores PYTHON* variables and the user site directory."""
    command = default_pdf_command()
    assert command[0] == sys.executable and command[1] == "-I"


def test_page_limit_is_checked_inside_the_worker() -> None:
    """A two-page PDF fails a one-page limit before text is returned."""
    with _rejected(RejectionCode.TOO_MANY_PAGES):
        LimitedTextExtractor().extract_pages(
            build_text_pdf((("a",), ("b",))), MediaType.PDF, DocumentLimits(max_pages=1)
        )


def test_encrypted_malformed_and_textless_pdfs_are_refused() -> None:
    """Each failure mode has its own code."""
    writer = PdfWriter(clone_from=PdfReader(io.BytesIO(build_text_pdf((("a",),)))))
    writer.encrypt("fictional", algorithm="RC4-128")
    encrypted = io.BytesIO()
    writer.write(encrypted)
    extractor = LimitedTextExtractor()
    with _rejected(RejectionCode.ENCRYPTED_PDF):
        extractor.extract_pages(encrypted.getvalue(), MediaType.PDF, LIMITS)
    with _rejected(RejectionCode.MALFORMED_PDF):
        extractor.extract_pages(b"%PDF-1.4\nnot a pdf", MediaType.PDF, LIMITS)
    with _rejected(RejectionCode.NO_TEXT_LAYER):
        extractor.extract_pages(build_text_pdf(((),)), MediaType.PDF, LIMITS)


def test_slow_worker_is_stopped_at_the_timeout() -> None:
    """A worker that never answers is killed; the caller gets a clear code."""
    sleeper = LimitedTextExtractor(
        (sys.executable, "-c", "import time; time.sleep(30)")
    )
    started = time.monotonic()
    with _rejected(RejectionCode.PROCESSING_TIMEOUT):
        sleeper.extract_pages(
            b"%PDF-", MediaType.PDF, DocumentLimits(timeout_seconds=0.5)
        )
    assert time.monotonic() - started < 5


@pytest.mark.parametrize(
    "script",
    [
        "print('not json')",
        "print('[1, 2]')",
        'print(\'{"status": "error", "code": "made_up"}\')',
        'print(\'{"status": "ok", "pages": [1]}\')',
        "import sys; sys.exit(3)",
    ],
)
def test_unusable_worker_answers_mean_malformed(script: str) -> None:
    """The parent trusts only a well-formed answer from its own worker."""
    extractor = LimitedTextExtractor((sys.executable, "-c", script))
    with _rejected(RejectionCode.MALFORMED_PDF):
        extractor.extract_pages(b"%PDF-", MediaType.PDF, LIMITS)


def test_worker_function_reports_codes_directly() -> None:
    """The worker's own logic, called in-process for speed."""
    assert read_pages(b"garbage", 5) == {"status": "error", "code": "malformed_pdf"}
    answer = read_pages(build_text_pdf((("Total: 1.00",),)), 5)
    assert answer == {"status": "ok", "pages": ["Total: 1.00\n"]}


def test_text_documents_are_decoded_in_process() -> None:
    """UTF-8 text is one page with normalized newlines."""
    pages = LimitedTextExtractor().extract_pages(b"A\r\nB", MediaType.TEXT, LIMITS)
    assert pages == ("A\nB",)
