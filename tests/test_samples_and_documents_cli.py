"""Samples are reproducible; the CLI refuses bad files without a database."""

from pathlib import Path

import pytest

from app.application.documents import clean_declared_name, prepare_document
from app.documents_cli import main
from app.domain.documents import DocumentLimits
from app.domain.extraction import (
    ExtractedField,
    ExtractionResult,
    ExtractionStatus,
    FieldStatus,
)
from app.infrastructure.documents.text_extraction import LimitedTextExtractor
from app.infrastructure.extraction.labeled_text import LabeledTextInvoiceParser
from app.samples import (
    build_text_pdf,
    clean_invoice_pdf_pages,
    clean_invoice_text,
    sample_files,
    write_samples,
)


def test_sample_pdf_bytes_are_identical_on_every_build() -> None:
    """No timestamps or random IDs: the same input gives the same bytes."""
    first = build_text_pdf(clean_invoice_pdf_pages())
    assert first == build_text_pdf(clean_invoice_pdf_pages())
    assert first.startswith(b"%PDF-1.4") and first.rstrip().endswith(b"%%EOF")


def test_pdf_writer_escapes_and_refuses_non_ascii() -> None:
    """Parentheses are escaped; Cyrillic needs fonts this writer lacks."""
    assert b"(a\\(b\\)c\\\\) Tj" in build_text_pdf((("a(b)c\\",),))
    with pytest.raises(ValueError, match="ASCII"):
        build_text_pdf((("Счёт",),))


def test_write_samples_never_overwrites(tmp_path: Path) -> None:
    """A second write into the same directory stops at the first existing file."""
    paths = write_samples(tmp_path / "samples")
    assert sorted(path.name for path in paths) == sorted(sample_files())
    with pytest.raises(FileExistsError):
        write_samples(tmp_path / "samples")


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        (None, None),
        ("../../etc/passwd", "passwd"),
        ("C:\\Users\\demo\\invoice.pdf", "invoice.pdf"),
        ("  \x00\t ", None),
        ("a" * 300 + ".txt", "a" * 255),
    ],
)
def test_declared_names_are_reduced_to_short_labels(
    name: str | None, expected: str | None
) -> None:
    """Paths and control characters are dropped; the name is only a label."""
    assert clean_declared_name(name) == expected


class _FabricatingExtractor:
    """Stands in for a future model that proposes a value it cannot support."""

    name = "fabricating-model/test"

    def extract(self, pages: tuple[str, ...]) -> ExtractionResult:
        """Claim the total is 0.00 while citing the real 30.00 span."""
        honest = LabeledTextInvoiceParser().extract(pages)
        total = honest.field("declared_total")
        assert total is not None
        forged = ExtractedField(
            "declared_total", FieldStatus.FOUND, "0.00", total.evidence
        )
        return ExtractionResult(
            self.name,
            tuple(forged if f.name == "declared_total" else f for f in honest.fields),
        )


def test_fabricated_values_from_any_extractor_need_review() -> None:
    """Evidence verification applies to every extractor, not just the parser."""
    prepared = prepare_document(
        clean_invoice_text().encode(),
        "clean.txt",
        text_extractor=LimitedTextExtractor(),
        field_extractor=_FabricatingExtractor(),
        limits=DocumentLimits(),
    )
    assert prepared.assessment.status is ExtractionStatus.NEEDS_REVIEW
    assert [p.code.value for p in prepared.assessment.problems] == [
        "unverified_evidence"
    ]
    assert prepared.assessment.invoice is None


def test_cli_writes_samples(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """write-samples needs no database and prints the written paths."""
    assert main(["write-samples", str(tmp_path / "out")]) == 0
    assert "clean_invoice.pdf" in capsys.readouterr().out


def test_cli_refuses_bad_files_before_connecting(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Rejected input exits 1 with its code, even without DATABASE_URL."""
    picture = tmp_path / "picture.pdf"
    picture.write_bytes(b"\x89PNG\r\n\x1a\nfictional")
    assert main(["ingest", str(picture)]) == 1
    assert "rejected: unsupported_type" in capsys.readouterr().err

    large = tmp_path / "large.txt"
    large.write_bytes(b"a" * (DocumentLimits().max_bytes + 1))
    assert main(["ingest", str(large)]) == 1
    assert "rejected: too_large" in capsys.readouterr().err


def test_cli_needs_a_database_only_to_store(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """An accepted file without DATABASE_URL exits 2 with an explanation."""
    good = tmp_path / "clean.txt"
    good.write_text(clean_invoice_text(), encoding="utf-8")
    assert main(["ingest", str(good)]) == 2
    assert "DATABASE_URL" in capsys.readouterr().err
    assert main(["ingest", str(tmp_path / "missing.txt")]) == 1
