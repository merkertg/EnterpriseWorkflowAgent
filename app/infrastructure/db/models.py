"""SQLAlchemy table mappings for versions, workflows, snapshots and audit.

Money uses NUMERIC columns and check constraints repeat the most important
domain bounds, so manual SQL cannot store an obviously invalid row.
"""

from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Identity,
    Integer,
    LargeBinary,
    MetaData,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.application.access import Role
from app.application.ports import CommandName, OutboxEventType
from app.domain.documents import MediaType
from app.domain.extraction import ExtractionStatus, FieldStatus
from app.domain.money import Currency
from app.domain.reconciliation import (
    SUPPORTED_DECIMAL_PLACES,
    SUPPORTED_ROUNDING,
    ReconciliationStatus,
)
from app.domain.workflow import WorkflowState

# Deterministic names let migrations and error handling refer to constraints.
NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s",
    "pk": "pk_%(table_name)s",
}

# Domain bounds: 999999999999.99 fits NUMERIC(14, 2), 999999.999999 fits (12, 6).
MONEY = Numeric(14, 2)
QUANTITY = Numeric(12, 6)
HASH_PATTERN = "'^[0-9a-f]{64}$'"


def _one_of(column: str, values: tuple[str, ...]) -> str:
    """Build a SQL membership test from trusted enum values, never user input."""
    return f"{column} IN ({', '.join(repr(value) for value in values)})"


CURRENCY_VALUES = tuple(item.value for item in Currency)
STATE_VALUES = tuple(item.value for item in WorkflowState)
COMMAND_VALUES = tuple(item.value for item in CommandName)
MEDIA_TYPE_VALUES = tuple(item.value for item in MediaType)
EXTRACTION_STATUS_VALUES = tuple(item.value for item in ExtractionStatus)
FIELD_STATUS_VALUES = tuple(item.value for item in FieldStatus)
RECONCILIATION_STATUS_VALUES = tuple(item.value for item in ReconciliationStatus)
ROLE_VALUES = tuple(item.value for item in Role)
OUTBOX_EVENT_VALUES = tuple(item.value for item in OutboxEventType)


class Base(DeclarativeBase):
    """Shared metadata used by the application and Alembic autogenerate checks."""

    metadata = MetaData(naming_convention=NAMING_CONVENTION)


