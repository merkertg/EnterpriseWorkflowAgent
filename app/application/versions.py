"""Append-only version numbering shared by every versioned input.

Called inside the caller's transaction. Identical content reuses the latest
version; anything else gets the next number. A unique constraint settles the
race when two writers pick the same number.
"""

from collections.abc import Callable
from uuid import uuid4

from app.application.ports import Versioned, VersionRef, WorkflowRepository
from app.domain.content_hash import content_hash
from app.domain.models import Invoice


def append_version(
    business_id: str,
    digest: str,
    current: VersionRef | None,
    add: Callable[[VersionRef], None],
) -> VersionRef:
    """Inside a transaction: reuse identical latest content or add the next number."""
    if current is not None and current.content_hash == digest:
        return current
    ref = VersionRef(
        version_id=uuid4(),
        business_id=business_id,
        version_no=1 if current is None else current.version_no + 1,
        content_hash=digest,
    )
    add(ref)
    return ref


def append_invoice_version(
    repository: WorkflowRepository, invoice: Invoice
) -> VersionRef:
    """Record invoice content in the caller's transaction; documents reuse this."""
    return append_version(
        invoice.invoice_id,
        content_hash(invoice),
        repository.latest_invoice_version(invoice.invoice_id),
        lambda ref: repository.add_invoice_version(Versioned(ref, invoice)),
    )


def invoice_number_key(supplier_id: str, invoice_id: str) -> str:
    """Lock key for one supplier invoice number (duplicate detection)."""
    return f"invoice-number:{supplier_id}|{invoice_id}"


def purchase_order_key(purchase_order_id: str) -> str:
    """Lock key for the version history of one purchase order."""
    return f"purchase-order:{purchase_order_id}"


def rules_key(rules_id: str) -> str:
    """Lock key for the version history of one reconciliation rule set."""
    return f"reconciliation-rules:{rules_id}"
