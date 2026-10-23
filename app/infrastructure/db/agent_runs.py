"""SQLAlchemy storage for finished agent runs and their traces."""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.application.ports import (
    AgentRunRecord,
    AgentStep,
    AgentStepKind,
    AgentStopReason,
    NextStep,
)
from app.infrastructure.db.inserts import insert_rows
from app.infrastructure.db.models import AgentRunRow, AgentStepRow
from app.infrastructure.db.repository import reconstructing


class SqlAlchemyAgentRunRepository:
    """Agent-run storage bound to the unit of work's session."""

    def __init__(self, session: Session) -> None:
        """Use the shared session; this class never commits."""
        self._session = session

    def add_run(self, run: AgentRunRecord) -> None:
        """Insert the run first, then its steps, in one flush."""
        insert_rows(
            self._session,
            AgentRunRow(
                id=run.run_id,
                workflow_id=run.workflow_id,
                actor_id=run.actor_id,
                provider=run.provider,
                stop_reason=run.stop_reason.value,
                provider_calls=run.provider_calls,
                tool_calls=run.tool_calls,
                refused_calls=run.refused_calls,
                proposed_next_step=None
                if run.proposed_next_step is None
                else run.proposed_next_step.value,
                next_step=None if run.next_step is None else run.next_step.value,
                override_reason=run.override_reason,
                explanation_sha256=run.explanation_sha256,
                explanation_chars=run.explanation_chars,
                started_at=run.started_at,
                finished_at=run.finished_at,
            ),
        )
        insert_rows(
            self._session,
            *(
                AgentStepRow(
                    run_id=run.run_id,
                    seq=step.seq,
                    kind=step.kind.value,
                    tool_name=step.tool_name,
                    arguments_sha256=step.arguments_sha256,
                    result_sha256=step.result_sha256,
                    result_bytes=step.result_bytes,
                    error_code=step.error_code,
                    duration_ms=step.duration_ms,
                )
                for step in run.steps
            ),
        )

    def get_run(self, run_id: UUID) -> AgentRunRecord | None:
        """Load a run with its steps in trace order."""
        row = self._session.get(AgentRunRow, run_id)
        if row is None:
            return None
        step_rows = self._session.scalars(
            select(AgentStepRow)
            .where(AgentStepRow.run_id == run_id)
            .order_by(AgentStepRow.seq)
        ).all()
        with reconstructing("agent run"):
            steps = tuple(
                AgentStep(
                    seq=item.seq,
                    kind=AgentStepKind(item.kind),
                    tool_name=item.tool_name,
                    arguments_sha256=item.arguments_sha256,
                    result_sha256=item.result_sha256,
                    result_bytes=item.result_bytes,
                    error_code=item.error_code,
                    duration_ms=item.duration_ms,
                )
                for item in step_rows
            )
            return AgentRunRecord(
                run_id=row.id,
                workflow_id=row.workflow_id,
                actor_id=row.actor_id,
                provider=row.provider,
                stop_reason=AgentStopReason(row.stop_reason),
                provider_calls=row.provider_calls,
                tool_calls=row.tool_calls,
                refused_calls=row.refused_calls,
                proposed_next_step=None
                if row.proposed_next_step is None
                else NextStep(row.proposed_next_step),
                next_step=None if row.next_step is None else NextStep(row.next_step),
                override_reason=row.override_reason,
                explanation_sha256=row.explanation_sha256,
                explanation_chars=row.explanation_chars,
                steps=steps,
                started_at=row.started_at,
                finished_at=row.finished_at,
            )
