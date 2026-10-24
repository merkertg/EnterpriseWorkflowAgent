"""What every evaluation case gets: a desk, a live receiver and a clock."""

from collections.abc import Callable
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import Engine

from app.application.delivery import DeliveryPolicy, OutboxDeliveryService
from app.application.errors import ApplicationError
from app.application.ports import OutboxEntry
from app.domain.documents import DocumentRejectedError
from app.domain.errors import DomainValidationError
from app.fictional_desk import FictionalDesk, ShiftedClock
from app.infrastructure.db.session import build_delivery_service
from app.infrastructure.webhook.sender import UrllibWebhookSender
from sandbox.receiver import Behaviour, Mode, ReceiverStore

# Small bounds keep delivery cases quick and their attempt counts readable.
EVAL_POLICY = DeliveryPolicy(
    max_attempts=3, base_delay_seconds=1.0, max_delay_seconds=10.0, timeout_seconds=0.5
)


@dataclass(slots=True)
class EvalContext:
    """Shared fixtures of one evaluation run."""

    engine: Engine
    desk: FictionalDesk
    store: ReceiverStore
    behaviour: Behaviour
    clock: ShiftedClock
    url: str
    secret: str

    def worker(self, secret: str | None = None) -> OutboxDeliveryService:
        """A delivery worker for the run's receiver, optionally mis-keyed."""
        return build_delivery_service(
            self.engine,
            UrllibWebhookSender(self.url),
            self.secret if secret is None else secret,
            EVAL_POLICY,
            self.clock.now,
        )

    def flush(self) -> int:
        """Deliver everything due with an accepting receiver; return the count.

        Delivery cases start from here, so each one observes only its own
        event, whatever earlier cases or runs left pending.
        """
        self.behaviour.set(Mode.ACCEPT)
        self.clock.advance(EVAL_POLICY.max_delay_seconds)
        return len(self.worker().deliver_due(limit=1000))

    def entry(self, workflow_id: str) -> OutboxEntry:
        """The single outbox entry of a decided workflow, read as the viewer."""
        [entry] = self.desk.workflows.outbox_events(
            workflow_id, actor=self.desk.team.viewer
        )
        return entry

    def event_id(self, workflow_id: str) -> UUID:
        """The event ID of a decided workflow."""
        return self.entry(workflow_id).event.event_id

    def actions(self, workflow_id: str) -> int:
        """How many actions the receiver took for this workflow."""
        return sum(1 for a in self.store.actions() if a.workflow_id == workflow_id)

    def receipts(self, workflow_id: str) -> int:
        """How many signed requests for this workflow reached the receiver."""
        return sum(
            a.receipts for a in self.store.actions() if a.workflow_id == workflow_id
        )

    def state(self, workflow_id: str) -> str:
        """The workflow's current state."""
        return self.desk.workflows.get_workflow(workflow_id).workflow.state.value

    def outbox_events(self, workflow_id: str) -> int:
        """How many decision events the workflow has."""
        return len(self.desk.workflows.outbox_events(workflow_id))


def outcome(action: Callable[[], object]) -> str:
    """Run a zero-argument callable; return ``ok`` or the stable error code."""
    try:
        action()
    except (ApplicationError, DomainValidationError) as error:
        return error.code
    except DocumentRejectedError as error:
        return error.code.value
    return "ok"
