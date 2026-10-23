"""Stage 03: immutable input versions, workflows, snapshots, audit, idempotency.

Revision ID: 0001
Revises:
Create Date: 2026-10-23

Generated with Alembic autogenerate, then reviewed by hand: the circular
workflow-to-snapshot foreign key is added after both tables exist, and
append-only tables receive a trigger that rejects UPDATE and DELETE.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


STATES = "('received', 'needs_review', 'awaiting_approval', 'approved', 'rejected')"

# Frozen copy of the append-only tables at this revision; later code may differ.
APPEND_ONLY_TABLES = (
    "invoice_versions",
    "invoice_version_lines",
    "purchase_order_versions",
    "purchase_order_version_lines",
    "policy_versions",
    "validation_snapshots",
    "audit_events",
    "idempotency_records",
)


def upgrade() -> None:
    """Create all Stage 03 tables, constraints and append-only triggers."""
    op.create_table(
        "idempotency_records",
        sa.Column("key", sa.String(length=128), nullable=False),
        sa.Column("command", sa.String(length=32), nullable=False),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column("workflow_id", sa.String(length=128), nullable=False),
        sa.Column("result_state", sa.String(length=32), nullable=False),
        sa.Column("result_version", sa.Integer(), nullable=False),
        sa.Column("snapshot_id", sa.Uuid(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "command IN ('start', 'check', 'approve', 'reject', 'revise')",
            name=op.f("ck_idempotency_records_command"),
        ),
        sa.CheckConstraint(
            "request_hash ~ '^[0-9a-f]{64}$'",
            name=op.f("ck_idempotency_records_request_hash_hex"),
        ),
        sa.CheckConstraint(
            f"result_state IN {STATES}",
            name=op.f("ck_idempotency_records_result_state"),
        ),
        sa.CheckConstraint(
            "result_version > 0",
            name=op.f("ck_idempotency_records_result_version_positive"),
        ),
        sa.PrimaryKeyConstraint("key", name=op.f("pk_idempotency_records")),
    )
    op.create_table(
        "invoice_versions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("invoice_id", sa.String(length=128), nullable=False),
        sa.Column("version_no", sa.Integer(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("supplier_id", sa.String(length=128), nullable=False),
        sa.Column("purchase_order_id", sa.String(length=128), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("declared_total", sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "content_hash ~ '^[0-9a-f]{64}$'",
            name=op.f("ck_invoice_versions_content_hash_hex"),
        ),
        sa.CheckConstraint(
            "currency IN ('USD', 'EUR', 'UZS')",
            name=op.f("ck_invoice_versions_currency"),
        ),
        sa.CheckConstraint(
            "declared_total >= 0",
            name=op.f("ck_invoice_versions_declared_total_nonnegative"),
        ),
        sa.CheckConstraint(
            "version_no > 0", name=op.f("ck_invoice_versions_version_no_positive")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_invoice_versions")),
        sa.UniqueConstraint(
            "invoice_id",
            "version_no",
            name=op.f("uq_invoice_versions_invoice_id_version_no"),
        ),
    )
    op.create_table(
        "policy_versions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("policy_id", sa.String(length=128), nullable=False),
        sa.Column("version_no", sa.Integer(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "allowed_currencies", postgresql.ARRAY(sa.String(length=3)), nullable=False
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "allowed_currencies <@ ARRAY['USD', 'EUR', 'UZS']::varchar[]",
            name=op.f("ck_policy_versions_allowed_currencies_known"),
        ),
        sa.CheckConstraint(
            "content_hash ~ '^[0-9a-f]{64}$'",
            name=op.f("ck_policy_versions_content_hash_hex"),
        ),
        sa.CheckConstraint(
            "cardinality(allowed_currencies) > 0",
            name=op.f("ck_policy_versions_allowed_currencies_nonempty"),
        ),
        sa.CheckConstraint(
            "version_no > 0", name=op.f("ck_policy_versions_version_no_positive")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_policy_versions")),
        sa.UniqueConstraint(
            "policy_id",
            "version_no",
            name=op.f("uq_policy_versions_policy_id_version_no"),
        ),
    )
    op.create_table(
        "purchase_order_versions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("purchase_order_id", sa.String(length=128), nullable=False),
        sa.Column("version_no", sa.Integer(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("supplier_id", sa.String(length=128), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "content_hash ~ '^[0-9a-f]{64}$'",
            name=op.f("ck_purchase_order_versions_content_hash_hex"),
        ),
        sa.CheckConstraint(
            "currency IN ('USD', 'EUR', 'UZS')",
            name=op.f("ck_purchase_order_versions_currency"),
        ),
        sa.CheckConstraint(
            "version_no > 0",
            name=op.f("ck_purchase_order_versions_version_no_positive"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_purchase_order_versions")),
        sa.UniqueConstraint(
            "purchase_order_id",
            "version_no",
            name=op.f("uq_purchase_order_versions_purchase_order_id_version_no"),
        ),
    )
    op.create_table(
        "invoice_version_lines",
        sa.Column("invoice_version_id", sa.Uuid(), nullable=False),
        sa.Column("line_no", sa.Integer(), nullable=False),
        sa.Column("sku", sa.String(length=128), nullable=False),
        sa.Column("description", sa.String(length=500), nullable=False),
        sa.Column("quantity", sa.Numeric(precision=12, scale=6), nullable=False),
        sa.Column("unit_price", sa.Numeric(precision=14, scale=2), nullable=False),
        sa.CheckConstraint(
            "line_no > 0", name=op.f("ck_invoice_version_lines_line_no_positive")
        ),
        sa.CheckConstraint(
            "quantity > 0", name=op.f("ck_invoice_version_lines_quantity_positive")
        ),
        sa.CheckConstraint(
            "unit_price >= 0",
            name=op.f("ck_invoice_version_lines_unit_price_nonnegative"),
        ),
        sa.ForeignKeyConstraint(
            ["invoice_version_id"],
            ["invoice_versions.id"],
            name=op.f("fk_invoice_version_lines_invoice_version_id"),
        ),
        sa.PrimaryKeyConstraint(
            "invoice_version_id", "line_no", name=op.f("pk_invoice_version_lines")
        ),
        sa.UniqueConstraint(
            "invoice_version_id",
            "sku",
            name=op.f("uq_invoice_version_lines_invoice_version_id_sku"),
        ),
    )
    op.create_table(
        "purchase_order_version_lines",
        sa.Column("purchase_order_version_id", sa.Uuid(), nullable=False),
        sa.Column("line_no", sa.Integer(), nullable=False),
        sa.Column("sku", sa.String(length=128), nullable=False),
        sa.Column("description", sa.String(length=500), nullable=False),
        sa.Column("quantity", sa.Numeric(precision=12, scale=6), nullable=False),
        sa.Column("unit_price", sa.Numeric(precision=14, scale=2), nullable=False),
        sa.CheckConstraint(
            "line_no > 0", name=op.f("ck_purchase_order_version_lines_line_no_positive")
        ),
        sa.CheckConstraint(
            "quantity > 0",
            name=op.f("ck_purchase_order_version_lines_quantity_positive"),
        ),
        sa.CheckConstraint(
            "unit_price >= 0",
            name=op.f("ck_purchase_order_version_lines_unit_price_nonnegative"),
        ),
        sa.ForeignKeyConstraint(
            ["purchase_order_version_id"],
            ["purchase_order_versions.id"],
            name=op.f("fk_purchase_order_version_lines_purchase_order_version_id"),
        ),
        sa.PrimaryKeyConstraint(
            "purchase_order_version_id",
            "line_no",
            name=op.f("pk_purchase_order_version_lines"),
        ),
        sa.UniqueConstraint(
            "purchase_order_version_id",
            "sku",
            name=op.f("uq_purchase_order_version_lines_purchase_order_version_id_sku"),
        ),
    )
    op.create_table(
        "workflows",
        sa.Column("workflow_id", sa.String(length=128), nullable=False),
        sa.Column("invoice_id", sa.String(length=128), nullable=False),
        sa.Column("state", sa.String(length=32), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("invoice_version_id", sa.Uuid(), nullable=False),
        sa.Column("purchase_order_version_id", sa.Uuid(), nullable=True),
        sa.Column("policy_version_id", sa.Uuid(), nullable=False),
        sa.Column("last_snapshot_id", sa.Uuid(), nullable=True),
        sa.Column("decision_actor_id", sa.String(length=128), nullable=True),
        sa.Column("decision_reason", sa.String(length=2000), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            f"state IN {STATES}",
            name=op.f("ck_workflows_state"),
        ),
        sa.CheckConstraint(
            "(decision_actor_id IS NULL) = (decision_reason IS NULL)",
            name=op.f("ck_workflows_decision_complete"),
        ),
        sa.CheckConstraint("version > 0", name=op.f("ck_workflows_version_positive")),
        sa.ForeignKeyConstraint(
            ["invoice_version_id"],
            ["invoice_versions.id"],
            name=op.f("fk_workflows_invoice_version_id"),
        ),
        sa.ForeignKeyConstraint(
            ["policy_version_id"],
            ["policy_versions.id"],
            name=op.f("fk_workflows_policy_version_id"),
        ),
        sa.ForeignKeyConstraint(
            ["purchase_order_version_id"],
            ["purchase_order_versions.id"],
            name=op.f("fk_workflows_purchase_order_version_id"),
        ),
        sa.PrimaryKeyConstraint("workflow_id", name=op.f("pk_workflows")),
    )
    op.create_index(
        op.f("ix_workflows_invoice_id"), "workflows", ["invoice_id"], unique=False
    )
    op.create_table(
        "validation_snapshots",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workflow_id", sa.String(length=128), nullable=False),
        sa.Column("workflow_version", sa.Integer(), nullable=False),
        sa.Column("invoice_version_id", sa.Uuid(), nullable=False),
        sa.Column("invoice_content_hash", sa.String(length=64), nullable=False),
        sa.Column("purchase_order_version_id", sa.Uuid(), nullable=True),
        sa.Column("purchase_order_content_hash", sa.String(length=64), nullable=True),
        sa.Column("policy_version_id", sa.Uuid(), nullable=False),
        sa.Column("policy_content_hash", sa.String(length=64), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column(
            "calculated_total", sa.Numeric(precision=14, scale=2), nullable=False
        ),
        sa.Column("issues", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "currency IN ('USD', 'EUR', 'UZS')",
            name=op.f("ck_validation_snapshots_currency"),
        ),
        sa.CheckConstraint(
            "jsonb_typeof(issues) = 'array'",
            name=op.f("ck_validation_snapshots_issues_array"),
        ),
        sa.CheckConstraint(
            "(purchase_order_version_id IS NULL) "
            "= (purchase_order_content_hash IS NULL)",
            name=op.f("ck_validation_snapshots_purchase_order_pair"),
        ),
        sa.CheckConstraint(
            "calculated_total >= 0",
            name=op.f("ck_validation_snapshots_calculated_total_nonnegative"),
        ),
        sa.CheckConstraint(
            "workflow_version > 0",
            name=op.f("ck_validation_snapshots_workflow_version_positive"),
        ),
        sa.ForeignKeyConstraint(
            ["invoice_version_id"],
            ["invoice_versions.id"],
            name=op.f("fk_validation_snapshots_invoice_version_id"),
        ),
        sa.ForeignKeyConstraint(
            ["policy_version_id"],
            ["policy_versions.id"],
            name=op.f("fk_validation_snapshots_policy_version_id"),
        ),
        sa.ForeignKeyConstraint(
            ["purchase_order_version_id"],
            ["purchase_order_versions.id"],
            name=op.f("fk_validation_snapshots_purchase_order_version_id"),
        ),
        sa.ForeignKeyConstraint(
            ["workflow_id"],
            ["workflows.workflow_id"],
            name=op.f("fk_validation_snapshots_workflow_id"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_validation_snapshots")),
    )
    op.create_index(
        op.f("ix_validation_snapshots_workflow_id"),
        "validation_snapshots",
        ["workflow_id"],
        unique=False,
    )
    op.create_table(
        "audit_events",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("workflow_id", sa.String(length=128), nullable=False),
        sa.Column("command", sa.String(length=32), nullable=False),
        sa.Column("previous_state", sa.String(length=32), nullable=True),
        sa.Column("new_state", sa.String(length=32), nullable=False),
        sa.Column("workflow_version", sa.Integer(), nullable=False),
        sa.Column("invoice_version_id", sa.Uuid(), nullable=False),
        sa.Column("purchase_order_version_id", sa.Uuid(), nullable=True),
        sa.Column("policy_version_id", sa.Uuid(), nullable=False),
        sa.Column("snapshot_id", sa.Uuid(), nullable=True),
        sa.Column(
            "issue_codes",
            postgresql.ARRAY(sa.String(length=64)),
            server_default=sa.text("'{}'"),
            nullable=False,
        ),
        sa.Column("actor_id", sa.String(length=128), nullable=True),
        sa.Column("source", sa.String(length=64), nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column(
            "occurred_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "command IN ('start', 'check', 'approve', 'reject', 'revise')",
            name=op.f("ck_audit_events_command"),
        ),
        sa.CheckConstraint(
            f"new_state IN {STATES}",
            name=op.f("ck_audit_events_new_state"),
        ),
        sa.CheckConstraint(
            f"previous_state IS NULL OR previous_state IN {STATES}",
            name=op.f("ck_audit_events_previous_state"),
        ),
        sa.CheckConstraint(
            "workflow_version > 0",
            name=op.f("ck_audit_events_workflow_version_positive"),
        ),
        sa.ForeignKeyConstraint(
            ["invoice_version_id"],
            ["invoice_versions.id"],
            name=op.f("fk_audit_events_invoice_version_id"),
        ),
        sa.ForeignKeyConstraint(
            ["policy_version_id"],
            ["policy_versions.id"],
            name=op.f("fk_audit_events_policy_version_id"),
        ),
        sa.ForeignKeyConstraint(
            ["purchase_order_version_id"],
            ["purchase_order_versions.id"],
            name=op.f("fk_audit_events_purchase_order_version_id"),
        ),
        sa.ForeignKeyConstraint(
            ["snapshot_id"],
            ["validation_snapshots.id"],
            name=op.f("fk_audit_events_snapshot_id"),
        ),
        sa.ForeignKeyConstraint(
            ["workflow_id"],
            ["workflows.workflow_id"],
            name=op.f("fk_audit_events_workflow_id"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_audit_events")),
    )
    op.create_index(
        op.f("ix_audit_events_workflow_id"),
        "audit_events",
        ["workflow_id"],
        unique=False,
    )
    op.create_foreign_key(
        op.f("fk_workflows_last_snapshot_id"),
        "workflows",
        "validation_snapshots",
        ["last_snapshot_id"],
        ["id"],
    )
    # Versions, snapshots, audit and idempotency results are history: no edits.
    op.execute(
        """
        CREATE FUNCTION reject_append_only_change() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'table % is append-only', TG_TABLE_NAME
                USING ERRCODE = 'restrict_violation';
        END;
        $$
        """
    )
    for table in APPEND_ONLY_TABLES:
        op.execute(
            f"CREATE TRIGGER trg_{table}_append_only "
            f"BEFORE UPDATE OR DELETE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION reject_append_only_change()"
        )


def downgrade() -> None:
    """Drop every Stage 03 object; this deletes all stored workflow data."""
    for table in APPEND_ONLY_TABLES:
        op.execute(f"DROP TRIGGER trg_{table}_append_only ON {table}")
    op.execute("DROP FUNCTION reject_append_only_change()")
    op.drop_constraint(
        op.f("fk_workflows_last_snapshot_id"), "workflows", type_="foreignkey"
    )
    op.drop_index(op.f("ix_audit_events_workflow_id"), table_name="audit_events")
    op.drop_table("audit_events")
    op.drop_index(
        op.f("ix_validation_snapshots_workflow_id"), table_name="validation_snapshots"
    )
    op.drop_table("validation_snapshots")
    op.drop_index(op.f("ix_workflows_invoice_id"), table_name="workflows")
    op.drop_table("workflows")
    op.drop_table("purchase_order_version_lines")
    op.drop_table("invoice_version_lines")
    op.drop_table("purchase_order_versions")
    op.drop_table("policy_versions")
    op.drop_table("invoice_versions")
    op.drop_table("idempotency_records")
