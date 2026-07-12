from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from .task_models import PlanAttempt


LoopStage = Literal[
    "bootstrap_understanding",
    "init_loop",
    "observe_pre",
    "plan_or_select_step",
    "step_review",
    "act",
    "observe_post",
    "verify",
    "decide",
    "needs_review",
    "needs_user_input",
    "complete",
    "blocked",
    "budget_exhausted",
]

LoopDecision = Literal[
    "continue",
    "retry",
    "repair",
    "replan",
    "needs_review",
    "needs_user_input",
    "complete",
    "blocked",
    "budget_exhausted",
]

ObservationLevel = Literal["L0", "L1", "L2"]


@dataclass(frozen=True)
class LoopPolicy:
    max_attempts: int = 4
    max_steps: int = 8
    max_same_failure_count: int = 2
    same_action_no_progress_limit: int = 1
    review_escalation_enabled: bool = True
    observation_escalation_enabled: bool = True
    default_observation_level: ObservationLevel = "L0"


@dataclass(frozen=True)
class LoopObservation:
    observation_id: str
    level: ObservationLevel
    phase: Literal["pre", "post"]
    packages: dict[str, dict[str, Any]] = field(default_factory=dict)
    route_id: str | None = None
    step_id: str | None = None


@dataclass(frozen=True)
class MigratedStepApprovalBinding:
    """Verified historical approval scope carried into exactly one current step."""

    review_id: str
    step_id: str
    action: str
    original_safety_review_id: str
    risk_level: str
    review_required: bool
    allowed: bool
    reason: str


@dataclass(frozen=True)
class LoopStepResult:
    step_id: str
    execution_status: str
    failure_class: str = "none"
    message: str = ""
    step_result: dict[str, Any] = field(default_factory=dict)
    pre_observation_id: str | None = None
    post_observation_id: str | None = None


@dataclass(frozen=True)
class LoopState:
    loop_snapshot_id: str
    trace_run_id: str
    goal_id: str
    primary_understanding_id: str | None
    candidate_set_id: str | None
    selected_route_decision_id: str | None
    current_step_id: str | None
    completed_step_ids: tuple[str, ...] = ()
    pending_review_id: str | None = None
    pending_step_safety_review_id: str | None = None
    pending_user_input_id: str | None = None
    pre_observation_id: str | None = None
    post_observation_id: str | None = None
    failure_history: tuple[str, ...] = ()
    attempt_records: tuple[PlanAttempt, ...] = ()
    model_artifact_ids: dict[str, str] = field(default_factory=dict)
    stage: LoopStage = "init_loop"
    step_count: int = 0
    attempt_count: int = 0
    observation_level: ObservationLevel = "L0"
    selected_route_id: str | None = None
    selected_plan_id: str | None = None
    migrated_step_approval: MigratedStepApprovalBinding | None = None


@dataclass(frozen=True)
class PendingLoopReview:
    review_id: str
    review_kind: Literal["loop", "user_input"]
    reason: str
    step_id: str | None
    plan_id: str | None
    snapshot: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class GoalLoopResult:
    decision: LoopDecision
    state: LoopState
    message: str = ""
    review_id: str | None = None
    selected_target: str | None = None
    execution_status: str = "not_started"
    acceptance_status: str = "skipped"
    overall_status: str = "failed"
    payload: dict[str, Any] = field(default_factory=dict)
    attempt_records: tuple[PlanAttempt, ...] = ()