def _created_at() -> Mapped[datetime]:
    """Use the database transaction time, so all writers share one clock."""
    return mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class InvoiceVersionRow(Base):
    """One immutable invoice header; lines are stored in a child table."""

    __tablename__ = "invoice_versions"
    __table_args__ = (
        UniqueConstraint("invoice_id", "version_no"),
        CheckConstraint("version_no > 0", name="version_no_positive"),
        CheckConstraint(f"content_hash ~ {HASH_PATTERN}", name="content_hash_hex"),
        CheckConstraint(_one_of("currency", CURRENCY_VALUES), name="currency"),
        CheckConstraint("declared_total >= 0", name="declared_total_nonnegative"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True)
    invoice_id: Mapped[str] = mapped_column(String(128))
    version_no: Mapped[int] = mapped_column(Integer)
    content_hash: Mapped[str] = mapped_column(String(64))
    supplier_id: Mapped[str] = mapped_column(String(128))
    purchase_order_id: Mapped[str] = mapped_column(String(128))
    currency: Mapped[str] = mapped_column(String(3))
    declared_total: Mapped[Decimal] = mapped_column(MONEY)
    created_at: Mapped[datetime] = _created_at()


class InvoiceLineRow(Base):
    """One ordered invoice line with exact NUMERIC quantity and price."""

    __tablename__ = "invoice_version_lines"
    __table_args__ = (
        UniqueConstraint("invoice_version_id", "sku"),
        CheckConstraint("line_no > 0", name="line_no_positive"),
        CheckConstraint("quantity > 0", name="quantity_positive"),
        CheckConstraint("unit_price >= 0", name="unit_price_nonnegative"),
    )

    invoice_version_id: Mapped[UUID] = mapped_column(
        ForeignKey("invoice_versions.id"), primary_key=True
    )
    line_no: Mapped[int] = mapped_column(Integer, primary_key=True)
    sku: Mapped[str] = mapped_column(String(128))
    description: Mapped[str] = mapped_column(String(500))
    quantity: Mapped[Decimal] = mapped_column(QUANTITY)
    unit_price: Mapped[Decimal] = mapped_column(MONEY)


class PurchaseOrderVersionRow(Base):
    """One immutable purchase order header."""

    __tablename__ = "purchase_order_versions"
    __table_args__ = (
        UniqueConstraint("purchase_order_id", "version_no"),
        CheckConstraint("version_no > 0", name="version_no_positive"),
        CheckConstraint(f"content_hash ~ {HASH_PATTERN}", name="content_hash_hex"),
        CheckConstraint(_one_of("currency", CURRENCY_VALUES), name="currency"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True)
    purchase_order_id: Mapped[str] = mapped_column(String(128))
    version_no: Mapped[int] = mapped_column(Integer)
    content_hash: Mapped[str] = mapped_column(String(64))
    supplier_id: Mapped[str] = mapped_column(String(128))
    currency: Mapped[str] = mapped_column(String(3))
    created_at: Mapped[datetime] = _created_at()


class PurchaseOrderLineRow(Base):
    """One ordered purchase order line."""

    __tablename__ = "purchase_order_version_lines"
    __table_args__ = (
        UniqueConstraint("purchase_order_version_id", "sku"),
        CheckConstraint("line_no > 0", name="line_no_positive"),
        CheckConstraint("quantity > 0", name="quantity_positive"),
        CheckConstraint("unit_price >= 0", name="unit_price_nonnegative"),
    )

    purchase_order_version_id: Mapped[UUID] = mapped_column(
        ForeignKey("purchase_order_versions.id"), primary_key=True
    )
    line_no: Mapped[int] = mapped_column(Integer, primary_key=True)
    sku: Mapped[str] = mapped_column(String(128))
    description: Mapped[str] = mapped_column(String(500))
    quantity: Mapped[Decimal] = mapped_column(QUANTITY)
    unit_price: Mapped[Decimal] = mapped_column(MONEY)


class PolicyVersionRow(Base):
    """One immutable validation policy; array order is part of its content."""

    __tablename__ = "policy_versions"
    __table_args__ = (
        UniqueConstraint("policy_id", "version_no"),
        CheckConstraint("version_no > 0", name="version_no_positive"),
        CheckConstraint(f"content_hash ~ {HASH_PATTERN}", name="content_hash_hex"),
        CheckConstraint(
            "cardinality(allowed_currencies) > 0", name="allowed_currencies_nonempty"
        ),
        CheckConstraint(
            "allowed_currencies <@ ARRAY["
            + ", ".join(repr(value) for value in CURRENCY_VALUES)
            + "]::varchar[]",
            name="allowed_currencies_known",
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True)
    policy_id: Mapped[str] = mapped_column(String(128))
    version_no: Mapped[int] = mapped_column(Integer)
    content_hash: Mapped[str] = mapped_column(String(64))
    allowed_currencies: Mapped[list[str]] = mapped_column(ARRAY(String(3)))
    created_at: Mapped[datetime] = _created_at()


class WorkflowRow(Base):
    """The only mutable row: current state, lock version and pinned inputs."""

    __tablename__ = "workflows"
    __table_args__ = (
        CheckConstraint(_one_of("state", STATE_VALUES), name="state"),
        CheckConstraint("version > 0", name="version_positive"),
        CheckConstraint(
            "(decision_actor_id IS NULL) = (decision_reason IS NULL)",
            name="decision_complete",
        ),
    )

    workflow_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    invoice_id: Mapped[str] = mapped_column(String(128), index=True)
    state: Mapped[str] = mapped_column(String(32))
    version: Mapped[int] = mapped_column(Integer)
    invoice_version_id: Mapped[UUID] = mapped_column(ForeignKey("invoice_versions.id"))
    purchase_order_version_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("purchase_order_versions.id")
    )
    policy_version_id: Mapped[UUID] = mapped_column(ForeignKey("policy_versions.id"))
    # Snapshots also point to workflows, so this foreign key is added afterwards.
    last_snapshot_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("validation_snapshots.id", use_alter=True)
    )
    decision_actor_id: Mapped[str | None] = mapped_column(String(128))
    decision_reason: Mapped[str | None] = mapped_column(String(2000))
    # Stage 06: owning business unit, who started it and the approved snapshot.
    business_unit: Mapped[str] = mapped_column(String(64), index=True)
    created_by: Mapped[str | None] = mapped_column(String(64))
    decision_reconciliation_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("reconciliation_snapshots.id", use_alter=True)
    )
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = _created_at()


