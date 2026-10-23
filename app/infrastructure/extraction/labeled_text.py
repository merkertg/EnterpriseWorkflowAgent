"""Deterministic extractor for the fictional "Label: value" invoice text format.

Recognised rows look like ``Invoice number: DEMO-INV-001`` or
``Line: SKU | description | quantity | unit price``. Unknown labels and free
text are ignored: text inside a document is never treated as an instruction.
Every value records the exact character span it came from.
"""

import re
from dataclasses import dataclass

from app.domain.extraction import (
    HEADER_FIELDS,
    LINE_PARTS,
    Evidence,
    ExtractedField,
    ExtractionResult,
    FieldStatus,
    line_field,
)

EXTRACTOR_NAME = "labeled-text-parser/1"
_HEADER_LABELS = {
    "invoice number": "invoice_id",
    "invoice no": "invoice_id",
    "invoice id": "invoice_id",
    "supplier id": "supplier_id",
    "supplier": "supplier_id",
    "purchase order": "purchase_order_id",
    "purchase order id": "purchase_order_id",
    "po number": "purchase_order_id",
    "currency": "currency",
    "total due": "declared_total",
    "invoice total": "declared_total",
    "total": "declared_total",
}
_LINE_LABELS = {"line", "item"}
_LABELED_ROW = re.compile(r"[ \t]*(?P<label>[A-Za-z][A-Za-z .]*?)[ \t]*:(?P<rest>.*)")


@dataclass(frozen=True, slots=True)
class _Candidate:
    """A trimmed value and its span, or an empty value with its row span."""

    value: str
    evidence: Evidence


def _trimmed(page: int, start: int, raw: str, row: Evidence) -> _Candidate:
    """Strip surrounding spaces while keeping exact offsets of the value."""
    value = raw.strip()
    if not value:
        return _Candidate("", row)
    leading = len(raw) - len(raw.lstrip())
    first = start + leading
    return _Candidate(value, Evidence(page, first, first + len(value)))


def _header_field(name: str, candidates: list[_Candidate]) -> ExtractedField:
    """Decide found, missing, ambiguous or invalid from all labelled rows."""
    present = [item for item in candidates if item.value]
    if not candidates:
        return ExtractedField(name, FieldStatus.MISSING, None, ())
    if not present:
        return ExtractedField(
            name,
            FieldStatus.INVALID,
            None,
            tuple(item.evidence for item in candidates),
            "The label has no value.",
        )
    if len({item.value for item in present}) == 1:
        return ExtractedField(
            name,
            FieldStatus.FOUND,
            present[0].value,
            tuple(item.evidence for item in present),
        )
    return ExtractedField(
        name,
        FieldStatus.AMBIGUOUS,
        None,
        tuple(item.evidence for item in present),
        "Different values appear for the same field.",
    )


def _line_fields(
    index: int, page: int, start: int, raw: str, row: Evidence
) -> list[ExtractedField]:
    """Split a line row into four parts, each with its own span."""
    parts = raw.split("|")
    if len(parts) != len(LINE_PARTS):
        return [
            ExtractedField(
                line_field(index, part),
                FieldStatus.INVALID,
                None,
                (row,),
                "Expected: SKU | description | quantity | unit price.",
            )
            for part in LINE_PARTS
        ]
    fields: list[ExtractedField] = []
    offset = start
    for part, raw_part in zip(LINE_PARTS, parts, strict=True):
        candidate = _trimmed(page, offset, raw_part, row)
        offset += len(raw_part) + 1
        if candidate.value:
            fields.append(
                ExtractedField(
                    line_field(index, part),
                    FieldStatus.FOUND,
                    candidate.value,
                    (candidate.evidence,),
                )
            )
        else:
            fields.append(
                ExtractedField(
                    line_field(index, part),
                    FieldStatus.INVALID,
                    None,
                    (row,),
                    "This part of the line is empty.",
                )
            )
    return fields


class LabeledTextInvoiceParser:
    """Implements the FieldExtractor port without any model or network call."""

    name = EXTRACTOR_NAME

    def extract(self, pages: tuple[str, ...]) -> ExtractionResult:
        """Scan every page row by row and report each schema field once."""
        header: dict[str, list[_Candidate]] = {name: [] for name in HEADER_FIELDS}
        lines: list[ExtractedField] = []
        line_count = 0
        for page_number, text in enumerate(pages, start=1):
            row_start = 0
            for row in text.split("\n"):
                match = _LABELED_ROW.fullmatch(row)
                if match is not None and row.strip():
                    label = " ".join(match["label"].lower().split())
                    rest_start = row_start + match.start("rest")
                    row_span = Evidence(page_number, row_start, row_start + len(row))
                    if label in _LINE_LABELS:
                        line_count += 1
                        lines.extend(
                            _line_fields(
                                line_count,
                                page_number,
                                rest_start,
                                match["rest"],
                                row_span,
                            )
                        )
                    elif label in _HEADER_LABELS:
                        header[_HEADER_LABELS[label]].append(
                            _trimmed(page_number, rest_start, match["rest"], row_span)
                        )
                row_start += len(row) + 1
        return ExtractionResult(
            EXTRACTOR_NAME,
            (
                *(_header_field(name, header[name]) for name in HEADER_FIELDS),
                *lines,
            ),
        )
