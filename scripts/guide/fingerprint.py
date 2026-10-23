"""Fingerprint of everything the PDF guide is built from.

The build stores this hash in the PDF metadata. A test recomputes it, so a
change to the generator, verification.json, application or interface
code, migrations or uv.lock without rebuilding both PDFs fails the test
suite. Standard library only: the test runs without ReportLab.
"""

import hashlib
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PREFIX = "source-sha256:"


def source_files(root: Path = PROJECT_ROOT) -> list[Path]:
    """List guide inputs in a stable order, skipping caches."""
    patterns = (
        "scripts/build_guide.py",
        "scripts/guide/*.py",
        "docs/guide/verification.json",
        "uv.lock",
        "app/**/*.py",
        "ui/**/*.py",
        "migrations/versions/*.py",
    )
    files = {
        path
        for pattern in patterns
        for path in root.glob(pattern)
        if path.is_file() and "__pycache__" not in path.parts
    }
    return sorted(files, key=lambda path: path.relative_to(root).as_posix())


def source_fingerprint(root: Path = PROJECT_ROOT) -> str:
    """Hash relative paths and contents, so renames also change the result."""
    digest = hashlib.sha256()
    for path in source_files(root):
        digest.update(path.relative_to(root).as_posix().encode("utf-8") + b"\0")
        digest.update(hashlib.sha256(path.read_bytes()).digest())
    return digest.hexdigest()