class ValidationSnapshotRow(Base):
    """An immutable check result with input IDs and their content hashes."""

    __tablename__ = "validation_snapshots"
    __table_args__ = (
        CheckConstraint("workflow_version > 0", name="workflow_version_positive"),
        CheckConstraint(
            "(purchase_order_version_id IS NULL) "
            "= (purchase_order_content_hash IS NULL)",
            name="purchase_order_pair",
        ),
        CheckConstraint(_one_of("currency", CURRENCY_VALUES), name="currency"),
        CheckConstraint("calculated_total >= 0", name="calculated_total_nonnegative"),
        CheckConstraint("jsonb_typeof(issues) = 'array'", name="issues_array"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True)
    workflow_id: Mapped[str] = mapped_column(
        ForeignKey("workflows.workflow_id"), index=True
    )
    workflow_version: Mapped[int] = mapped_column(Integer)
    invoice_version_id: Mapped[UUID] = mapped_column(ForeignKey("invoice_versions.id"))
    invoice_content_hash: Mapped[str] = mapped_column(String(64))
    purchase_order_version_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("purchase_order_versions.id")
    )
    purchase_order_content_hash: Mapped[str | None] = mapped_column(String(64))
    policy_version_id: Mapped[UUID] = mapped_column(ForeignKey("policy_versions.id"))
    policy_content_hash: Mapped[str] = mapped_column(String(64))
    currency: Mapped[str] = mapped_column(String(3))
    calculated_total: Mapped[Decimal] = mapped_column(MONEY)
    issues: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = _created_at()


class AuditEventRow(Base):
    """Append-only audit entry; it stores IDs and codes, not document text."""

    __tablename__ = "audit_events"
    __table_args__ = (
        CheckConstraint(_one_of("command", COMMAND_VALUES), name="command"),
        CheckConstraint(
            f"previous_state IS NULL OR {_one_of('previous_state', STATE_VALUES)}",
            name="previous_state",
        ),
        CheckConstraint(_one_of("new_state", STATE_VALUES), name="new_state"),
        CheckConstraint("workflow_version > 0", name="workflow_version_positive"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    workflow_id: Mapped[str] = mapped_column(
        ForeignKey("workflows.workflow_id"), index=True
    )
    command: Mapped[str] = mapped_column(String(32))
    previous_state: Mapped[str | None] = mapped_column(String(32))
    new_state: Mapped[str] = mapped_column(String(32))
    workflow_version: Mapped[int] = mapped_column(Integer)
    invoice_version_id: Mapped[UUID] = mapped_column(ForeignKey("invoice_versions.id"))
    purchase_order_version_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("purchase_order_versions.id")
    )
    policy_version_id: Mapped[UUID] = mapped_column(ForeignKey("policy_versions.id"))
    snapshot_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("validation_snapshots.id")
    )
    issue_codes: Mapped[list[str]] = mapped_column(
        ARRAY(String(64)), server_default=text("'{}'")
    )
    actor_id: Mapped[str | None] = mapped_column(String(128))
    source: Mapped[str] = mapped_column(String(64))
    idempotency_key: Mapped[str] = mapped_column(String(128))
    reconciliation_snapshot_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("reconciliation_snapshots.id")
    )
    outbox_event_id: Mapped[UUID | None] = mapped_column(ForeignKey("outbox_events.id"))
    occurred_at: Mapped[datetime] = _created_at()


