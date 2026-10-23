# Document intake and field provenance

[README](../../README.md) · [Detailed Russian guide](../STAGE_04.md) ·
[ADR 0004](../adr/0004-document-intake-and-field-provenance.md) ·
[Verification](../VERIFICATION.md)

Stage 04 accepts UTF-8 text and PDFs with a text layer, extracts invoice
fields with a deterministic parser and records the exact source span of every
value. No HTTP endpoint is added; uploads over HTTP arrive with authorization
in Stage 06.

## Commands

```bash
uv run --locked python -m app.documents_cli write-samples var/samples
uv run --locked python -m app.documents_cli ingest var/samples/clean_invoice.pdf
uv run --locked python -m app.documents_cli show DOCUMENT_ID
uv run --locked python -m app.documents_cli evidence DOCUMENT_ID declared_total
uv run --locked python -m app.documents_cli demo
```

`write-samples` needs no database and never overwrites files. `ingest` checks
and extracts before connecting, so a refused file is reported without
`DATABASE_URL`. `show`, `evidence`, `demo` and storing need `DATABASE_URL` and
`uv run --locked alembic upgrade head` (revision `0002`). Exit codes: `0`
success, `1` rejected file or command error, `2` missing or invalid
`DATABASE_URL`. `var/` is ignored by Git.

## Intake rules

Format is detected from bytes: `%PDF-` means PDF; otherwise strict UTF-8 text
without NUL or other control characters (tab, LF, FF and CR are allowed). The
file name is kept only as a sanitized label of at most 255 characters.

| `DocumentLimits` field | Default |
| --- | --- |
| `max_bytes` | 1 048 576 |
| `max_pages` | 20 |
| `max_text_chars` | 200 000 |
| `timeout_seconds` | 5.0 (PDF parsing) |

Rejection codes: `empty_document`, `too_large`, `unsupported_type`,
`invalid_text_encoding`, `too_many_pages`, `text_too_long`, `encrypted_pdf`,
`malformed_pdf`, `no_text_layer`, `processing_timeout`. Rejected files are not
stored.

PDFs are parsed by `app/infrastructure/documents/pdf_worker.py`, run as
`python -I` in a separate process that receives bytes on stdin and returns JSON
on stdout. It is killed at the timeout; any unexpected answer is treated as
`malformed_pdf`. Memory is not limited yet.

## Field schema and assessment

Fields: `invoice_id`, `supplier_id`, `purchase_order_id`, `currency`,
`declared_total` and, per line `n`, `lines[n].sku`, `lines[n].description`,
`lines[n].quantity`, `lines[n].unit_price`. Each field is `found`, `missing`,
`ambiguous` or `invalid` and cites zero or more spans
`Evidence(page, start, end)` into the stored page text (1-based pages,
0-based offsets, `end` exclusive).

`assess_extraction()` treats every extractor as a proposer:

- each found value must equal the text at every cited span, otherwise
  `unverified_evidence`;
- unknown names give `unexpected_field`; no lines give `no_line_items`;
- `missing`/`ambiguous`/`invalid` give `missing_field`/`ambiguous_field`/`invalid_value`;
- money must be `digits.dd`, quantity a plain decimal, currency `USD`/`EUR`/`UZS`;
  `30,00`, `$30.00`, `1e3`, `12.5` and `GBP` are `invalid_value`;
- domain rule failures (zero quantity, repeated SKU) give `invalid_invoice`.

All problems are reported together. `complete` yields an `Invoice` and an
invoice version (Stage 03), which can start a workflow. `needs_review` yields
no invoice.

## Deterministic parser

`LabeledTextInvoiceParser` (`labeled-text-parser/1`) reads `Label: value` rows.
Labels are case- and space-insensitive with synonyms (`Invoice number/no/id`,
`Supplier ID`/`Supplier`, `Purchase order`/`PO number`,
`Total due`/`Invoice total`/`Total`, `Line`/`Item`). A line row is
`SKU | description | quantity | unit price`. A repeated identical value is found
with several citations; different values are ambiguous. Unknown labels and
free text, including instruction-like sentences, are ignored.

## Storage

Revision `0002` adds append-only `source_documents` (bytes, SHA-256, format,
size, pages, name label), `document_pages`, `document_extractions` and
`extracted_fields`. Identical bytes are stored once and return the existing
extraction; concurrent uploads converge on one document. Document, pages,
extraction and invoice version commit in one transaction. Reads verify the
byte hash; opening evidence re-checks the quote against the value. If a
re-upload extracts different text, fields are recomputed from the stored pages.

## Python use

```python
from app.infrastructure.db.session import build_document_service, create_database_engine

service = build_document_service(create_database_engine(database_url))
result = service.ingest(content_bytes, "invoice.pdf")
evidence = service.get_evidence(result.document.document_id, "declared_total")
```

`service.prepare()` (or `prepare_document()`) runs every check without a
database; `service.store(prepared)` writes it.

Stage 05 reconciliation cites these evidence spans for each discrepancy; see
[reconciliation](reconciliation.md).

## Limits

Text-layer PDFs and the training label format only; no OCR; ASCII-only sample
PDF writer; no manual correction of `needs_review` fields yet; no HTTP upload
until Stage 06; the worker has a time limit but no memory limit. See
[testing](testing.md) for checks.
