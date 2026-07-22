from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace

from .task import (
    TERMINAL_STATUSES,
    EffectKind,
    InvalidTaskTransition,
    TaskEvent,
    TaskEventType,
    TaskRun,
    TaskStatus,
    TaskTransition,
    TerminalOutcome,
    TransitionEffect,
)

StateChange = tuple[TaskRun, tuple[TransitionEffect, ...]]
Handler = Callable[[TaskRun, TaskEvent], StateChange]

_COMMON_ACTIVE = frozenset({TaskEventType.PAUSE_REQUESTED, TaskEventType.CANCEL_REQUESTED, TaskEventType.TIMEOUT, TaskEventType.FAIL})
_WAITING_CONTROL = frozenset(
    {TaskEventType.PAUSE_REQUESTED, TaskEventType.CANCEL_REQUESTED, TaskEventType.TAKEOVER_REQUESTED, TaskEventType.TIMEOUT, TaskEventType.FAIL}
)

_STATUS_EVENTS: dict[TaskStatus, frozenset[TaskEventType]] = {
    TaskStatus.CREATED: frozenset(
        {TaskEventType.PLAN_REQUESTED, TaskEventType.PAUSE_REQUESTED, TaskEventType.CANCEL_REQUESTED, TaskEventType.TIMEOUT, TaskEventType.FAIL}
    ),
    TaskStatus.PLANNING: frozenset(
        {
            TaskEventType.PLAN_READY,
            TaskEventType.PLAN_FAILED,
            TaskEventType.FACTS_CAPTURED,
            TaskEventType.MODEL_RESULT_RECORDED,
            TaskEventType.CLARIFICATION_REQUIRED,
            TaskEventType.WAIT_REQUESTED,
            TaskEventType.COMPLETE,
        }
    )
    | _COMMON_ACTIVE,
    TaskStatus.READY: frozenset(
        {
            TaskEventType.DISPATCH_REQUESTED,
            TaskEventType.REVIEW_REQUIRED,
            TaskEventType.RETRY_SCHEDULED,
            TaskEventType.WAIT_REQUESTED,
            TaskEventType.REPLAN_REQUESTED,
            TaskEventType.CLARIFICATION_REQUIRED,
            TaskEventType.TAKEOVER_REQUESTED,
            TaskEventType.COMPLETE,
            TaskEventType.DRY_RUN_COMPLETED,
            TaskEventType.VERIFICATION_PASSED,
            TaskEventType.FAIL,
            TaskEventType.PAUSE_REQUESTED,
            TaskEventType.CANCEL_REQUESTED,
        }
    ),
    TaskStatus.RUNNING: frozenset(
        {
            TaskEventType.ACTION_PROPOSED,
            TaskEventType.ACTION_SUCCEEDED,
            TaskEventType.ACTION_FAILED,
            TaskEventType.RECONCILIATION_REQUIRED,
            TaskEventType.REVIEW_REQUIRED,
            TaskEventType.WAIT_REQUESTED,
            TaskEventType.TAKEOVER_REQUESTED,
        }
    )
    | _COMMON_ACTIVE,
    TaskStatus.VERIFYING: frozenset(
        {TaskEventType.STEP_EVIDENCE_RECORDED, TaskEventType.VERIFICATION_PASSED, TaskEventType.VERIFICATION_FAILED, TaskEventType.REPLAN_REQUESTED}
    )
    | _COMMON_ACTIVE,
    TaskStatus.WAITING: frozenset({TaskEventType.TIMER_ELAPSED, TaskEventType.EVENT_RECEIVED}) | _WAITING_CONTROL,
    TaskStatus.AWAITING_REVIEW: frozenset({TaskEventType.REVIEW_REQUIRED, TaskEventType.REVIEW_APPROVED, TaskEventType.REVIEW_REJECTED}) | _WAITING_CONTROL,
    TaskStatus.AWAITING_CLARIFICATION: frozenset({TaskEventType.CLARIFICATION_PROVIDED}) | _WAITING_CONTROL,
    TaskStatus.RETRY_WAIT: frozenset({TaskEventType.TIMER_ELAPSED}) | _COMMON_ACTIVE,
    TaskStatus.REPLANNING: frozenset({TaskEventType.PLAN_READY, TaskEventType.PLAN_FAILED, TaskEventType.CLARIFICATION_REQUIRED}) | _COMMON_ACTIVE,
    TaskStatus.RECONCILING: frozenset({TaskEventType.RECONCILIATION_SUCCEEDED, TaskEventType.RECONCILIATION_NOT_APPLIED, TaskEventType.RECONCILIATION_UNKNOWN})
    | _COMMON_ACTIVE,
    TaskStatus.PAUSED: frozenset(
        {TaskEventType.RESUME_REQUESTED, TaskEventType.CANCEL_REQUESTED, TaskEventType.TAKEOVER_REQUESTED, TaskEventType.TIMEOUT, TaskEventType.FAIL}
    ),
    TaskStatus.CANCEL_REQUESTED: frozenset(
        {
            TaskEventType.CANCELLATION_CONFIRMED,
            TaskEventType.ACTION_SUCCEEDED,
            TaskEventType.ACTION_FAILED,
            TaskEventType.RECONCILIATION_REQUIRED,
            TaskEventType.TIMEOUT,
            TaskEventType.FAIL,
        }
    ),
    TaskStatus.TAKEN_OVER: frozenset({TaskEventType.RELEASE_REQUESTED, TaskEventType.CANCEL_REQUESTED, TaskEventType.TIMEOUT, TaskEventType.FAIL}),
    TaskStatus.DRY_RUN: frozenset(),
    TaskStatus.SUCCEEDED: frozenset(),
    TaskStatus.FAILED: frozenset(),
    TaskStatus.CANCELLED: frozenset(),
    TaskStatus.BLOCKED: frozenset(),
}


