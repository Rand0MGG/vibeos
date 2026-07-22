from __future__ import annotations

from dataclasses import dataclass

from .core.domain import TaskRun
from .models import CommandRequest, EffectAssessment
from .planning_models import PlanningArtifacts
from .task_models import PlanAttempt, PlanExecutionResult, StepExecutionResult


@dataclass(frozen=True)
class TaskEnginePolicy:
    max_attempts: int = 4
    max_steps: int = 8
    lease_seconds: int = 90
    heartbeat_seconds: float = 15.0
    retry_delay_seconds: int = 5
    task_timeout_seconds: int = 6 * 60 * 60
    default_observation_level: str = "O0"


@dataclass(frozen=True)
class DurableTaskResult:
    task: TaskRun
    request: CommandRequest
    planning: PlanningArtifacts | None
    step_results: tuple[StepExecutionResult, ...] = ()
    attempts: tuple[PlanAttempt, ...] = ()
    execution: PlanExecutionResult | None = None
    review: EffectAssessment | None = None
    review_id: str | None = None
    message: str = ""
    selected_target: str | None = None
    execution_status: str = "not_started"
    acceptance_status: str = "skipped"
    overall_status: str = "failed"
