"""Intake rules refuse empty, oversized, binary and badly encoded input."""

import pytest

from app.domain.documents import (
    DocumentLimits,
    DocumentRejectedError,
    MediaType,
    RejectionCode,
    check_pages,
    check_size,
    decode_text_document,
    detect_media_type,
)
from app.domain.errors import DomainValidationError

SMALL = DocumentLimits(max_bytes=10, max_pages=2, max_text_chars=20)


def _rejected(code: RejectionCode) -> pytest.RaisesExc[DocumentRejectedError]:
    """Expect a rejection and check its code after the block."""
    return pytest.raises(DocumentRejectedError, check=lambda e: e.code is code)


def test_size_limit_is_inclusive_and_empty_is_refused() -> None:
    """Exactly max_bytes passes; one byte more or zero bytes fail."""
    check_size(b"x" * 10, SMALL)
    with _rejected(RejectionCode.TOO_LARGE):
        check_size(b"x" * 11, SMALL)
    with _rejected(RejectionCode.EMPTY_DOCUMENT):
        check_size(b"", SMALL)
    with pytest.raises(DomainValidationError):
        check_size("text", SMALL)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "content",
    [
        b"\x89PNG\r\n\x1a\n",
        b"\xff\xd8\xff\xe0",
        b"GIF89a",
        b"PK\x03\x04docx",
        b"text\x00with nul",
    ],
)
def test_binary_formats_are_unsupported(content: bytes) -> None:
    """Pictures, office archives and NUL bytes are not text."""
    with _rejected(RejectionCode.UNSUPPORTED_TYPE):
        detect_media_type(content)


def test_format_comes_from_content() -> None:
    """A PDF header means PDF; anything else is checked as text."""
    assert detect_media_type(b"%PDF-1.4\n") is MediaType.PDF
    assert detect_media_type(b"Invoice number: 1") is MediaType.TEXT


def test_text_is_strict_utf8_with_normalized_newlines() -> None:
    """BOM is dropped and CRLF/CR become LF so offsets are stable."""
    assert decode_text_document(b"\xef\xbb\xbfA\r\nB\rC\tD\fE") == "A\nB\nC\tD\fE"
    assert decode_text_document("Счёт".encode()) == "Счёт"
    with _rejected(RejectionCode.INVALID_TEXT_ENCODING):
        decode_text_document("Счёт".encode("cp1251"))
    with _rejected(RejectionCode.UNSUPPORTED_TYPE):
        decode_text_document(b"bell\x07")


def test_page_limits_and_missing_text() -> None:
    """Page count, total length and the presence of any text are enforced."""
    assert check_pages(("a", "b"), MediaType.PDF, SMALL) == ("a", "b")
    with _rejected(RejectionCode.TOO_MANY_PAGES):
        check_pages(("a", "b", "c"), MediaType.PDF, SMALL)
    with _rejected(RejectionCode.TEXT_TOO_LONG):
        check_pages(("x" * 21,), MediaType.TEXT, SMALL)
    with _rejected(RejectionCode.NO_TEXT_LAYER):
        check_pages((" \n", ""), MediaType.PDF, SMALL)
    with _rejected(RejectionCode.EMPTY_DOCUMENT):
        check_pages((" \n",), MediaType.TEXT, SMALL)


@pytest.mark.parametrize(
    "arguments",
    [
        {"max_bytes": 0},
        {"max_pages": -1},
        {"max_text_chars": True},
        {"timeout_seconds": 0},
        {"timeout_seconds": 61},
    ],
)
def test_limits_must_be_positive_and_bounded(arguments: dict[str, object]) -> None:
    """A zero, negative, boolean or excessive limit is a configuration error."""
    with pytest.raises(DomainValidationError):
        DocumentLimits(**arguments)  # type: ignore[arg-type]
