"""Stage 05: reconciliation rule versions, currency rules and snapshots.

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-23

Generated with Alembic autogenerate, then edited by hand to add append-only
triggers. They reuse reject_append_only_change() from revision 0001.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003"
down_revision: str | Sequence[str] | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# Frozen copy of this revision's append-only tables.
APPEND_ONLY_TABLES = (
    "reconciliation_rule_versions",
    "reconciliation_currency_rules",
    "reconciliation_snapshots",
)


def upgrade() -> None:
    """Create reconciliation storage and protect it from edits."""
    op.create_table(
        "reconciliation_rule_versions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("rules_id", sa.String(length=128), nullable=False),
        sa.Column("version_no", sa.Integer(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("allow_partial_quantities", sa.Boolean(), nullable=False),
        sa.Column("require_source_document", sa.Boolean(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "content_hash ~ '^[0-9a-f]{64}$'",
            name=op.f("ck_reconciliation_rule_versions_content_hash_hex"),
        ),
        sa.CheckConstraint(
            "version_no > 0",
            name=op.f("ck_reconciliation_rule_versions_version_no_positive"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_reconciliation_rule_versions")),
        sa.UniqueConstraint(
            "rules_id",
            "version_no",
            name=op.f("uq_reconciliation_rule_versions_rules_id_version_no"),
        ),
    )
    op.create_table(
        "reconciliation_currency_rules",
        sa.Column("rules_version_id", sa.Uuid(), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column(
            "unit_price_tolerance", sa.Numeric(precision=14, scale=2), nullable=False
        ),
        sa.Column("total_tolerance", sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column("decimal_places", sa.Integer(), nullable=False),
        sa.Column("rounding", sa.String(length=32), nullable=False),
        sa.CheckConstraint(
            "currency IN ('USD', 'EUR', 'UZS')",
            name=op.f("ck_reconciliation_currency_rules_currency"),
        ),
        sa.CheckConstraint(
            "decimal_places = 2 AND rounding = 'ROUND_HALF_UP'",
            name=op.f("ck_reconciliation_currency_rules_supported_rounding"),
        ),
        sa.CheckConstraint(
            "total_tolerance >= 0",
            name=op.f("ck_reconciliation_currency_rules_total_tolerance_nonnegative"),
        ),
        sa.CheckConstraint(
            "unit_price_tolerance >= 0",
            name=op.f("ck_reconciliation_currency_rules_unit_tolerance_nonnegative"),
        ),
        sa.ForeignKeyConstraint(
            ["rules_version_id"],
            ["reconciliation_rule_versions.id"],
            name=op.f("fk_reconciliation_currency_rules_rules_version_id"),
        ),
        sa.PrimaryKeyConstraint(
            "rules_version_id",
            "currency",
            name=op.f("pk_reconciliation_currency_rules"),
        ),
    )
    op.create_table(
        "reconciliation_snapshots",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workflow_id", sa.String(length=128), nullable=False),
        sa.Column("workflow_version", sa.Integer(), nullable=False),
        sa.Column("inputs_hash", sa.String(length=64), nullable=False),
        sa.Column("engine", sa.String(length=64), nullable=False),
        sa.Column("invoice_version_id", sa.Uuid(), nullable=False),
        sa.Column("invoice_content_hash", sa.String(length=64), nullable=False),
        sa.Column("purchase_order_version_id", sa.Uuid(), nullable=True),
        sa.Column("purchase_order_content_hash", sa.String(length=64), nullable=True),
        sa.Column("rules_version_id", sa.Uuid(), nullable=False),
        sa.Column("rules_content_hash", sa.String(length=64), nullable=False),
        sa.Column("source_document_id", sa.Uuid(), nullable=True),
        sa.Column("document_content_hash", sa.String(length=64), nullable=True),
        sa.Column("extraction_id", sa.Uuid(), nullable=True),
        sa.Column("fields_hash", sa.String(length=64), nullable=True),
        sa.Column(
            "duplicate_workflow_ids",
            postgresql.ARRAY(sa.String(length=128)),
            nullable=False,
        ),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column(
            "calculated_total", sa.Numeric(precision=14, scale=2), nullable=False
        ),
        sa.Column("expected_total", sa.Numeric(precision=14, scale=2), nullable=True),
        sa.Column("order_total", sa.Numeric(precision=14, scale=2), nullable=True),
        sa.Column(
            "discrepancies", postgresql.JSONB(astext_type=sa.Text()), nullable=False
        ),
        sa.Column("lines", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "currency IN ('USD', 'EUR', 'UZS')",
            name=op.f("ck_reconciliation_snapshots_currency"),
        ),
        sa.CheckConstraint(
            "inputs_hash ~ '^[0-9a-f]{64}$'",
            name=op.f("ck_reconciliation_snapshots_inputs_hash_hex"),
        ),
        sa.CheckConstraint(
            "jsonb_typeof(discrepancies) = 'array' AND jsonb_typeof(lines) = 'array'",
            name=op.f("ck_reconciliation_snapshots_json_arrays"),
        ),
        sa.CheckConstraint(
            "status IN ('matched', 'needs_review')",
            name=op.f("ck_reconciliation_snapshots_status"),
        ),
        sa.CheckConstraint(
            "(extraction_id IS NULL) = (fields_hash IS NULL)",
            name=op.f("ck_reconciliation_snapshots_extraction_pair"),
        ),
        sa.CheckConstraint(
            "(purchase_order_version_id IS NULL) "
            "= (purchase_order_content_hash IS NULL)",
            name=op.f("ck_reconciliation_snapshots_purchase_order_pair"),
        ),
        sa.CheckConstraint(
            "(source_document_id IS NULL) = (document_content_hash IS NULL)",
            name=op.f("ck_reconciliation_snapshots_document_pair"),
        ),
        sa.CheckConstraint(
            "workflow_version > 0",
            name=op.f("ck_reconciliation_snapshots_workflow_version_positive"),
        ),
        sa.ForeignKeyConstraint(
            ["extraction_id"],
            ["document_extractions.id"],
            name=op.f("fk_reconciliation_snapshots_extraction_id"),
        ),
        sa.ForeignKeyConstraint(
            ["invoice_version_id"],
            ["invoice_versions.id"],
            name=op.f("fk_reconciliation_snapshots_invoice_version_id"),
        ),
        sa.ForeignKeyConstraint(
            ["purchase_order_version_id"],
            ["purchase_order_versions.id"],
            name=op.f("fk_reconciliation_snapshots_purchase_order_version_id"),
        ),
        sa.ForeignKeyConstraint(
            ["rules_version_id"],
            ["reconciliation_rule_versions.id"],
            name=op.f("fk_reconciliation_snapshots_rules_version_id"),
        ),
        sa.ForeignKeyConstraint(
            ["source_document_id"],
            ["source_documents.id"],
            name=op.f("fk_reconciliation_snapshots_source_document_id"),
        ),
        sa.ForeignKeyConstraint(
            ["workflow_id"],
            ["workflows.workflow_id"],
            name=op.f("fk_reconciliation_snapshots_workflow_id"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_reconciliation_snapshots")),
        sa.UniqueConstraint(
            "workflow_id",
            "inputs_hash",
            name=op.f("uq_reconciliation_snapshots_workflow_id_inputs_hash"),
        ),
    )
    op.create_index(
        op.f("ix_reconciliation_snapshots_workflow_id"),
        "reconciliation_snapshots",
        ["workflow_id"],
        unique=False,
    )
    for table in APPEND_ONLY_TABLES:
        op.execute(
            f"CREATE TRIGGER trg_{table}_append_only "
            f"BEFORE UPDATE OR DELETE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION reject_append_only_change()"
        )


def downgrade() -> None:
    """Drop reconciliation storage; this deletes every stored snapshot."""
    for table in APPEND_ONLY_TABLES:
        op.execute(f"DROP TRIGGER trg_{table}_append_only ON {table}")
    op.drop_index(
        op.f("ix_reconciliation_snapshots_workflow_id"),
        table_name="reconciliation_snapshots",
    )
    op.drop_table("reconciliation_snapshots")
    op.drop_table("reconciliation_currency_rules")
    op.drop_table("reconciliation_rule_versions")
