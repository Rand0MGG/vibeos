from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


TASK_SCHEMA_VERSION = "v2"


class TaskStatus(StrEnum):
    CREATED = "created"
    PLANNING = "planning"
    READY = "ready"
    RUNNING = "running"
    VERIFYING = "verifying"
    WAITING = "waiting"
    AWAITING_REVIEW = "awaiting_review"
    AWAITING_CLARIFICATION = "awaiting_clarification"
    RETRY_WAIT = "retry_wait"
    REPLANNING = "replanning"
    RECONCILING = "reconciling"
    PAUSED = "paused"
    CANCEL_REQUESTED = "cancel_requested"
    TAKEN_OVER = "taken_over"
    DRY_RUN = "dry_run"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    BLOCKED = "blocked"


TERMINAL_STATUSES = frozenset(
    {
        TaskStatus.SUCCEEDED,
        TaskStatus.DRY_RUN,
        TaskStatus.FAILED,
        TaskStatus.CANCELLED,
        TaskStatus.BLOCKED,
    }
)


class TaskEventType(StrEnum):
    PLAN_REQUESTED = "plan_requested"
    PLAN_READY = "plan_ready"
    PLAN_FAILED = "plan_failed"
    CLARIFICATION_REQUIRED = "clarification_required"
    CLARIFICATION_PROVIDED = "clarification_provided"
    REVIEW_REQUIRED = "review_required"
    REVIEW_APPROVED = "review_approved"
    REVIEW_REJECTED = "review_rejected"
    DISPATCH_REQUESTED = "dispatch_requested"
    ACTION_PROPOSED = "action_proposed"
    ACTION_SUCCEEDED = "action_succeeded"
    ACTION_FAILED = "action_failed"
    STEP_EVIDENCE_RECORDED = "step_evidence_recorded"
    RECONCILIATION_REQUIRED = "reconciliation_required"
    RECONCILIATION_SUCCEEDED = "reconciliation_succeeded"
    RECONCILIATION_NOT_APPLIED = "reconciliation_not_applied"
    RECONCILIATION_UNKNOWN = "reconciliation_unknown"
    VERIFICATION_PASSED = "verification_passed"
    VERIFICATION_FAILED = "verification_failed"
    RETRY_SCHEDULED = "retry_scheduled"
    REPLAN_REQUESTED = "replan_requested"
    WAIT_REQUESTED = "wait_requested"
    TIMER_ELAPSED = "timer_elapsed"
    EVENT_RECEIVED = "event_received"
    PAUSE_REQUESTED = "pause_requested"
    RESUME_REQUESTED = "resume_requested"
    CANCEL_REQUESTED = "cancel_requested"
    CANCELLATION_CONFIRMED = "cancellation_confirmed"
    TAKEOVER_REQUESTED = "takeover_requested"
    RELEASE_REQUESTED = "release_requested"
    TIMEOUT = "timeout"
    COMPLETE = "complete"
    DRY_RUN_COMPLETED = "dry_run_completed"
    FAIL = "fail"


class EffectKind(StrEnum):
    PLAN = "plan"
    DISPATCH_ACTION = "dispatch_action"
    VERIFY = "verify"
    RECONCILE = "reconcile"
    SCHEDULE_TIMER = "schedule_timer"
    CANCEL_ACTION = "cancel_action"
    NOTIFY = "notify"


class InvalidTaskTransition(ValueError):
    """The requested transition is not valid for the authoritative state."""


@dataclass(frozen=True)
class GoalContract:
    contract_id: str
    task_id: str
    goal: str
    scope: tuple[str, ...]
    completion_conditions: tuple[str, ...]
    allowed_effects: tuple[str, ...]
    reality_boundaries: tuple[str, ...]
    version: int
    created_at: str
    dry_run: bool | None = None
    schema_version: str = TASK_SCHEMA_VERSION


@dataclass(frozen=True)
class PlanRevision:
    plan_revision_id: str
    task_id: str
    revision: int
    plan_id: str
    payload_json: str
    created_at: str
    reason: str
    schema_version: str = TASK_SCHEMA_VERSION


@dataclass(frozen=True)
class Step:
    step_id: str
    task_id: str
    plan_revision_id: str
    ordinal: int
    action: str
    capability_id: str
    status: str
    idempotency_key: str
    payload_json: str
    created_at: str
    updated_at: str
    schema_version: str = TASK_SCHEMA_VERSION


@dataclass(frozen=True)
class Attempt:
    attempt_id: str
    task_id: str
    step_id: str | None
    attempt_number: int
    classification: str
    status: str
    started_at: str
    finished_at: str | None = None
    detail_json: str = "{}"
    schema_version: str = TASK_SCHEMA_VERSION


@dataclass(frozen=True)
class WaitCondition:
    wait_id: str
    task_id: str
    kind: str
    due_at: str | None
    event_key: str | None
    status: str
    created_at: str
    satisfied_at: str | None = None
    schema_version: str = TASK_SCHEMA_VERSION


@dataclass(frozen=True)
class ActionReceipt:
    receipt_id: str
    task_id: str
    step_id: str
    proposal_id: str
    idempotency_key: str
    status: str
    adapter: str | None
    external_reference: str | None
    result_json: str
    occurred_at: str
    schema_version: str = TASK_SCHEMA_VERSION


@dataclass(frozen=True)
class ActionProposal:
    proposal_id: str
    task_id: str
    step_id: str
    attempt_id: str
    idempotency_key: str
    action: str
    capability_id: str
    request_json: str
    status: str
    created_at: str
    updated_at: str
    schema_version: str = TASK_SCHEMA_VERSION


@dataclass(frozen=True)
class EvidenceBundle:
    evidence_id: str
    task_id: str
    step_id: str | None
    receipt_id: str | None
    status: str
    summary: str
    payload_json: str
    observed_at: str
    schema_version: str = TASK_SCHEMA_VERSION


@dataclass(frozen=True)
class TerminalOutcome:
    task_id: str
    status: str
    reason: str
    evidence_ids: tuple[str, ...]
    finished_at: str
    schema_version: str = TASK_SCHEMA_VERSION


@dataclass(frozen=True)
class TaskRun:
    task_id: str
    contract_id: str
    status: TaskStatus
    revision: int
    created_at: str
    updated_at: str
    active_plan_revision_id: str | None = None
    current_step_id: str | None = None
    completed_step_ids: tuple[str, ...] = ()
    pending_interaction_id: str | None = None
    pending_reason: str | None = None
    next_wake_at: str | None = None
    wait_event_key: str | None = None
    deadline_at: str | None = None
    suspended_status: TaskStatus | None = None
    cancel_requested: bool = False
    takeover_owner: str | None = None
    last_event: str = "created"
    terminal_outcome: TerminalOutcome | None = None
    schema_version: str = TASK_SCHEMA_VERSION


@dataclass(frozen=True)
class TaskEvent:
    event_id: str
    task_id: str
    event_type: TaskEventType
    occurred_at: str
    reason: str = ""
    interaction_id: str | None = None
    plan_revision_id: str | None = None
    step_id: str | None = None
    wake_at: str | None = None
    owner: str | None = None
    terminal_status: TaskStatus | None = None
    evidence_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class TransitionEffect:
    kind: EffectKind
    effect_id: str
    task_id: str
    step_id: str | None = None
    not_before: str | None = None


@dataclass(frozen=True)
class TaskTransition:
    previous_revision: int
    state: TaskRun
    event: TaskEvent
    effects: tuple[TransitionEffect, ...]


@dataclass(frozen=True)
class TaskLease:
    task_id: str
    owner: str
    expires_at: str
    fencing_token: int
