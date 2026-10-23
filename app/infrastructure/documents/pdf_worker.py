"""Standalone PDF text worker: PDF bytes on stdin, JSON on stdout.

The parent runs this file with ``python -I`` in a separate process and kills it
after a timeout. It imports nothing from the application, only pypdf, so it
works without the project on ``sys.path`` and ignores PYTHON* variables.
"""

import io
import json
import sys


def read_pages(content: bytes, max_pages: int) -> dict[str, object]:
    """Return page texts or a rejection code matching RejectionCode values."""
    from pypdf import PdfReader

    try:
        reader = PdfReader(io.BytesIO(content), strict=False)
        if reader.is_encrypted:
            return {"status": "error", "code": "encrypted_pdf"}
        if len(reader.pages) > max_pages:
            return {"status": "error", "code": "too_many_pages"}
        pages = [page.extract_text() or "" for page in reader.pages]
    except Exception:  # Any parser failure means the file cannot be trusted.
        return {"status": "error", "code": "malformed_pdf"}
    return {"status": "ok", "pages": pages}


def main() -> int:
    """Read the page limit from argv and the PDF from stdin; always print JSON."""
    max_pages = int(sys.argv[1])
    result = read_pages(sys.stdin.buffer.read(), max_pages)
    sys.stdout.write(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