class IdempotencyRecordRow(Base):
    """A committed command result keyed by the caller's idempotency key."""

    __tablename__ = "idempotency_records"
    __table_args__ = (
        CheckConstraint(_one_of("command", COMMAND_VALUES), name="command"),
        CheckConstraint(f"request_hash ~ {HASH_PATTERN}", name="request_hash_hex"),
        CheckConstraint(_one_of("result_state", STATE_VALUES), name="result_state"),
        CheckConstraint("result_version > 0", name="result_version_positive"),
    )

    # No foreign keys: the key is claimed before the workflow row is written.
    key: Mapped[str] = mapped_column(String(128), primary_key=True)
    command: Mapped[str] = mapped_column(String(32))
    request_hash: Mapped[str] = mapped_column(String(64))
    workflow_id: Mapped[str] = mapped_column(String(128))
    result_state: Mapped[str] = mapped_column(String(32))
    result_version: Mapped[int] = mapped_column(Integer)
    snapshot_id: Mapped[UUID | None] = mapped_column()
    created_at: Mapped[datetime] = _created_at()


class SourceDocumentRow(Base):
    """An accepted document's original bytes under a server-generated ID."""

    __tablename__ = "source_documents"
    __table_args__ = (
        UniqueConstraint("content_hash"),
        CheckConstraint(f"content_hash ~ {HASH_PATTERN}", name="content_hash_hex"),
        CheckConstraint(_one_of("media_type", MEDIA_TYPE_VALUES), name="media_type"),
        CheckConstraint(
            "size_bytes > 0 AND size_bytes = octet_length(content)",
            name="size_matches_content",
        ),
        CheckConstraint("page_count > 0", name="page_count_positive"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True)
    content_hash: Mapped[str] = mapped_column(String(64))
    media_type: Mapped[str] = mapped_column(String(32))
    size_bytes: Mapped[int] = mapped_column(Integer)
    page_count: Mapped[int] = mapped_column(Integer)
    declared_name: Mapped[str | None] = mapped_column(String(255))
    content: Mapped[bytes] = mapped_column(LargeBinary)
    created_at: Mapped[datetime] = _created_at()


class DocumentPageRow(Base):
    """Normalized text of one page; evidence offsets point into this text."""

    __tablename__ = "document_pages"
    __table_args__ = (CheckConstraint("page_no > 0", name="page_no_positive"),)

    document_id: Mapped[UUID] = mapped_column(
        ForeignKey("source_documents.id"), primary_key=True
    )
    page_no: Mapped[int] = mapped_column(Integer, primary_key=True)
    text: Mapped[str] = mapped_column(Text)


class DocumentExtractionRow(Base):
    """One extractor's result for a document and the completeness decision."""

    __tablename__ = "document_extractions"
    __table_args__ = (
        UniqueConstraint("document_id", "extractor"),
        CheckConstraint(_one_of("status", EXTRACTION_STATUS_VALUES), name="status"),
        CheckConstraint(
            "(status = 'complete') = (invoice_version_id IS NOT NULL)",
            name="invoice_only_when_complete",
        ),
        CheckConstraint("jsonb_typeof(problems) = 'array'", name="problems_array"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True)
    document_id: Mapped[UUID] = mapped_column(
        ForeignKey("source_documents.id"), index=True
    )
    extractor: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(32))
    invoice_version_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("invoice_versions.id")
    )
    problems: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = _created_at()


