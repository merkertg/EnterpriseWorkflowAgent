"""Document intake: validate, extract page text and fields, store with evidence.

``prepare`` does all checks and extraction without touching the database, so
a rejected file is never stored. ``store`` writes the document, its page texts,
the extraction and, only for a complete extraction, an invoice version in one
transaction. Incomplete extractions wait for a person and never yield an invoice.
"""

import hashlib
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import PurePosixPath
from uuid import UUID, uuid4

from app.application.access import Permission, Principal, authorize
from app.application.errors import (
    ConcurrentWriteError,
    NotFoundError,
    StoredDataIntegrityError,
    VersionConflictError,
)
from app.application.ports import (
    FieldExtractor,
    StoredDocument,
    StoredExtraction,
    TextExtractor,
    UnitOfWork,
)
from app.application.users import current_principal
from app.application.versions import append_invoice_version
from app.domain.documents import (
    DocumentLimits,
    MediaType,
    check_size,
    detect_media_type,
)
from app.domain.errors import DomainValidationError
from app.domain.extraction import (
    Assessment,
    ExtractedField,
    ExtractionResult,
    FieldStatus,
    assess_extraction,
)

_STORE_ATTEMPTS = 3


@dataclass(frozen=True, slots=True)
class PreparedDocument:
    """Everything known about an accepted file before it is stored."""

    content: bytes
    content_hash: str
    media_type: MediaType
    declared_name: str | None
    pages: tuple[str, ...]
    extraction: ExtractionResult
    assessment: Assessment


@dataclass(frozen=True, slots=True)
class IngestResult:
    """The stored document and extraction; flags show reuse of earlier rows."""

    document: StoredDocument
    extraction: StoredExtraction
    reused_document: bool
    reused_extraction: bool


@dataclass(frozen=True, slots=True)
class Citation:
    """One cited span with its exact text and the full line around it."""

    page: int
    start: int
    end: int
    quote: str
    line_number: int
    line: str


@dataclass(frozen=True, slots=True)
class FieldEvidence:
    """A field as extracted and the source text that supports it."""

    document_id: UUID
    extractor: str
    field: ExtractedField
    citations: tuple[Citation, ...]


def clean_declared_name(name: str | None) -> str | None:
    """Keep a short base name as a label only; it never decides the format."""
    if name is None:
        return None
    base = PurePosixPath(name.replace("\\", "/")).name
    printable = "".join(char for char in base if char.isprintable()).strip()
    return printable[:255] or None


def cite(pages: tuple[str, ...], page: int, start: int, end: int) -> Citation:
    """Cut the quote and its line from stored page text."""
    text = pages[page - 1]
    line_start = text.rfind("\n", 0, start) + 1
    line_end = text.find("\n", end)
    return Citation(
        page=page,
        start=start,
        end=end,
        quote=text[start:end],
        line_number=text.count("\n", 0, start) + 1,
        line=text[line_start : len(text) if line_end == -1 else line_end],
    )


def prepare_document(
    content: bytes,
    declared_name: str | None,
    *,
    text_extractor: TextExtractor,
    field_extractor: FieldExtractor,
    limits: DocumentLimits,
) -> PreparedDocument:
    """Check size and format, extract text and fields; no database access.

    Raises DocumentRejectedError for refused input. The name is a label: the
    format comes only from the bytes.
    """
    check_size(content, limits)
    media_type = detect_media_type(content)
    pages = text_extractor.extract_pages(content, media_type, limits)
    extraction = field_extractor.extract(pages)
    return PreparedDocument(
        content=content,
        content_hash=hashlib.sha256(content).hexdigest(),
        media_type=media_type,
        declared_name=clean_declared_name(declared_name),
        pages=pages,
        extraction=extraction,
        assessment=assess_extraction(extraction, pages),
    )


