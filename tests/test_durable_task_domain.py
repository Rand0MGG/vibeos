from __future__ import annotations

from itertools import product

import pytest

from vibeos.core.domain.task import InvalidTaskTransition, TaskEvent, TaskEventType, TaskRun, TaskStatus
from vibeos.core.domain.task_transitions import allowed_events, transition

ALL_COMBINATIONS = tuple(product(TaskStatus, TaskEventType))


@pytest.mark.parametrize(("status", "event_type"), ALL_COMBINATIONS)
def test_complete_state_event_matrix_is_fail_closed(status: TaskStatus, event_type: TaskEventType) -> None:
    state = _state(status)
    task_event = _event(event_type)

    if event_type in allowed_events(status):
        result = transition(state, task_event)
        assert result.previous_revision == 7
        assert result.state.revision == 8
        assert result.state.last_event == event_type.value
    else:
        with pytest.raises(InvalidTaskTransition):
            transition(state, task_event)


@pytest.mark.parametrize("terminal", [TaskStatus.DRY_RUN, TaskStatus.SUCCEEDED, TaskStatus.FAILED, TaskStatus.CANCELLED, TaskStatus.BLOCKED])
def test_terminal_tasks_cannot_be_revived_by_any_worker_event(terminal: TaskStatus) -> None:
    state = _state(terminal)
    for event_type in TaskEventType:
        with pytest.raises(InvalidTaskTransition):
            transition(state, _event(event_type))


def test_transition_is_pure_and_deterministic() -> None:
    state = _state(TaskStatus.READY)
    task_event = _event(TaskEventType.WAIT_REQUESTED, wake_at="2099-01-01T01:00:00.000Z")
    first = transition(state, task_event)
    second = transition(state, task_event)
    assert first == second
    assert state.status is TaskStatus.READY
    assert state.revision == 7
    assert first.state.status is TaskStatus.WAITING


def test_event_for_another_task_is_rejected() -> None:
    with pytest.raises(InvalidTaskTransition, match="task id"):
        transition(_state(TaskStatus.CREATED), _event(TaskEventType.PLAN_REQUESTED, task_id="other"))


def _state(status: TaskStatus) -> TaskRun:
    return TaskRun(
        task_id="task-matrix",
        contract_id="contract-matrix",
        status=status,
        revision=7,
        created_at="2099-01-01T00:00:00.000Z",
        updated_at="2099-01-01T00:00:00.000Z",
        suspended_status=TaskStatus.READY if status in {TaskStatus.PAUSED, TaskStatus.TAKEN_OVER} else None,
        wait_event_key="interaction-1" if status is TaskStatus.WAITING else None,
    )


def _event(event_type: TaskEventType, *, task_id: str = "task-matrix", wake_at: str | None = None) -> TaskEvent:
    return TaskEvent(
        event_id=f"event-{event_type.value}",
        task_id=task_id,
        event_type=event_type,
        occurred_at="2099-01-01T00:00:01.000Z",
        interaction_id="interaction-1",
        plan_revision_id="planrev-1",
        step_id="step-1",
        wake_at=wake_at or ("2099-01-01T01:00:00.000Z" if event_type is TaskEventType.RETRY_SCHEDULED else None),
        owner="operator",
        evidence_ids=("evidence-1",) if event_type in {TaskEventType.COMPLETE, TaskEventType.DRY_RUN_COMPLETED} else (),
    )


@pytest.mark.parametrize(
    ("status", "event_type", "message"),
    [
        (TaskStatus.PLANNING, TaskEventType.PLAN_READY, "plan_revision_id"),
        (TaskStatus.READY, TaskEventType.DISPATCH_REQUESTED, "step_id"),
        (TaskStatus.READY, TaskEventType.WAIT_REQUESTED, "wake_at or interaction_id"),
        (TaskStatus.READY, TaskEventType.RETRY_SCHEDULED, "wake_at"),
        (TaskStatus.READY, TaskEventType.TAKEOVER_REQUESTED, "owner"),
        (TaskStatus.READY, TaskEventType.COMPLETE, "terminal evidence"),
    ],
)
def test_semantic_event_invariants_fail_closed(status: TaskStatus, event_type: TaskEventType, message: str) -> None:
    task_event = TaskEvent("invalid", "task-matrix", event_type, "2099-01-01T00:00:01.000Z")
    with pytest.raises(InvalidTaskTransition, match=message):
        transition(_state(status), task_event)