class ExtractedFieldRow(Base):
    """One proposed field, its status and the spans that support it."""

    __tablename__ = "extracted_fields"
    __table_args__ = (
        UniqueConstraint("extraction_id", "name"),
        CheckConstraint(_one_of("status", FIELD_STATUS_VALUES), name="status"),
        CheckConstraint("position >= 0", name="position_nonnegative"),
        CheckConstraint("jsonb_typeof(evidence) = 'array'", name="evidence_array"),
    )

    extraction_id: Mapped[UUID] = mapped_column(
        ForeignKey("document_extractions.id"), primary_key=True
    )
    position: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16))
    value: Mapped[str | None] = mapped_column(String(500))
    message: Mapped[str | None] = mapped_column(String(500))
    evidence: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)


class ReconciliationRuleVersionRow(Base):
    """One immutable reconciliation rule set; tolerances are child rows."""

    __tablename__ = "reconciliation_rule_versions"
    __table_args__ = (
        UniqueConstraint("rules_id", "version_no"),
        CheckConstraint("version_no > 0", name="version_no_positive"),
        CheckConstraint(f"content_hash ~ {HASH_PATTERN}", name="content_hash_hex"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True)
    rules_id: Mapped[str] = mapped_column(String(128))
    version_no: Mapped[int] = mapped_column(Integer)
    content_hash: Mapped[str] = mapped_column(String(64))
    allow_partial_quantities: Mapped[bool] = mapped_column(Boolean)
    require_source_document: Mapped[bool] = mapped_column(Boolean)
    created_at: Mapped[datetime] = _created_at()


class ReconciliationCurrencyRuleRow(Base):
    """Tolerances for one currency; rounding is limited to what the engine does."""

    __tablename__ = "reconciliation_currency_rules"
    __table_args__ = (
        CheckConstraint(_one_of("currency", CURRENCY_VALUES), name="currency"),
        CheckConstraint("unit_price_tolerance >= 0", name="unit_tolerance_nonnegative"),
        CheckConstraint("total_tolerance >= 0", name="total_tolerance_nonnegative"),
        CheckConstraint(
            f"decimal_places = {SUPPORTED_DECIMAL_PLACES} "
            f"AND rounding = '{SUPPORTED_ROUNDING}'",
            name="supported_rounding",
        ),
    )

    rules_version_id: Mapped[UUID] = mapped_column(
        ForeignKey("reconciliation_rule_versions.id"), primary_key=True
    )
    currency: Mapped[str] = mapped_column(String(3), primary_key=True)
    unit_price_tolerance: Mapped[Decimal] = mapped_column(MONEY)
    total_tolerance: Mapped[Decimal] = mapped_column(MONEY)
    decimal_places: Mapped[int] = mapped_column(Integer)
    rounding: Mapped[str] = mapped_column(String(32))


class ReconciliationSnapshotRow(Base):
    """An immutable reconciliation result with every input version and hash."""

    __tablename__ = "reconciliation_snapshots"
    __table_args__ = (
        UniqueConstraint("workflow_id", "inputs_hash"),
        CheckConstraint(f"inputs_hash ~ {HASH_PATTERN}", name="inputs_hash_hex"),
        CheckConstraint("workflow_version > 0", name="workflow_version_positive"),
        CheckConstraint(
            "(purchase_order_version_id IS NULL) "
            "= (purchase_order_content_hash IS NULL)",
            name="purchase_order_pair",
        ),
        CheckConstraint(
            "(source_document_id IS NULL) = (document_content_hash IS NULL)",
            name="document_pair",
        ),
        CheckConstraint(
            "(extraction_id IS NULL) = (fields_hash IS NULL)", name="extraction_pair"
        ),
        CheckConstraint(_one_of("status", RECONCILIATION_STATUS_VALUES), name="status"),
        CheckConstraint(_one_of("currency", CURRENCY_VALUES), name="currency"),
        CheckConstraint(
            "jsonb_typeof(discrepancies) = 'array' AND jsonb_typeof(lines) = 'array'",
            name="json_arrays",
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True)
    workflow_id: Mapped[str] = mapped_column(
        ForeignKey("workflows.workflow_id"), index=True
    )
    workflow_version: Mapped[int] = mapped_column(Integer)
    inputs_hash: Mapped[str] = mapped_column(String(64))
    engine: Mapped[str] = mapped_column(String(64))
    invoice_version_id: Mapped[UUID] = mapped_column(ForeignKey("invoice_versions.id"))
    invoice_content_hash: Mapped[str] = mapped_column(String(64))
    purchase_order_version_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("purchase_order_versions.id")
    )
    purchase_order_content_hash: Mapped[str | None] = mapped_column(String(64))
    rules_version_id: Mapped[UUID] = mapped_column(
        ForeignKey("reconciliation_rule_versions.id")
    )
    rules_content_hash: Mapped[str] = mapped_column(String(64))
    source_document_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("source_documents.id")
    )
    document_content_hash: Mapped[str | None] = mapped_column(String(64))
    extraction_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("document_extractions.id")
    )
    fields_hash: Mapped[str | None] = mapped_column(String(64))
    duplicate_workflow_ids: Mapped[list[str]] = mapped_column(ARRAY(String(128)))
    status: Mapped[str] = mapped_column(String(32))
    currency: Mapped[str] = mapped_column(String(3))
    calculated_total: Mapped[Decimal] = mapped_column(MONEY)
    expected_total: Mapped[Decimal | None] = mapped_column(MONEY)
    order_total: Mapped[Decimal | None] = mapped_column(MONEY)
    discrepancies: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)
    lines: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = _created_at()


