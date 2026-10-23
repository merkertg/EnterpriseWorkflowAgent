"""Stage 07: agent runs and their traces.

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-23

Generated with Alembic autogenerate, then edited by hand to add append-only
triggers. They reuse reject_append_only_change() from revision 0001. Neither
table has a column for prompts, document text or the agent's explanation.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | Sequence[str] | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# Frozen copy of this revision's append-only tables.
APPEND_ONLY_TABLES = ("agent_runs", "agent_steps")

_NEXT_STEPS = (
    "'ready_for_approval', 'request_new_reconciliation', "
    "'recommend_rejection', 'needs_human_review'"
)


def upgrade() -> None:
    """Create run and step tables and protect them from edits."""
    op.create_table(
        "agent_runs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workflow_id", sa.String(length=128), nullable=False),
        sa.Column("actor_id", sa.String(length=64), nullable=False),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("stop_reason", sa.String(length=32), nullable=False),
        sa.Column("provider_calls", sa.Integer(), nullable=False),
        sa.Column("tool_calls", sa.Integer(), nullable=False),
        sa.Column("refused_calls", sa.Integer(), nullable=False),
        sa.Column("proposed_next_step", sa.String(length=32), nullable=True),
        sa.Column("next_step", sa.String(length=32), nullable=True),
        sa.Column("override_reason", sa.String(length=64), nullable=True),
        sa.Column("explanation_sha256", sa.String(length=64), nullable=True),
        sa.Column("explanation_chars", sa.Integer(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "explanation_sha256 IS NULL OR explanation_sha256 ~ '^[0-9a-f]{64}$'",
            name=op.f("ck_agent_runs_explanation_sha256_hex"),
        ),
        sa.CheckConstraint(
            f"next_step IS NULL OR next_step IN ({_NEXT_STEPS})",
            name=op.f("ck_agent_runs_next_step"),
        ),
        sa.CheckConstraint(
            f"proposed_next_step IS NULL OR proposed_next_step IN ({_NEXT_STEPS})",
            name=op.f("ck_agent_runs_proposed_next_step"),
        ),
        sa.CheckConstraint(
            "stop_reason IN ('final_answer', 'tool_call_limit', "
            "'provider_call_limit', 'too_many_refusals', 'provider_timeout', "
            "'provider_error', 'provider_refusal', 'tool_timeout', "
            "'invalid_final_answer')",
            name=op.f("ck_agent_runs_stop_reason"),
        ),
        sa.CheckConstraint(
            "provider_calls >= 0 AND tool_calls >= 0 AND refused_calls >= 0",
            name=op.f("ck_agent_runs_counts_non_negative"),
        ),
        sa.ForeignKeyConstraint(
            ["workflow_id"],
            ["workflows.workflow_id"],
            name=op.f("fk_agent_runs_workflow_id"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_agent_runs")),
    )
    op.create_index(
        op.f("ix_agent_runs_workflow_id"), "agent_runs", ["workflow_id"], unique=False
    )
    op.create_table(
        "agent_steps",
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("tool_name", sa.String(length=64), nullable=True),
        sa.Column("arguments_sha256", sa.String(length=64), nullable=True),
        sa.Column("result_sha256", sa.String(length=64), nullable=True),
        sa.Column("result_bytes", sa.Integer(), nullable=True),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=False),
        sa.CheckConstraint(
            "arguments_sha256 IS NULL OR arguments_sha256 ~ '^[0-9a-f]{64}$'",
            name=op.f("ck_agent_steps_arguments_sha256_hex"),
        ),
        sa.CheckConstraint(
            "kind IN ('provider_call', 'tool_call', 'refused_call', "
            "'final_answer', 'stop')",
            name=op.f("ck_agent_steps_kind"),
        ),
        sa.CheckConstraint(
            "result_sha256 IS NULL OR result_sha256 ~ '^[0-9a-f]{64}$'",
            name=op.f("ck_agent_steps_result_sha256_hex"),
        ),
        sa.CheckConstraint(
            "duration_ms >= 0", name=op.f("ck_agent_steps_duration_non_negative")
        ),
        sa.CheckConstraint("seq > 0", name=op.f("ck_agent_steps_seq_positive")),
        sa.ForeignKeyConstraint(
            ["run_id"], ["agent_runs.id"], name=op.f("fk_agent_steps_run_id")
        ),
        sa.PrimaryKeyConstraint("run_id", "seq", name=op.f("pk_agent_steps")),
    )
    for table in APPEND_ONLY_TABLES:
        op.execute(
            f"CREATE TRIGGER trg_{table}_append_only "
            f"BEFORE UPDATE OR DELETE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION reject_append_only_change()"
        )


def downgrade() -> None:
    """Remove agent traces; stored runs are lost."""
    for table in APPEND_ONLY_TABLES:
        op.execute(f"DROP TRIGGER trg_{table}_append_only ON {table}")
    op.drop_table("agent_steps")
    op.drop_index(op.f("ix_agent_runs_workflow_id"), table_name="agent_runs")
    op.drop_table("agent_runs")