class DocumentService:
    """Accept documents within limits and keep every value traceable."""

    def __init__(
        self,
        unit_of_work: Callable[[], UnitOfWork],
        text_extractor: TextExtractor,
        field_extractor: FieldExtractor,
        limits: DocumentLimits | None = None,
    ) -> None:
        """Receive adapters explicitly; limits default to DocumentLimits()."""
        self._unit_of_work = unit_of_work
        self._text_extractor = text_extractor
        self._field_extractor = field_extractor
        self._limits = DocumentLimits() if limits is None else limits

    def prepare(self, content: bytes, declared_name: str | None) -> PreparedDocument:
        """Check and extract with this service's adapters and limits."""
        return prepare_document(
            content,
            declared_name,
            text_extractor=self._text_extractor,
            field_extractor=self._field_extractor,
            limits=self._limits,
        )

    def ingest(
        self,
        content: bytes,
        declared_name: str | None,
        *,
        actor: Principal | None = None,
    ) -> IngestResult:
        """Prepare and store in one call.

        An actor needs the submit permission; it is checked before the costly
        parsing and again, against the stored account, when storing.
        """
        if actor is not None:
            authorize(actor, Permission.SUBMIT, None)
        return self.store(self.prepare(content, declared_name), actor=actor)

    def store(
        self, prepared: PreparedDocument, *, actor: Principal | None = None
    ) -> IngestResult:
        """Store once per identical bytes and once per extractor, atomically."""
        if type(prepared) is not PreparedDocument:
            raise DomainValidationError(
                "invalid_type", "prepared", "Use a PreparedDocument."
            )
        for _attempt in range(_STORE_ATTEMPTS):
            try:
                return self._store_once(prepared, actor)
            except ConcurrentWriteError:
                # Another writer stored the same bytes or extraction first.
                continue
        raise VersionConflictError("Concurrent writers are storing this document.")

    def _store_once(
        self, prepared: PreparedDocument, actor: Principal | None
    ) -> IngestResult:
        """Write document, pages, extraction and invoice version together."""
        with self._unit_of_work() as unit:
            current_principal(unit, actor, Permission.SUBMIT, None)
            documents = unit.documents
            existing = documents.find_document_by_hash(prepared.content_hash)
            if existing is None:
                document = StoredDocument(
                    document_id=uuid4(),
                    content_hash=prepared.content_hash,
                    media_type=prepared.media_type,
                    size_bytes=len(prepared.content),
                    page_count=len(prepared.pages),
                    declared_name=prepared.declared_name,
                )
                documents.add_document(document, prepared.content, prepared.pages)
                extraction, assessment = prepared.extraction, prepared.assessment
            else:
                document = existing
                extraction, assessment = self._for_stored_pages(
                    prepared, documents.get_pages(document.document_id)
                )
            found = documents.find_extraction(
                document.document_id, extraction.extractor
            )
            if found is not None:
                return IngestResult(document, found, existing is not None, True)
            invoice_version = (
                None
                if assessment.invoice is None
                else append_invoice_version(unit.repository, assessment.invoice)
            )
            stored = StoredExtraction(
                extraction_id=uuid4(),
                document_id=document.document_id,
                result=extraction,
                status=assessment.status,
                problems=assessment.problems,
                invoice_version=invoice_version,
            )
            documents.add_extraction(stored)
            unit.commit()
            return IngestResult(document, stored, existing is not None, False)

    def _for_stored_pages(
        self, prepared: PreparedDocument, stored_pages: tuple[str, ...]
    ) -> tuple[ExtractionResult, Assessment]:
        """Evidence must point into stored text; re-extract if it differs."""
        if stored_pages == prepared.pages:
            return prepared.extraction, prepared.assessment
        extraction = self._field_extractor.extract(stored_pages)
        return extraction, assess_extraction(extraction, stored_pages)

    # Queries ------------------------------------------------------------------

    def get_document(self, document_id: UUID) -> StoredDocument:
        """Return metadata of a stored document."""
        with self._unit_of_work() as unit:
            document = unit.documents.get_document(document_id)
        if document is None:
            raise NotFoundError("Document was not found.")
        return document

    def get_content(self, document_id: UUID) -> bytes:
        """Return the original bytes after verifying their hash."""
        with self._unit_of_work() as unit:
            content = unit.documents.get_content(document_id)
        if content is None:
            raise NotFoundError("Document was not found.")
        return content

    def get_extraction(self, document_id: UUID) -> StoredExtraction:
        """Return the latest extraction of a document."""
        with self._unit_of_work() as unit:
            extraction = unit.documents.latest_extraction(document_id)
        if extraction is None:
            raise NotFoundError("Document or extraction was not found.")
        return extraction

    def get_evidence(self, document_id: UUID, field_name: str) -> FieldEvidence:
        """Open the source text behind one field, re-checking it on the way."""
        with self._unit_of_work() as unit:
            extraction = unit.documents.latest_extraction(document_id)
            pages = unit.documents.get_pages(document_id)
        if extraction is None:
            raise NotFoundError("Document or extraction was not found.")
        field = extraction.result.field(field_name)
        if field is None:
            raise NotFoundError("The extraction has no such field.")
        if any(item.quote(pages) is None for item in field.evidence):
            raise StoredDataIntegrityError("Evidence points outside the stored text.")
        citations = tuple(
            cite(pages, item.page, item.start, item.end) for item in field.evidence
        )
        if field.status is FieldStatus.FOUND and any(
            citation.quote != field.value for citation in citations
        ):
            raise StoredDataIntegrityError("Stored evidence does not match the value.")
        return FieldEvidence(document_id, extraction.result.extractor, field, citations)
