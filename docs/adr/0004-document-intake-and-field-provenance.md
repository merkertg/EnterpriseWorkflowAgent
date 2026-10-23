# ADR 0004: Document intake, isolated PDF parsing and verified field provenance

- Status: Accepted
- Date: 2026-10-23
- Scope: Stage 04 document intake, text extraction, field schema and storage

## Context

Stage 03 persists invoices created in code. Stage 04 must accept bounded text
and PDF input, reject unsupported or oversized files clearly, store documents
under server-generated IDs, extract fields with a deterministic parser, record
the source of every value, and send incomplete extraction to manual review.
Stage 07 will add a model-based extractor whose output is an untrusted
proposal. Document text must never act as an instruction.

## Decision

1. Detect format from bytes, not names: `%PDF-` is PDF; otherwise strict UTF-8
   without NUL or binary control characters; common binary signatures are
   rejected. Enforce `DocumentLimits` (1 MiB, 20 pages, 200 000 characters,
   5 s PDF parsing) before storage. Rejections are a `DocumentRejectedError`
   with a stable `RejectionCode`, and nothing is written.
2. Parse PDFs with pypdf 6 in a separate `python -I` process running a
   standalone worker script that imports no application code. Bytes go in on
   stdin and JSON comes out on stdout; the process is killed at the timeout,
   and any unexpected answer counts as a malformed PDF. Text files are decoded
   in-process. Newlines are normalized so offsets are stable.
3. Define a strict field schema in the domain (`app/domain/extraction.py`):
   fixed header names plus `lines[n].<part>`, statuses
   found/missing/ambiguous/invalid, and `Evidence(page, start, end)` spans into
   stored page text. `assess_extraction` verifies that every found value equals
   the text at each cited span, enforces the schema and canonical formats
   (`digits.dd` money, plain decimal quantity, known currency), applies invoice
   rules and reports every problem at once. Only a complete assessment yields
   an `Invoice`; it never repairs or guesses values.
4. Introduce ports `TextExtractor` and `FieldExtractor`. The first extractor is
   `LabeledTextInvoiceParser`, a deterministic "Label: value" parser that
   ignores unknown labels and free text. A future model provider implements
   the same port and passes the same verification.
5. Store documents append-only: original bytes (`bytea`) with a unique SHA-256,
   normalized page texts, extractions (unique per document and extractor) and
   fields with JSON evidence. One transaction writes document, pages,
   extraction and, for complete extractions, the invoice version via the
   Stage 03 version logic. Identical bytes reuse the stored document and
   extraction; races on the unique hash retry. Evidence always refers to
   stored pages; if a re-upload extracts different text, fields are
   recomputed from stored pages. Reads verify byte hashes and quotes.
6. Keep `prepare_document` free of storage, so the CLI reports refusals
   without a database. Expose the stage through `python -m app.documents_cli`
   (`write-samples`, `ingest`, `show`, `evidence`, `demo`). Defer the HTTP
   upload endpoint to Stage 06, where authentication and roles exist.
7. Generate fictional samples, including PDFs, with a small deterministic
   writer in `app/samples.py`, so no binary fixtures are committed.

## Alternatives considered

- **Trust the file extension or declared content type:** rejected; both are
  caller-controlled.
- **Parse PDFs in-process, or with `multiprocessing` spawn:** in-process cannot
  be interrupted reliably; spawn re-imports the caller's main module, which
  breaks depending on how Python was started.
- **PyMuPDF or pdfminer.six:** PyMuPDF's AGPL licence and native code are
  heavier than needed; pypdf is pure Python, typed and sufficient for
  text-layer PDFs.
- **Store files on disk:** separates bytes from the transaction that records
  them; small learning files fit in PostgreSQL.
- **Let the parser normalize values (e.g. `30,00` → `30.00`):** guessing hides
  ambiguity; review is the safer outcome.
- **Expose an unauthenticated upload API now:** would invite accidental
  exposure before Stage 06 access checks.

## Consequences

Every stored value can be traced to a page, line and exact quote, and a
fabricated value cannot pass even if a future model cites real text. Refused
files leave no data. A hostile PDF can cost at most the timeout, though memory
use is not yet limited. The training format is narrow, and real supplier
layouts will need additional extractors. Fields that need review cannot yet be
corrected by a person; that workflow arrives with the interface stages.
