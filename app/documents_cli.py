"""Stage 04 command line: write samples, ingest a file, show fields and evidence.

``write-samples`` needs no database. ``ingest`` checks and extracts a file
before connecting, so a refused file is reported without DATABASE_URL.
Output is JSON; it may contain document text because it is the caller's own.
"""

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from uuid import UUID, uuid4

from sqlalchemy import Engine
from sqlalchemy.exc import OperationalError, ProgrammingError

from app.application.documents import (
    DocumentService,
    FieldEvidence,
    IngestResult,
    prepare_document,
)
from app.application.errors import ApplicationError
from app.application.ports import CommandContext, StoredExtraction, VersionRef
from app.config import Settings
from app.demo import build_demo_documents
from app.domain.documents import DocumentLimits, DocumentRejectedError
from app.domain.errors import DomainValidationError
from app.domain.rules import ValidationPolicy
from app.infrastructure.db.session import (
    build_document_service,
    build_workflow_service,
    create_database_engine,
)
from app.infrastructure.documents.text_extraction import LimitedTextExtractor
from app.infrastructure.extraction.labeled_text import LabeledTextInvoiceParser
from app.samples import build_text_pdf, sample_files, write_samples


class _ConfigurationError(Exception):
    """DATABASE_URL is missing or invalid; reported with exit code 2."""


def _ref(ref: VersionRef | None) -> dict[str, object] | None:
    """Show an invoice version reference."""
    if ref is None:
        return None
    return {
        "invoice_id": ref.business_id,
        "version_no": ref.version_no,
        "version_id": str(ref.version_id),
    }


def describe_extraction(extraction: StoredExtraction) -> dict[str, object]:
    """List fields with status, value and evidence coordinates."""
    return {
        "extraction_id": str(extraction.extraction_id),
        "extractor": extraction.result.extractor,
        "status": extraction.status.value,
        "problems": [
            {"code": p.code.value, "field": p.field, "message": p.message}
            for p in extraction.problems
        ],
        "invoice_version": _ref(extraction.invoice_version),
        "fields": [
            {
                "name": item.name,
                "status": item.status.value,
                "value": item.value,
                "evidence": [[e.page, e.start, e.end] for e in item.evidence],
            }
            for item in extraction.result.fields
        ],
    }


def describe_ingest(result: IngestResult) -> dict[str, object]:
    """Summarize a stored document and its extraction."""
    document = result.document
    return {
        "document_id": str(document.document_id),
        "media_type": document.media_type.value,
        "pages": document.page_count,
        "size_bytes": document.size_bytes,
        "content_hash": document.content_hash,
        "reused_document": result.reused_document,
        "reused_extraction": result.reused_extraction,
        **describe_extraction(result.extraction),
    }


def describe_evidence(evidence: FieldEvidence) -> dict[str, object]:
    """Show the source text behind a field: page, offsets, quote and line."""
    return {
        "document_id": str(evidence.document_id),
        "extractor": evidence.extractor,
        "field": evidence.field.name,
        "status": evidence.field.status.value,
        "value": evidence.field.value,
        "message": evidence.field.message,
        "citations": [
            {
                "page": c.page,
                "line_number": c.line_number,
                "start": c.start,
                "end": c.end,
                "quote": c.quote,
                "line": c.line,
            }
            for c in evidence.citations
        ],
    }


def _engine() -> Engine:
    """Create an engine from DATABASE_URL or raise a configuration error."""
    settings = Settings()
    if settings.database_url is None:
        raise _ConfigurationError("Set DATABASE_URL to use the document store.")
    try:
        return create_database_engine(settings.database_url.get_secret_value())
    except ValueError as error:
        raise _ConfigurationError(str(error)) from error


def _read_limited(path: Path, limits: DocumentLimits) -> bytes:
    """Read at most one byte past the limit, so huge files are not loaded."""
    with path.open("rb") as handle:
        return handle.read(limits.max_bytes + 1)


