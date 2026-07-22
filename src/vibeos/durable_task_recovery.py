from __future__ import annotations

from .core.adapters.task_repository import SqliteTaskRepository
from .core.domain import transition
from .core.domain.task import TaskEventType, TaskLease, TaskRun, TaskStatus
from .durable_task_support import event


class DurableInterruptionRecovery:
    """Safely disposes states that cannot return directly to normal driving."""

    def __init__(self, repository: SqliteTaskRepository) -> None:
        self.repository = repository

    def resolve(self, state: TaskRun, lease: TaskLease) -> bool:
        if state.status is TaskStatus.RECONCILING:
            self._commit(
                state,
                TaskEventType.RECONCILIATION_UNKNOWN,
                lease,
                reason="restart recovery found no adapter reconciliation proof",
            )
            return True
        if state.status is not TaskStatus.CANCEL_REQUESTED:
            return False
        proposal = self.repository.unresolved_proposal(state.task_id)
        if proposal is None:
            self._commit(
                state,
                TaskEventType.CANCELLATION_CONFIRMED,
                lease,
                reason="no unresolved external action remained after cancellation",
            )
            return True
        state = self._commit(
            state,
            TaskEventType.RECONCILIATION_REQUIRED,
            lease,
            step_id=proposal.step_id,
            reason="cancellation found an unresolved external action",
        )
        self._commit(
            state,
            TaskEventType.RECONCILIATION_UNKNOWN,
            lease,
            step_id=proposal.step_id,
            reason="external cancellation could not be safely proven",
        )
        return True

    def _commit(
        self,
        state: TaskRun,
        kind: TaskEventType,
        lease: TaskLease,
        *,
        reason: str,
        step_id: str | None = None,
    ) -> TaskRun:
        task_event = event(state, kind, reason=reason, step_id=step_id)
        return self.repository.commit(transition(state, task_event), lease=lease)