def allowed_events(status: TaskStatus) -> frozenset[TaskEventType]:
    return _STATUS_EVENTS[status]


def transition(state: TaskRun, event: TaskEvent) -> TaskTransition:
    if event.task_id != state.task_id:
        raise InvalidTaskTransition("event task id does not match current task")
    if event.event_type not in allowed_events(state.status):
        raise InvalidTaskTransition(f"{event.event_type.value} is invalid while task is {state.status.value}")
    _validate_event(state, event)
    updated, effects = _HANDLERS[event.event_type](state, event)
    return TaskTransition(
        previous_revision=state.revision,
        state=replace(updated, revision=state.revision + 1, updated_at=event.occurred_at, last_event=event.event_type.value),
        event=event,
        effects=effects,
    )


def _set(status: TaskStatus, effect: EffectKind | None = None) -> Handler:
    def handler(state: TaskRun, event: TaskEvent) -> StateChange:
        effects = (_effect(event, effect),) if effect is not None else ()
        return replace(state, status=status), effects

    return handler


def _plan_ready(state: TaskRun, event: TaskEvent) -> StateChange:
    return replace(state, status=TaskStatus.READY, active_plan_revision_id=event.plan_revision_id, pending_reason=None), ()


def _clarification_required(state: TaskRun, event: TaskEvent) -> StateChange:
    return replace(state, status=TaskStatus.AWAITING_CLARIFICATION, pending_interaction_id=event.interaction_id, pending_reason=event.reason), ()


def _clarification_provided(state: TaskRun, event: TaskEvent) -> StateChange:
    return replace(state, status=TaskStatus.PLANNING, pending_interaction_id=None, pending_reason=None), (_effect(event, EffectKind.PLAN),)


def _review_required(state: TaskRun, event: TaskEvent) -> StateChange:
    return replace(
        state, status=TaskStatus.AWAITING_REVIEW, current_step_id=event.step_id, pending_interaction_id=event.interaction_id, pending_reason=event.reason
    ), ()


def _review_approved(state: TaskRun, event: TaskEvent) -> StateChange:
    return replace(state, status=TaskStatus.READY, pending_interaction_id=None, pending_reason=None), ()


def _dispatch(state: TaskRun, event: TaskEvent) -> StateChange:
    return replace(state, status=TaskStatus.RUNNING, current_step_id=event.step_id), (_effect(event, EffectKind.DISPATCH_ACTION),)


def _action_succeeded(state: TaskRun, event: TaskEvent) -> StateChange:
    return replace(state, status=TaskStatus.VERIFYING, current_step_id=event.step_id or state.current_step_id), (_effect(event, EffectKind.VERIFY),)


def _action_failed(state: TaskRun, event: TaskEvent) -> StateChange:
    return replace(state, status=TaskStatus.READY, pending_reason=event.reason), ()


def _reconciliation_required(state: TaskRun, event: TaskEvent) -> StateChange:
    return replace(state, status=TaskStatus.RECONCILING, pending_reason=event.reason), (_effect(event, EffectKind.RECONCILE),)


def _pause(state: TaskRun, event: TaskEvent) -> StateChange:
    return replace(state, status=TaskStatus.PAUSED, suspended_status=state.status, pending_reason=event.reason), ()


def _verification_passed(state: TaskRun, event: TaskEvent) -> StateChange:
    completed = state.completed_step_ids
    if event.step_id and event.step_id not in completed:
        completed = (*completed, event.step_id)
    updated = replace(state, completed_step_ids=completed)
    if event.terminal_status is TaskStatus.SUCCEEDED:
        return _terminal(updated, event, TaskStatus.SUCCEEDED)
    return replace(updated, status=TaskStatus.READY, current_step_id=None), ()


