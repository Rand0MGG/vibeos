from __future__ import annotations

from ..core.domain.task import TaskEvent, TaskEventType, TaskRun
from .contracts import FailureCode, GatewayResult


def keyring_wait_event(*, state: TaskRun, result: GatewayResult, event_id: str, occurred_at: str) -> TaskEvent:
    failure = result.failure
    if result.status != "waiting" or failure is None or failure.code is not FailureCode.KEYRING_LOCKED or not failure.wait_event_key:
        raise ValueError("gateway result is not a keyring wait condition")
    return TaskEvent(
        event_id=event_id,
        task_id=state.task_id,
        event_type=TaskEventType.WAIT_REQUESTED,
        occurred_at=occurred_at,
        reason=failure.safe_message,
        interaction_id=failure.wait_event_key,
    )


def keyring_unlocked_event(*, state: TaskRun, event_id: str, occurred_at: str) -> TaskEvent:
    if not state.wait_event_key or not state.wait_event_key.startswith("secret-service:unlocked:"):
        raise ValueError("task is not waiting for a keyring unlock event")
    return TaskEvent(
        event_id=event_id,
        task_id=state.task_id,
        event_type=TaskEventType.EVENT_RECEIVED,
        occurred_at=occurred_at,
        interaction_id=state.wait_event_key,
        reason="session keyring unlocked",
    )
