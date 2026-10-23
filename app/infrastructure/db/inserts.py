"""Flush inserts immediately and translate known constraint races."""

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.application.errors import (
    ConcurrentWriteError,
    IdempotencyKeyClaimedError,
    WorkflowAlreadyExistsError,
)

# Unique constraints whose violation means "another writer was first".
_CONCURRENT_UNIQUES = {
    "uq_invoice_versions_invoice_id_version_no",
    "uq_purchase_order_versions_purchase_order_id_version_no",
    "uq_policy_versions_policy_id_version_no",
    "uq_source_documents_content_hash",
    "uq_document_extractions_document_id_extractor",
}


def _constraint_name(error: IntegrityError) -> str | None:
    """Read the violated constraint reported by PostgreSQL through psycopg."""
    diagnostics = getattr(error.orig, "diag", None)
    name = getattr(diagnostics, "constraint_name", None)
    return name if isinstance(name, str) else None


def insert_rows(session: Session, *rows: object) -> None:
    """Add rows and flush now, so a uniqueness race surfaces at a known point."""
    session.add_all(rows)
    try:
        session.flush()
    except IntegrityError as error:
        name = _constraint_name(error)
        if name in _CONCURRENT_UNIQUES:
            raise ConcurrentWriteError("Another writer stored this first.") from error
        if name == "pk_idempotency_records":
            raise IdempotencyKeyClaimedError(
                "Another request committed this idempotency key."
            ) from error
        if name == "pk_workflows":
            raise WorkflowAlreadyExistsError(
                "Workflow ID is already in use."
            ) from error
        raise