def _schedule(status: TaskStatus) -> Handler:
    def handler(state: TaskRun, event: TaskEvent) -> StateChange:
        kind = EffectKind.SCHEDULE_TIMER if event.wake_at is not None else None
        effects = (_effect(event, kind),) if kind is not None else ()
        return replace(
            state,
            status=status,
            suspended_status=state.status,
            next_wake_at=event.wake_at,
            wait_event_key=event.interaction_id,
            pending_reason=event.reason,
        ), effects

    return handler


def _timer_elapsed(state: TaskRun, event: TaskEvent) -> StateChange:
    resumed = state.suspended_status or TaskStatus.READY
    if resumed in TERMINAL_STATUSES or resumed in {TaskStatus.WAITING, TaskStatus.RETRY_WAIT, TaskStatus.PAUSED, TaskStatus.TAKEN_OVER}:
        resumed = TaskStatus.READY
    return replace(state, status=resumed, suspended_status=None, next_wake_at=None, wait_event_key=None, pending_reason=None), ()


def _resume(state: TaskRun, event: TaskEvent) -> StateChange:
    resumed = state.suspended_status or TaskStatus.READY
    if resumed in TERMINAL_STATUSES or resumed in {TaskStatus.PAUSED, TaskStatus.TAKEN_OVER}:
        resumed = TaskStatus.READY
    return replace(state, status=resumed, suspended_status=None, pending_reason=None), ()


def _cancel(state: TaskRun, event: TaskEvent) -> StateChange:
    active = state.status in {TaskStatus.RUNNING, TaskStatus.RECONCILING}
    effects = (_effect(event, EffectKind.CANCEL_ACTION),) if active else ()
    return replace(state, status=TaskStatus.CANCEL_REQUESTED, cancel_requested=True, pending_reason=event.reason), effects


def _takeover(state: TaskRun, event: TaskEvent) -> StateChange:
    return replace(state, status=TaskStatus.TAKEN_OVER, suspended_status=state.status, takeover_owner=event.owner, pending_reason=event.reason), ()


def _release(state: TaskRun, event: TaskEvent) -> StateChange:
    resumed = state.suspended_status or TaskStatus.READY
    return replace(state, status=resumed, suspended_status=None, takeover_owner=None, pending_reason=None), ()


def _fail(state: TaskRun, event: TaskEvent) -> StateChange:
    status = event.terminal_status if event.terminal_status in TERMINAL_STATUSES else TaskStatus.FAILED
    return _terminal(state, event, status)


def _terminal_handler(status: TaskStatus) -> Handler:
    return lambda state, event: _terminal(state, event, status)


def _terminal(state: TaskRun, event: TaskEvent, status: TaskStatus) -> StateChange:
    outcome = TerminalOutcome(
        task_id=state.task_id,
        status=status.value,
        reason=event.reason,
        evidence_ids=event.evidence_ids,
        finished_at=event.occurred_at,
        diagnosis=event.diagnosis,
        action=event.action,
        current_state=event.current_state,
        completion_judgment=event.completion_judgment,
        unresolved_risks=event.unresolved_risks,
    )
    updated = replace(
        state,
        status=status,
        terminal_outcome=outcome,
        pending_interaction_id=None,
        next_wake_at=None,
        wait_event_key=None,
        takeover_owner=None,
    )
    return updated, (_effect(event, EffectKind.NOTIFY),)


def _effect(event: TaskEvent, kind: EffectKind) -> TransitionEffect:
    return TransitionEffect(kind=kind, effect_id=f"{event.event_id}:{kind.value}", task_id=event.task_id, step_id=event.step_id, not_before=event.wake_at)


