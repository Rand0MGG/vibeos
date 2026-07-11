from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from .loop_models import LoopObservation, LoopPolicy, LoopState, ObservationLevel
from .models import CommandRequest, PermissionReview, ReviewRequest
from .planner import PlanningArtifacts
from .task_models import FailureClassification, PlanAttempt, PlanExecutionResult, ReplanDecision, StepExecutionResult, StepReviewRecord, TaskPlan, TaskStep


class PlanningPort(Protocol):
    """Owns planning artifacts and their controlled transitions."""

    def payload(self, planning: PlanningArtifacts) -> dict[str, object]: ...

    def resolve_understanding_transition(self, planning: PlanningArtifacts, *, trigger: str) -> PlanningArtifacts: ...

    def apply_replan_transition(
        self,
        planning: PlanningArtifacts,
        *,
        decision: ReplanDecision,
        failure: FailureClassification,
    ) -> PlanningArtifacts: ...

    def replan(
        self,
        planning: PlanningArtifacts,
        request: CommandRequest,
        excluded_route_ids: tuple[str, ...],
        excluded_capability_ids: tuple[str, ...],
        candidate_domain_ids: tuple[str, ...],
    ) -> PlanningArtifacts: ...


class ObservationPort(Protocol):
    """Collects bounded pre/post execution observations."""

    def observe(
        self,
        *,
        plan: TaskPlan,
        step: TaskStep,
        phase: str,
        level: ObservationLevel,
    ) -> LoopObservation: ...

    def progressed(
        self,
        plan: TaskPlan,
        step: TaskStep,
        step_result: StepExecutionResult,
        pre_observation: LoopObservation,
        post_observation: LoopObservation,
        request: CommandRequest,
    ) -> bool: ...


class ReviewPort(Protocol):
    """Decides per-step safety and persists suspendable review state."""

    def review_step(
        self,
        plan: TaskPlan,
        step: TaskStep,
        observation: LoopObservation | None,
    ) -> tuple[PermissionReview, StepReviewRecord]: ...

    def persist_step_review(
        self,
        utterance: str,
        planning: PlanningArtifacts,
        state: LoopState,
        step: TaskStep,
        reason: str,
    ) -> ReviewRequest: ...

    def persist_user_input(
        self,
        utterance: str,
        planning: PlanningArtifacts,
        state: LoopState,
        reason: str,
    ) -> ReviewRequest: ...


class ExecutionPort(Protocol):
    """Executes one already-reviewed, registered task step."""

    def execute_step(
        self,
        plan: TaskPlan,
        step: TaskStep,
        request: CommandRequest,
        attempt_id: str,
    ) -> StepExecutionResult: ...


class AcceptancePort(Protocol):
    """Aggregates verified step receipts into a plan-level outcome."""

    def assess(
        self,
        plan: TaskPlan,
        step_results: tuple[StepExecutionResult, ...],
        request: CommandRequest,
        run_id: str,
        understanding_id: str | None,
        candidate_set_id: str | None,
        route_decision_id: str | None,
    ) -> PlanExecutionResult: ...


class RecoveryPort(Protocol):
    """Classifies failures and selects bounded recovery actions."""

    def classify(self, plan: TaskPlan, execution: PlanExecutionResult) -> FailureClassification: ...

    def decide(
        self,
        utterance: str,
        plan: TaskPlan,
        attempts: tuple[PlanAttempt, ...],
        failure: FailureClassification,
        understanding_id: str | None,
        candidate_set_id: str | None,
        available_domain_ids: tuple[str, ...],
    ) -> ReplanDecision: ...


@dataclass(frozen=True)
class GoalLoopPorts:
    """The small, typed host-owned boundary of the GoalLoop state machine."""

    planning: PlanningPort
    observation: ObservationPort
    review: ReviewPort
    execution: ExecutionPort
    acceptance: AcceptancePort
    recovery: RecoveryPort
    policy: LoopPolicy
