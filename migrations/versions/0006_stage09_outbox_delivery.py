"""Stage 09: outbox delivery state and the attempt log.

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-24

Generated with Alembic autogenerate, then edited by hand:

- ``outbox_deliveries`` holds one mutable row per event. The worker claims
  due rows with ``FOR UPDATE SKIP LOCKED``.
- ``outbox_attempts`` is append-only and reuses reject_append_only_change()
  from revision 0001. It stores codes and timing, never the request body.
- Events written before this revision get a pending delivery row, so the
  worker delivers them too.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: str | Sequence[str] | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# Frozen copy of this revision's append-only tables.
APPEND_ONLY_TABLES = ("outbox_attempts",)


def upgrade() -> None:
    """Create delivery storage, protect the log and backfill old events."""
    op.create_table(
        "outbox_deliveries",
        sa.Column("event_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column(
            "attempts", sa.Integer(), server_default=sa.text("0"), nullable=False
        ),
        sa.Column(
            "total_attempts", sa.Integer(), server_default=sa.text("0"), nullable=False
        ),
        sa.Column(
            "requeues", sa.Integer(), server_default=sa.text("0"), nullable=False
        ),
        sa.Column(
            "next_attempt_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("last_error", sa.String(length=64), nullable=True),
        sa.Column("last_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'retry_scheduled', 'delivered', 'exhausted')",
            name=op.f("ck_outbox_deliveries_status"),
        ),
        sa.CheckConstraint(
            "attempts >= 0 AND total_attempts >= attempts AND requeues >= 0",
            name=op.f("ck_outbox_deliveries_counts_valid"),
        ),
        sa.CheckConstraint(
            "(status = 'delivered') = (delivered_at IS NOT NULL)",
            name=op.f("ck_outbox_deliveries_delivered_at_matches_status"),
        ),
        sa.ForeignKeyConstraint(
            ["event_id"],
            ["outbox_events.id"],
            name=op.f("fk_outbox_deliveries_event_id"),
        ),
        sa.PrimaryKeyConstraint("event_id", name=op.f("pk_outbox_deliveries")),
    )
    op.create_index(
        op.f("ix_outbox_deliveries_status"),
        "outbox_deliveries",
        ["status"],
        unique=False,
    )
    op.create_table(
        "outbox_attempts",
        sa.Column("event_id", sa.Uuid(), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("duration_ms", sa.Integer(), nullable=False),
        sa.Column("outcome", sa.String(length=16), nullable=False),
        sa.Column("http_status", sa.Integer(), nullable=True),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("duplicate", sa.Boolean(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "outcome IN ('delivered', 'failed')",
            name=op.f("ck_outbox_attempts_outcome"),
        ),
        sa.CheckConstraint("seq > 0", name=op.f("ck_outbox_attempts_seq_positive")),
        sa.CheckConstraint(
            "duration_ms >= 0", name=op.f("ck_outbox_attempts_duration_non_negative")
        ),
        sa.CheckConstraint(
            "http_status IS NULL OR http_status BETWEEN 100 AND 599",
            name=op.f("ck_outbox_attempts_http_status_range"),
        ),
        sa.CheckConstraint(
            "(outcome = 'delivered') = (error_code IS NULL)",
            name=op.f("ck_outbox_attempts_error_code_matches_outcome"),
        ),
        sa.ForeignKeyConstraint(
            ["event_id"],
            ["outbox_events.id"],
            name=op.f("fk_outbox_attempts_event_id"),
        ),
        sa.PrimaryKeyConstraint("event_id", "seq", name=op.f("pk_outbox_attempts")),
    )
    for table in APPEND_ONLY_TABLES:
        op.execute(
            f"CREATE TRIGGER trg_{table}_append_only "
            f"BEFORE UPDATE OR DELETE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION reject_append_only_change()"
        )
    op.execute(
        "INSERT INTO outbox_deliveries (event_id, status, next_attempt_at) "
        "SELECT id, 'pending', created_at FROM outbox_events"
    )


def downgrade() -> None:
    """Remove delivery storage; states and attempt logs are lost, events stay."""
    for table in APPEND_ONLY_TABLES:
        op.execute(f"DROP TRIGGER trg_{table}_append_only ON {table}")
    op.drop_table("outbox_attempts")
    op.drop_index(op.f("ix_outbox_deliveries_status"), table_name="outbox_deliveries")
    op.drop_table("outbox_deliveries")