def run_documents_demo(service: DocumentService, engine: Engine) -> dict[str, object]:
    """Ingest samples, show refusals and evidence, then start a workflow."""
    files = sample_files()
    stored: dict[str, IngestResult] = {
        name: service.ingest(content, name) for name, content in files.items()
    }
    refused: dict[str, str] = {}
    for name, content in {
        "picture.png": b"\x89PNG\r\n\x1a\nfictional",
        "too_large.txt": b"a" * (DocumentLimits().max_bytes + 1),
        "scanned.pdf": build_text_pdf([[]]),
        "latin1.txt": "Счёт".encode("cp1251"),
    }.items():
        try:
            service.prepare(content, name)
        except DocumentRejectedError as error:
            refused[name] = error.code.value

    clean = stored["clean_invoice.pdf"]
    invoice_version = clean.extraction.invoice_version
    if invoice_version is None:
        raise RuntimeError("The clean sample must produce an invoice.")
    workflows = build_workflow_service(engine)
    _, purchase_order = build_demo_documents()
    order_version = workflows.record_purchase_order(purchase_order)
    policy_version = workflows.record_policy(ValidationPolicy("demo-documents-v1"))
    run_id = uuid4().hex[:12]
    workflow_id = f"DEMO-WF-DOC-{run_id}"
    workflows.start(
        workflow_id,
        invoice_version_id=invoice_version.version_id,
        purchase_order_version_id=order_version.version_id,
        policy_version_id=policy_version.version_id,
        context=CommandContext(f"{run_id}:start", "documents-demo"),
    )
    checked = workflows.check(
        workflow_id,
        expected_version=1,
        context=CommandContext(f"{run_id}:check", "documents-demo"),
    )
    document_id = clean.document.document_id
    return {
        "stage": "04",
        "mode": "fictional_documents_demo",
        "documents": {
            name: {
                key: value
                for key, value in describe_ingest(result).items()
                if key != "fields"
            }
            for name, result in stored.items()
        },
        "refused": refused,
        "evidence": {
            name: describe_evidence(service.get_evidence(document_id, name))
            for name in ("invoice_id", "supplier_id", "currency", "declared_total")
        },
        "workflow": {
            "workflow_id": workflow_id,
            "invoice_version": _ref(invoice_version),
            "state_after_check": checked.state.value,
        },
    }


def _ingest(path: Path) -> dict[str, object]:
    """Prepare offline first; only an accepted file needs the database."""
    limits = DocumentLimits()
    prepared = prepare_document(
        _read_limited(path, limits),
        path.name,
        text_extractor=LimitedTextExtractor(),
        field_extractor=LabeledTextInvoiceParser(),
        limits=limits,
    )
    engine = _engine()
    try:
        return describe_ingest(build_document_service(engine, limits).store(prepared))
    finally:
        engine.dispose()


def _stored(arguments: argparse.Namespace) -> object:
    """Run a database-backed command and release connections afterwards."""
    engine = _engine()
    try:
        service = build_document_service(engine)
        if arguments.command == "show":
            return describe_extraction(service.get_extraction(arguments.document_id))
        if arguments.command == "evidence":
            return describe_evidence(
                service.get_evidence(arguments.document_id, arguments.field)
            )
        return run_documents_demo(service, engine)
    finally:
        engine.dispose()


def main(argv: Sequence[str] | None = None) -> int:
    """Parse a command, run it and print JSON or a one-line error."""
    parser = argparse.ArgumentParser(
        prog="python -m app.documents_cli",
        description="Fictional Stage 04 document intake.",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    samples = commands.add_parser("write-samples", help="write fictional samples")
    samples.add_argument("directory", type=Path)
    ingest = commands.add_parser("ingest", help="check, extract and store a file")
    ingest.add_argument("path", type=Path)
    show = commands.add_parser("show", help="show a document's latest extraction")
    show.add_argument("document_id", type=UUID)
    evidence = commands.add_parser("evidence", help="open the source of one field")
    evidence.add_argument("document_id", type=UUID)
    evidence.add_argument("field")
    commands.add_parser("demo", help="run the fictional end-to-end scenario")
    arguments = parser.parse_args(argv)

    try:
        if arguments.command == "write-samples":
            paths = write_samples(arguments.directory)
            output: object = [str(path) for path in paths]
        elif arguments.command == "ingest":
            output = _ingest(arguments.path)
        else:
            output = _stored(arguments)
    except _ConfigurationError as error:
        print(str(error), file=sys.stderr)
        return 2
    except DocumentRejectedError as error:
        print(f"rejected: {error.code.value}: {error}", file=sys.stderr)
        return 1
    except (ApplicationError, DomainValidationError) as error:
        print(f"{error.code}: {error}", file=sys.stderr)
        return 1
    except FileExistsError as error:
        print(f"Refusing to overwrite {error.filename}.", file=sys.stderr)
        return 1
    except OSError as error:
        print(f"Cannot read or write a file: {error.strerror}.", file=sys.stderr)
        return 1
    except OperationalError:
        print("Cannot connect to the database in DATABASE_URL.", file=sys.stderr)
        return 1
    except ProgrammingError:
        print("Schema is missing: run `alembic upgrade head` first.", file=sys.stderr)
        return 1
    print(json.dumps(output, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
