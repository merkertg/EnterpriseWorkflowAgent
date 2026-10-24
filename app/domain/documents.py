"""Intake rules for uploaded documents: size, format, encoding and page limits.

Format is detected from content, never from a file name. Document text is data:
nothing here interprets it as instructions.
"""

import math
from dataclasses import dataclass
from enum import StrEnum

from app.domain.errors import DomainValidationError

# Signatures of common binary formats that are definitely not supported text.
_BINARY_SIGNATURES = (
    b"\x89PNG",
    b"\xff\xd8\xff",
    b"GIF8",
    b"PK\x03\x04",
    b"\xd0\xcf\x11\xe0",
)
# Tab, line feed, form feed and carriage return are the only allowed controls.
_ALLOWED_CONTROLS = {"\t", "\n", "\f", "\r"}


class MediaType(StrEnum):
    """Supported formats; scanned PDFs without a text layer are not supported."""

    TEXT = "text/plain"
    PDF = "application/pdf"


class RejectionCode(StrEnum):
    """Stable reasons for refusing a document before anything is stored."""

    EMPTY_DOCUMENT = "empty_document"
    TOO_LARGE = "too_large"
    UNSUPPORTED_TYPE = "unsupported_type"
    INVALID_TEXT_ENCODING = "invalid_text_encoding"
    TOO_MANY_PAGES = "too_many_pages"
    TEXT_TOO_LONG = "text_too_long"
    ENCRYPTED_PDF = "encrypted_pdf"
    MALFORMED_PDF = "malformed_pdf"
    NO_TEXT_LAYER = "no_text_layer"
    PROCESSING_TIMEOUT = "processing_timeout"
    RESOURCE_LIMIT = "resource_limit"


class DocumentRejectedError(Exception):
    """A document that will not be stored, with a code a client can act on."""

    def __init__(self, code: RejectionCode, message: str) -> None:
        """Keep the message generic; it never echoes document content."""
        self.code = code
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class DocumentLimits:
    """Learning-project limits; every check happens before storage."""

    max_bytes: int = 1_048_576
    max_pages: int = 20
    max_text_chars: int = 200_000
    timeout_seconds: float = 5.0
    # Address space of the PDF worker; enforced where the OS supports RLIMIT_AS.
    worker_memory_bytes: int = 536_870_912

    def __post_init__(self) -> None:
        """Reject zero, negative and non-numeric limits."""
        for name in ("max_bytes", "max_pages", "max_text_chars", "worker_memory_bytes"):
            value = getattr(self, name)
            if type(value) is not int or value < 1:
                raise DomainValidationError(
                    "invalid_limit", name, f"{name} must be a positive integer."
                )
        if type(self.timeout_seconds) not in (int, float) or not (
            0 < self.timeout_seconds <= 60
        ):
            raise DomainValidationError(
                "invalid_limit", "timeout_seconds", "Use a timeout of 0–60 seconds."
            )

    @property
    def worker_cpu_seconds(self) -> int:
        """CPU time for the worker: the wall-clock timeout, rounded up."""
        return max(1, math.ceil(self.timeout_seconds))


def check_size(content: bytes, limits: DocumentLimits) -> None:
    """Refuse empty and oversized input before any parsing work."""
    if type(content) is not bytes:
        raise DomainValidationError("invalid_type", "content", "Content must be bytes.")
    if not content:
        raise DocumentRejectedError(
            RejectionCode.EMPTY_DOCUMENT, "The document is empty."
        )
    if len(content) > limits.max_bytes:
        raise DocumentRejectedError(
            RejectionCode.TOO_LARGE,
            f"The document exceeds the limit of {limits.max_bytes} bytes.",
        )


def normalize_text(text: str) -> str:
    """Use one newline convention so evidence offsets are stable."""
    return text.replace("\r\n", "\n").replace("\r", "\n")


def detect_media_type(content: bytes) -> MediaType:
    """Identify PDF by its header and text by strict UTF-8 without binary data."""
    if content.startswith(b"%PDF-"):
        return MediaType.PDF
    if content.startswith(_BINARY_SIGNATURES) or b"\x00" in content:
        raise DocumentRejectedError(
            RejectionCode.UNSUPPORTED_TYPE,
            "Only UTF-8 text and PDF files with a text layer are supported.",
        )
    return MediaType.TEXT


def decode_text_document(content: bytes) -> str:
    """Decode strict UTF-8, drop a byte-order mark and refuse control characters."""
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise DocumentRejectedError(
            RejectionCode.INVALID_TEXT_ENCODING, "Save the text file as UTF-8."
        ) from error
    if any(ord(char) < 32 and char not in _ALLOWED_CONTROLS for char in text):
        raise DocumentRejectedError(
            RejectionCode.UNSUPPORTED_TYPE,
            "The file contains binary control characters.",
        )
    return normalize_text(text)


def check_pages(
    pages: tuple[str, ...], media_type: MediaType, limits: DocumentLimits
) -> tuple[str, ...]:
    """Apply page, length and has-text limits to extracted page texts."""
    if len(pages) > limits.max_pages:
        raise DocumentRejectedError(
            RejectionCode.TOO_MANY_PAGES,
            f"The document has more than {limits.max_pages} pages.",
        )
    if sum(len(page) for page in pages) > limits.max_text_chars:
        raise DocumentRejectedError(
            RejectionCode.TEXT_TOO_LONG,
            f"Extracted text exceeds {limits.max_text_chars} characters.",
        )
    if not pages or not any(page.strip() for page in pages):
        if media_type is MediaType.PDF:
            raise DocumentRejectedError(
                RejectionCode.NO_TEXT_LAYER,
                "The PDF has no extractable text; scanned PDFs need OCR.",
            )
        raise DocumentRejectedError(
            RejectionCode.EMPTY_DOCUMENT, "The document contains no text."
        )
    return pages
