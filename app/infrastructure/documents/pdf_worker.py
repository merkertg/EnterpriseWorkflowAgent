"""Standalone PDF text worker: PDF bytes on stdin, JSON on stdout.

The parent runs this file with ``python -I`` in a separate process and kills it
after a timeout. It imports nothing from the application, only pypdf, so it
works without the project on ``sys.path`` and ignores PYTHON* variables.

Before parsing, the worker limits its own CPU time and, where the operating
system allows it, its address space. Linux enforces RLIMIT_AS; macOS refuses
to set it, so there only the CPU and wall-clock limits apply. The answer
reports which limits are in force.
"""

import io
import json
import resource
import sys


def limit_resources(
    memory_bytes: int | None, cpu_seconds: int | None
) -> dict[str, bool]:
    """Apply the limits this platform accepts; report which ones hold."""
    applied = {"memory": False, "cpu": False}
    if cpu_seconds is not None:
        try:
            resource.setrlimit(resource.RLIMIT_CPU, (cpu_seconds, cpu_seconds + 1))
            applied["cpu"] = True
        except (ValueError, OSError):
            pass
    if memory_bytes is not None:
        try:
            resource.setrlimit(resource.RLIMIT_AS, (memory_bytes, memory_bytes))
            applied["memory"] = True
        except (ValueError, OSError):  # macOS: "current limit exceeds maximum"
            pass
    return applied


def read_pages(
    content: bytes, max_pages: int, max_text_chars: int | None = None
) -> dict[str, object]:
    """Return page texts or a rejection code matching RejectionCode values.

    Text is counted while pages are read, so a small file that expands into
    a huge text stops early instead of filling the parent's memory.
    """
    from pypdf import PdfReader

    try:
        reader = PdfReader(io.BytesIO(content), strict=False)
        if reader.is_encrypted:
            return {"status": "error", "code": "encrypted_pdf"}
        if len(reader.pages) > max_pages:
            return {"status": "error", "code": "too_many_pages"}
        pages: list[str] = []
        total = 0
        for page in reader.pages:
            text = page.extract_text() or ""
            total += len(text)
            if max_text_chars is not None and total > max_text_chars:
                return {"status": "error", "code": "text_too_long"}
            pages.append(text)
    except MemoryError:
        return {"status": "error", "code": "resource_limit"}
    except Exception:  # Any parser failure means the file cannot be trusted.
        return {"status": "error", "code": "malformed_pdf"}
    return {"status": "ok", "pages": pages}


def main() -> int:
    """Read limits from argv and the PDF from stdin; always print JSON.

    Arguments: max_pages [max_text_chars memory_bytes cpu_seconds].
    """
    numbers = [int(value) for value in sys.argv[1:5]]
    max_pages = numbers[0]
    max_text_chars = numbers[1] if len(numbers) > 1 else None
    memory_bytes = numbers[2] if len(numbers) > 2 else None
    cpu_seconds = numbers[3] if len(numbers) > 3 else None
    content = sys.stdin.buffer.read()
    applied = limit_resources(memory_bytes, cpu_seconds)
    result = read_pages(content, max_pages, max_text_chars)
    sys.stdout.write(json.dumps({**result, "limits": applied}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
