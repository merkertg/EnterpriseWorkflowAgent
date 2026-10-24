"""Page text from UTF-8 files in-process and from PDFs in a separate process.

PDF parsing runs in a fresh ``python -I`` process with a hard timeout. A
hostile or broken PDF can then neither hang the caller nor keep running: the
child is killed when the limit is reached. The worker also caps its own CPU
time, its address space where the OS allows it, and the text it returns.
"""

import json
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path

from app.domain.documents import (
    DocumentLimits,
    DocumentRejectedError,
    MediaType,
    RejectionCode,
    check_pages,
    decode_text_document,
    normalize_text,
)

PDF_WORKER = Path(__file__).with_name("pdf_worker.py")
_PDF_MESSAGES = {
    RejectionCode.ENCRYPTED_PDF: "Encrypted PDFs are not supported.",
    RejectionCode.TOO_MANY_PAGES: "The PDF has too many pages.",
    RejectionCode.MALFORMED_PDF: "The PDF could not be read.",
    RejectionCode.TEXT_TOO_LONG: "The PDF's text exceeds the length limit.",
    RejectionCode.RESOURCE_LIMIT: "Reading the PDF exceeded the memory or CPU limit.",
}


def default_pdf_command() -> tuple[str, ...]:
    """Run the worker in isolated mode: no user site, no PYTHON* variables."""
    return (sys.executable, "-I", str(PDF_WORKER))


def _malformed() -> DocumentRejectedError:
    """The worker gave no usable answer, so the PDF is treated as unreadable."""
    return DocumentRejectedError(
        RejectionCode.MALFORMED_PDF, _PDF_MESSAGES[RejectionCode.MALFORMED_PDF]
    )


def _pdf_pages(
    command: Sequence[str], content: bytes, limits: DocumentLimits
) -> tuple[str, ...]:
    """Send PDF bytes to the worker and translate its JSON answer."""
    try:
        completed = subprocess.run(
            [
                *command,
                str(limits.max_pages),
                str(limits.max_text_chars),
                str(limits.worker_memory_bytes),
                str(limits.worker_cpu_seconds),
            ],
            input=content,
            capture_output=True,
            timeout=limits.timeout_seconds,
            check=False,
        )
    except subprocess.TimeoutExpired as error:
        raise DocumentRejectedError(
            RejectionCode.PROCESSING_TIMEOUT,
            f"Processing took longer than {limits.timeout_seconds} seconds.",
        ) from error
    if completed.returncode < 0:
        # Killed by a signal: SIGXCPU at the CPU limit, SIGKILL from the OS.
        raise DocumentRejectedError(
            RejectionCode.RESOURCE_LIMIT,
            _PDF_MESSAGES[RejectionCode.RESOURCE_LIMIT],
        )
    try:
        answer = json.loads(completed.stdout)
    except ValueError as error:
        raise _malformed() from error
    if not isinstance(answer, dict):
        raise _malformed()
    if answer.get("status") == "ok":
        pages = answer.get("pages")
        if not isinstance(pages, list) or not all(isinstance(p, str) for p in pages):
            raise _malformed()
        return tuple(normalize_text(page) for page in pages)
    try:
        code = RejectionCode(str(answer.get("code")))
    except ValueError as error:
        raise _malformed() from error
    raise DocumentRejectedError(
        code, _PDF_MESSAGES.get(code, "The PDF could not be processed.")
    )


class LimitedTextExtractor:
    """Extract page texts within DocumentLimits; implements the TextExtractor port."""

    def __init__(self, pdf_command: Sequence[str] | None = None) -> None:
        """Allow tests to substitute the worker command, e.g. a slow one."""
        self._pdf_command = tuple(pdf_command or default_pdf_command())

    def extract_pages(
        self, content: bytes, media_type: MediaType, limits: DocumentLimits
    ) -> tuple[str, ...]:
        """Return normalized page texts or raise DocumentRejectedError."""
        if media_type is MediaType.TEXT:
            pages: tuple[str, ...] = (decode_text_document(content),)
        else:
            pages = _pdf_pages(self._pdf_command, content, limits)
        return check_pages(pages, media_type, limits)
