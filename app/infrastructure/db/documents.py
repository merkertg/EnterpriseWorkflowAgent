"""SQLAlchemy implementation of the document repository.

Documents keep their original bytes and the page texts that evidence refers
to. Loads verify the byte hash and rebuild fields through domain constructors.
"""

import hashlib
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.application.errors import StoredDataIntegrityError
from app.application.ports import StoredDocument, StoredExtraction, VersionRef
from app.domain.documents import MediaType
from app.domain.extraction import (
    Evidence,
    ExtractedField,
    ExtractionProblem,
    ExtractionResult,
    ExtractionStatus,
    FieldStatus,
    ProblemCode,
)
from app.infrastructure.db.inserts import insert_rows
from app.infrastructure.db.models import (
    DocumentExtractionRow,
    DocumentPageRow,
    ExtractedFieldRow,
    InvoiceVersionRow,
    SourceDocumentRow,
)
from app.infrastructure.db.repository import reconstructing


def _document(row: SourceDocumentRow) -> StoredDocument:
    """Map a row to metadata without loading the bytes into the record."""
    with reconstructing("document"):
        media_type = MediaType(row.media_type)
    return StoredDocument(
        document_id=row.id,
        content_hash=row.content_hash,
        media_type=media_type,
        size_bytes=row.size_bytes,
        page_count=row.page_count,
        declared_name=row.declared_name,
    )


def _evidence_data(item: ExtractedField) -> list[dict[str, Any]]:
    """Store spans as small JSON objects with page and offsets only."""
    return [{"page": e.page, "start": e.start, "end": e.end} for e in item.evidence]


class SqlAlchemyDocumentRepository:
    """Document storage bound to the unit of work's session."""

    def __init__(self, session: Session) -> None:
        """Use the shared session; this class never commits."""
        self._session = session

    def find_document_by_hash(self, content_hash: str) -> StoredDocument | None:
        """Return an already stored document with identical bytes."""
        row = self._session.scalars(
            select(SourceDocumentRow).where(
                SourceDocumentRow.content_hash == content_hash
            )
        ).first()
        return None if row is None else _document(row)

    def get_document(self, document_id: UUID) -> StoredDocument | None:
        """Return document metadata by server-generated ID."""
        row = self._session.get(SourceDocumentRow, document_id)
        return None if row is None else _document(row)

    def add_document(
        self, document: StoredDocument, content: bytes, pages: tuple[str, ...]
    ) -> None:
        """Insert bytes first, so a duplicate-hash race is detected early."""
        insert_rows(
            self._session,
            SourceDocumentRow(
                id=document.document_id,
                content_hash=document.content_hash,
                media_type=document.media_type.value,
                size_bytes=document.size_bytes,
                page_count=document.page_count,
                declared_name=document.declared_name,
                content=content,
            ),
        )
        insert_rows(
            self._session,
            *(
                DocumentPageRow(
                    document_id=document.document_id, page_no=number, text=text
                )
                for number, text in enumerate(pages, start=1)
            ),
        )

    def get_content(self, document_id: UUID) -> bytes | None:
        """Return original bytes after checking them against the stored hash."""
        row = self._session.get(SourceDocumentRow, document_id)
        if row is None:
            return None
        if hashlib.sha256(row.content).hexdigest() != row.content_hash:
            raise StoredDataIntegrityError("Stored document bytes do not match.")
        return row.content

    def get_pages(self, document_id: UUID) -> tuple[str, ...]:
        """Return page texts in page order."""
        rows = self._session.scalars(
            select(DocumentPageRow)
            .where(DocumentPageRow.document_id == document_id)
            .order_by(DocumentPageRow.page_no)
        ).all()
        return tuple(row.text for row in rows)

    def _extraction(self, row: DocumentExtractionRow) -> StoredExtraction:
        """Rebuild fields, problems and the invoice version reference."""
        field_rows = self._session.scalars(
            select(ExtractedFieldRow)
            .where(ExtractedFieldRow.extraction_id == row.id)
            .order_by(ExtractedFieldRow.position)
        ).all()
        invoice_ref: VersionRef | None = None
        if row.invoice_version_id is not None:
            invoice = self._session.get(InvoiceVersionRow, row.invoice_version_id)
            if invoice is None:
                raise StoredDataIntegrityError("Extraction invoice is missing.")
            invoice_ref = VersionRef(
                invoice.id, invoice.invoice_id, invoice.version_no, invoice.content_hash
            )
        with reconstructing("extraction"):
            result = ExtractionResult(
                row.extractor,
                tuple(
                    ExtractedField(
                        name=field.name,
                        status=FieldStatus(field.status),
                        value=field.value,
                        evidence=tuple(
                            Evidence(item["page"], item["start"], item["end"])
                            for item in field.evidence
                        ),
                        message=field.message,
                    )
                    for field in field_rows
                ),
            )
            problems = tuple(
                ExtractionProblem(
                    ProblemCode(item["code"]), item["field"], item["message"]
                )
                for item in row.problems
            )
            status = ExtractionStatus(row.status)
        return StoredExtraction(
            extraction_id=row.id,
            document_id=row.document_id,
            result=result,
            status=status,
            problems=problems,
            invoice_version=invoice_ref,
        )

    def find_extraction(
        self, document_id: UUID, extractor: str
    ) -> StoredExtraction | None:
        """Return the extraction one extractor already made for a document."""
        row = self._session.scalars(
            select(DocumentExtractionRow).where(
                DocumentExtractionRow.document_id == document_id,
                DocumentExtractionRow.extractor == extractor,
            )
        ).first()
        return None if row is None else self._extraction(row)

    def latest_extraction(self, document_id: UUID) -> StoredExtraction | None:
        """Return the newest extraction; ties are broken by ID for stability."""
        row = self._session.scalars(
            select(DocumentExtractionRow)
            .where(DocumentExtractionRow.document_id == document_id)
            .order_by(DocumentExtractionRow.created_at.desc(), DocumentExtractionRow.id)
            .limit(1)
        ).first()
        return None if row is None else self._extraction(row)

    def add_extraction(self, extraction: StoredExtraction) -> None:
        """Insert the extraction header, then each field in reported order."""
        insert_rows(
            self._session,
            DocumentExtractionRow(
                id=extraction.extraction_id,
                document_id=extraction.document_id,
                extractor=extraction.result.extractor,
                status=extraction.status.value,
                invoice_version_id=None
                if extraction.invoice_version is None
                else extraction.invoice_version.version_id,
                problems=[
                    {"code": p.code.value, "field": p.field, "message": p.message}
                    for p in extraction.problems
                ],
            ),
        )
        insert_rows(
            self._session,
            *(
                ExtractedFieldRow(
                    extraction_id=extraction.extraction_id,
                    position=position,
                    name=item.name,
                    status=item.status.value,
                    value=item.value,
                    message=item.message,
                    evidence=_evidence_data(item),
                )
                for position, item in enumerate(extraction.result.fields)
            ),
        )