_HANDLERS: dict[TaskEventType, Handler] = {
    TaskEventType.PLAN_REQUESTED: _set(TaskStatus.PLANNING, EffectKind.PLAN),
    TaskEventType.PLAN_READY: _plan_ready,
    TaskEventType.PLAN_FAILED: _terminal_handler(TaskStatus.FAILED),
    TaskEventType.FACTS_CAPTURED: lambda state, event: (state, ()),
    TaskEventType.MODEL_RESULT_RECORDED: lambda state, event: (state, ()),
    TaskEventType.CLARIFICATION_REQUIRED: _clarification_required,
    TaskEventType.CLARIFICATION_PROVIDED: _clarification_provided,
    TaskEventType.REVIEW_REQUIRED: _review_required,
    TaskEventType.REVIEW_APPROVED: _review_approved,
    TaskEventType.REVIEW_REJECTED: _terminal_handler(TaskStatus.CANCELLED),
    TaskEventType.DISPATCH_REQUESTED: _dispatch,
    TaskEventType.ACTION_PROPOSED: lambda state, event: (state, ()),
    TaskEventType.ACTION_SUCCEEDED: _action_succeeded,
    TaskEventType.ACTION_FAILED: _action_failed,
    TaskEventType.STEP_EVIDENCE_RECORDED: _verification_passed,
    TaskEventType.RECONCILIATION_REQUIRED: _reconciliation_required,
    TaskEventType.RECONCILIATION_SUCCEEDED: _action_succeeded,
    TaskEventType.RECONCILIATION_NOT_APPLIED: _set(TaskStatus.RUNNING),
    TaskEventType.RECONCILIATION_UNKNOWN: _pause,
    TaskEventType.VERIFICATION_PASSED: _verification_passed,
    TaskEventType.VERIFICATION_FAILED: _action_failed,
    TaskEventType.RETRY_SCHEDULED: _schedule(TaskStatus.RETRY_WAIT),
    TaskEventType.REPLAN_REQUESTED: _set(TaskStatus.REPLANNING, EffectKind.PLAN),
    TaskEventType.WAIT_REQUESTED: _schedule(TaskStatus.WAITING),
    TaskEventType.TIMER_ELAPSED: _timer_elapsed,
    TaskEventType.EVENT_RECEIVED: _timer_elapsed,
    TaskEventType.PAUSE_REQUESTED: _pause,
    TaskEventType.RESUME_REQUESTED: _resume,
    TaskEventType.CANCEL_REQUESTED: _cancel,
    TaskEventType.CANCELLATION_CONFIRMED: _terminal_handler(TaskStatus.CANCELLED),
    TaskEventType.TAKEOVER_REQUESTED: _takeover,
    TaskEventType.RELEASE_REQUESTED: _release,
    TaskEventType.TIMEOUT: _fail,
    TaskEventType.COMPLETE: _terminal_handler(TaskStatus.SUCCEEDED),
    TaskEventType.DRY_RUN_COMPLETED: _terminal_handler(TaskStatus.DRY_RUN),
    TaskEventType.FAIL: _fail,
}


def _validate_event(state: TaskRun, event: TaskEvent) -> None:
    required: dict[TaskEventType, tuple[str, object | None]] = {
        TaskEventType.PLAN_READY: ("plan_revision_id", event.plan_revision_id),
        TaskEventType.CLARIFICATION_REQUIRED: ("interaction_id", event.interaction_id),
        TaskEventType.REVIEW_REQUIRED: ("interaction_id", event.interaction_id),
        TaskEventType.DISPATCH_REQUESTED: ("step_id", event.step_id),
        TaskEventType.ACTION_PROPOSED: ("step_id", event.step_id),
        TaskEventType.ACTION_SUCCEEDED: ("step_id", event.step_id),
        TaskEventType.ACTION_FAILED: ("step_id", event.step_id),
        TaskEventType.STEP_EVIDENCE_RECORDED: ("step_id", event.step_id),
        TaskEventType.RECONCILIATION_REQUIRED: ("step_id", event.step_id),
        TaskEventType.RECONCILIATION_SUCCEEDED: ("step_id", event.step_id),
        TaskEventType.RECONCILIATION_NOT_APPLIED: ("step_id", event.step_id),
        TaskEventType.RECONCILIATION_UNKNOWN: ("step_id", event.step_id),
        TaskEventType.VERIFICATION_PASSED: ("step_id", event.step_id),
        TaskEventType.VERIFICATION_FAILED: ("step_id", event.step_id),
        TaskEventType.RETRY_SCHEDULED: ("wake_at", event.wake_at),
        TaskEventType.TAKEOVER_REQUESTED: ("owner", event.owner),
        TaskEventType.EVENT_RECEIVED: ("interaction_id", event.interaction_id),
    }
    item = required.get(event.event_type)
    if item is not None and not item[1]:
        raise InvalidTaskTransition(f"{event.event_type.value} requires {item[0]}")
    if event.event_type is TaskEventType.WAIT_REQUESTED and not (event.wake_at or event.interaction_id):
        raise InvalidTaskTransition("wait_requested requires wake_at or interaction_id")
    if event.event_type is TaskEventType.EVENT_RECEIVED and event.interaction_id != state.wait_event_key:
        raise InvalidTaskTransition("event_received does not match the active wait event key")
    if event.event_type in {TaskEventType.COMPLETE, TaskEventType.DRY_RUN_COMPLETED} and state.status is not TaskStatus.PLANNING:
        if not event.evidence_ids:
            raise InvalidTaskTransition(f"{event.event_type.value} requires terminal evidence")