class UserRow(Base):
    """An employee: roles and business units; can be deactivated."""

    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint(
            "cardinality(roles) > 0 AND roles <@ ARRAY["
            + ", ".join(repr(value) for value in ROLE_VALUES)
            + "]::varchar[]",
            name="roles_known",
        ),
        CheckConstraint("cardinality(units) > 0", name="units_nonempty"),
    )

    user_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    display_name: Mapped[str] = mapped_column(String(200))
    roles: Mapped[list[str]] = mapped_column(ARRAY(String(32)))
    units: Mapped[list[str]] = mapped_column(ARRAY(String(64)))
    active: Mapped[bool] = mapped_column(Boolean)
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = _created_at()


class ApiTokenRow(Base):
    """SHA-256 of a bearer token; the token itself is shown once and never kept."""

    __tablename__ = "api_tokens"
    __table_args__ = (
        UniqueConstraint("token_hash"),
        CheckConstraint(f"token_hash ~ {HASH_PATTERN}", name="token_hash_hex"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.user_id"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64))
    label: Mapped[str] = mapped_column(String(100))
    created_at: Mapped[datetime] = _created_at()
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class OutboxEventRow(Base):
    """An event written with a decision; delivery attempts arrive in Stage 09."""

    __tablename__ = "outbox_events"
    __table_args__ = (
        CheckConstraint(_one_of("event_type", OUTBOX_EVENT_VALUES), name="event_type"),
        CheckConstraint("workflow_version > 0", name="workflow_version_positive"),
        CheckConstraint("jsonb_typeof(payload) = 'object'", name="payload_object"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True)
    event_type: Mapped[str] = mapped_column(String(64))
    workflow_id: Mapped[str] = mapped_column(
        ForeignKey("workflows.workflow_id"), index=True
    )
    workflow_version: Mapped[int] = mapped_column(Integer)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = _created_at()


# Rows that must never change after insertion; a database trigger enforces this.
APPEND_ONLY_TABLES = (
    "invoice_versions",
    "invoice_version_lines",
    "purchase_order_versions",
    "purchase_order_version_lines",
    "policy_versions",
    "validation_snapshots",
    "audit_events",
    "idempotency_records",
    "source_documents",
    "document_pages",
    "document_extractions",
    "extracted_fields",
    "reconciliation_rule_versions",
    "reconciliation_currency_rules",
    "reconciliation_snapshots",
    "outbox_events",
)
