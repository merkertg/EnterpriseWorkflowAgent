"""Stage 06: employees, API tokens, business units, decisions and the outbox.

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-23

Generated with Alembic autogenerate, then edited by hand:

- ``workflows.business_unit`` is added with the temporary server default
  ``'unassigned'`` so existing rows get a value; the default is then dropped,
  and new workflows must name their unit explicitly.
- ``outbox_events`` receives the append-only trigger from revision 0001.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004"
down_revision: str | Sequence[str] | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# Frozen copy of this revision's append-only tables.
APPEND_ONLY_TABLES = ("outbox_events",)


def upgrade() -> None:
    """Create identity and outbox storage and link decisions to snapshots."""
    op.create_table(
        "users",
        sa.Column("user_id", sa.String(length=64), nullable=False),
        sa.Column("display_name", sa.String(length=200), nullable=False),
        sa.Column("roles", postgresql.ARRAY(sa.String(length=32)), nullable=False),
        sa.Column("units", postgresql.ARRAY(sa.String(length=64)), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
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
            "cardinality(roles) > 0 AND roles <@ "
            "ARRAY['viewer', 'clerk', 'approver']::varchar[]",
            name=op.f("ck_users_roles_known"),
        ),
        sa.CheckConstraint(
            "cardinality(units) > 0", name=op.f("ck_users_units_nonempty")
        ),
        sa.PrimaryKeyConstraint("user_id", name=op.f("pk_users")),
    )
    op.create_table(
        "api_tokens",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.String(length=64), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("label", sa.String(length=100), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "token_hash ~ '^[0-9a-f]{64}$'", name=op.f("ck_api_tokens_token_hash_hex")
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.user_id"], name=op.f("fk_api_tokens_user_id")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_api_tokens")),
        sa.UniqueConstraint("token_hash", name=op.f("uq_api_tokens_token_hash")),
    )
    op.create_index(
        op.f("ix_api_tokens_user_id"), "api_tokens", ["user_id"], unique=False
    )
    op.create_table(
        "outbox_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("workflow_id", sa.String(length=128), nullable=False),
        sa.Column("workflow_version", sa.Integer(), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "event_type IN ('invoice.approved', 'invoice.rejected')",
            name=op.f("ck_outbox_events_event_type"),
        ),
        sa.CheckConstraint(
            "jsonb_typeof(payload) = 'object'",
            name=op.f("ck_outbox_events_payload_object"),
        ),
        sa.CheckConstraint(
            "workflow_version > 0",
            name=op.f("ck_outbox_events_workflow_version_positive"),
        ),
        sa.ForeignKeyConstraint(
            ["workflow_id"],
            ["workflows.workflow_id"],
            name=op.f("fk_outbox_events_workflow_id"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_outbox_events")),
    )
    op.create_index(
        op.f("ix_outbox_events_workflow_id"),
        "outbox_events",
        ["workflow_id"],
        unique=False,
    )
    # Adding nullable columns is DDL: the append-only row trigger does not fire.
    op.add_column(
        "audit_events",
        sa.Column("reconciliation_snapshot_id", sa.Uuid(), nullable=True),
    )
    op.add_column(
        "audit_events", sa.Column("outbox_event_id", sa.Uuid(), nullable=True)
    )
    op.create_foreign_key(
        op.f("fk_audit_events_outbox_event_id"),
        "audit_events",
        "outbox_events",
        ["outbox_event_id"],
        ["id"],
    )
    op.create_foreign_key(
        op.f("fk_audit_events_reconciliation_snapshot_id"),
        "audit_events",
        "reconciliation_snapshots",
        ["reconciliation_snapshot_id"],
        ["id"],
    )
    op.add_column(
        "workflows",
        sa.Column(
            "business_unit",
            sa.String(length=64),
            nullable=False,
            server_default="unassigned",
        ),
    )
    op.alter_column("workflows", "business_unit", server_default=None)
    op.add_column(
        "workflows", sa.Column("created_by", sa.String(length=64), nullable=True)
    )
    op.add_column(
        "workflows",
        sa.Column("decision_reconciliation_id", sa.Uuid(), nullable=True),
    )
    op.create_index(
        op.f("ix_workflows_business_unit"),
        "workflows",
        ["business_unit"],
        unique=False,
    )
    op.create_foreign_key(
        op.f("fk_workflows_decision_reconciliation_id"),
        "workflows",
        "reconciliation_snapshots",
        ["decision_reconciliation_id"],
        ["id"],
    )
    for table in APPEND_ONLY_TABLES:
        op.execute(
            f"CREATE TRIGGER trg_{table}_append_only "
            f"BEFORE UPDATE OR DELETE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION reject_append_only_change()"
        )


def downgrade() -> None:
    """Remove Stage 06 storage; decisions and outbox events are lost."""
    for table in APPEND_ONLY_TABLES:
        op.execute(f"DROP TRIGGER trg_{table}_append_only ON {table}")
    op.drop_constraint(
        op.f("fk_workflows_decision_reconciliation_id"),
        "workflows",
        type_="foreignkey",
    )
    op.drop_index(op.f("ix_workflows_business_unit"), table_name="workflows")
    op.drop_column("workflows", "decision_reconciliation_id")
    op.drop_column("workflows", "created_by")
    op.drop_column("workflows", "business_unit")
    op.drop_constraint(
        op.f("fk_audit_events_reconciliation_snapshot_id"),
        "audit_events",
        type_="foreignkey",
    )
    op.drop_constraint(
        op.f("fk_audit_events_outbox_event_id"), "audit_events", type_="foreignkey"
    )
    op.drop_column("audit_events", "outbox_event_id")
    op.drop_column("audit_events", "reconciliation_snapshot_id")
    op.drop_index(op.f("ix_outbox_events_workflow_id"), table_name="outbox_events")
    op.drop_table("outbox_events")
    op.drop_index(op.f("ix_api_tokens_user_id"), table_name="api_tokens")
    op.drop_table("api_tokens")
    op.drop_table("users")
