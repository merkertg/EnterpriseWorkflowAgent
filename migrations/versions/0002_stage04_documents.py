"""Stage 04: source documents, page texts, extractions and extracted fields.

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-23

Generated with Alembic autogenerate, then edited by hand to add append-only
triggers. They reuse reject_append_only_change() from revision 0001.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002"
down_revision: str | Sequence[str] | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# Frozen copy of this revision's append-only tables.
APPEND_ONLY_TABLES = (
    "source_documents",
    "document_pages",
    "document_extractions",
    "extracted_fields",
)


def upgrade() -> None:
    """Create document storage tables and protect them from edits."""
    op.create_table(
        "source_documents",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("media_type", sa.String(length=32), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("page_count", sa.Integer(), nullable=False),
        sa.Column("declared_name", sa.String(length=255), nullable=True),
        sa.Column("content", sa.LargeBinary(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "content_hash ~ '^[0-9a-f]{64}$'",
            name=op.f("ck_source_documents_content_hash_hex"),
        ),
        sa.CheckConstraint(
            "media_type IN ('text/plain', 'application/pdf')",
            name=op.f("ck_source_documents_media_type"),
        ),
        sa.CheckConstraint(
            "page_count > 0", name=op.f("ck_source_documents_page_count_positive")
        ),
        sa.CheckConstraint(
            "size_bytes > 0 AND size_bytes = octet_length(content)",
            name=op.f("ck_source_documents_size_matches_content"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_source_documents")),
        sa.UniqueConstraint(
            "content_hash", name=op.f("uq_source_documents_content_hash")
        ),
    )
    op.create_table(
        "document_extractions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("extractor", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("invoice_version_id", sa.Uuid(), nullable=True),
        sa.Column("problems", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "(status = 'complete') = (invoice_version_id IS NOT NULL)",
            name=op.f("ck_document_extractions_invoice_only_when_complete"),
        ),
        sa.CheckConstraint(
            "jsonb_typeof(problems) = 'array'",
            name=op.f("ck_document_extractions_problems_array"),
        ),
        sa.CheckConstraint(
            "status IN ('complete', 'needs_review')",
            name=op.f("ck_document_extractions_status"),
        ),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["source_documents.id"],
            name=op.f("fk_document_extractions_document_id"),
        ),
        sa.ForeignKeyConstraint(
            ["invoice_version_id"],
            ["invoice_versions.id"],
            name=op.f("fk_document_extractions_invoice_version_id"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_document_extractions")),
        sa.UniqueConstraint(
            "document_id",
            "extractor",
            name=op.f("uq_document_extractions_document_id_extractor"),
        ),
    )
    op.create_index(
        op.f("ix_document_extractions_document_id"),
        "document_extractions",
        ["document_id"],
        unique=False,
    )
    op.create_table(
        "document_pages",
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("page_no", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.CheckConstraint(
            "page_no > 0", name=op.f("ck_document_pages_page_no_positive")
        ),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["source_documents.id"],
            name=op.f("fk_document_pages_document_id"),
        ),
        sa.PrimaryKeyConstraint(
            "document_id", "page_no", name=op.f("pk_document_pages")
        ),
    )
    op.create_table(
        "extracted_fields",
        sa.Column("extraction_id", sa.Uuid(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("value", sa.String(length=500), nullable=True),
        sa.Column("message", sa.String(length=500), nullable=True),
        sa.Column("evidence", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.CheckConstraint(
            "jsonb_typeof(evidence) = 'array'",
            name=op.f("ck_extracted_fields_evidence_array"),
        ),
        sa.CheckConstraint(
            "status IN ('found', 'missing', 'ambiguous', 'invalid')",
            name=op.f("ck_extracted_fields_status"),
        ),
        sa.CheckConstraint(
            "position >= 0", name=op.f("ck_extracted_fields_position_nonnegative")
        ),
        sa.ForeignKeyConstraint(
            ["extraction_id"],
            ["document_extractions.id"],
            name=op.f("fk_extracted_fields_extraction_id"),
        ),
        sa.PrimaryKeyConstraint(
            "extraction_id", "position", name=op.f("pk_extracted_fields")
        ),
        sa.UniqueConstraint(
            "extraction_id", "name", name=op.f("uq_extracted_fields_extraction_id_name")
        ),
    )
    for table in APPEND_ONLY_TABLES:
        op.execute(
            f"CREATE TRIGGER trg_{table}_append_only "
            f"BEFORE UPDATE OR DELETE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION reject_append_only_change()"
        )


def downgrade() -> None:
    """Drop document storage; this deletes every stored document."""
    for table in APPEND_ONLY_TABLES:
        op.execute(f"DROP TRIGGER trg_{table}_append_only ON {table}")
    op.drop_table("extracted_fields")
    op.drop_table("document_pages")
    op.drop_index(
        op.f("ix_document_extractions_document_id"), table_name="document_extractions"
    )
    op.drop_table("document_extractions")
    op.drop_table("source_documents")
