from __future__ import annotations

from typing import TYPE_CHECKING

from .goal_ports import AcceptancePort, ExecutionPort, ObservationPort, PlanningPort, RecoveryPort, ReviewPort
from .loop_models import LoopObservation, LoopState, ObservationLevel
from .models import CommandRequest, PermissionReview, ReviewRequest
from .observation_service import ObservationService
from .planner import PlanningArtifacts
from .task_models import FailureClassification, PlanAttempt, PlanExecutionResult, ReplanDecision, StepExecutionResult, StepReviewRecord, TaskPlan, TaskStep

if TYPE_CHECKING:
    from .broker import CapabilityBroker


class BrokerPlanningPort(PlanningPort):
    def __init__(self, broker: CapabilityBroker) -> None:
        self._broker = broker

    def payload(self, planning: PlanningArtifacts) -> dict[str, object]:
        return self._broker._planning_payload(planning)

    def resolve_understanding_transition(self, planning: PlanningArtifacts, *, trigger: str) -> PlanningArtifacts:
        return self._broker._resolve_planning_understanding_transition(planning, trigger=trigger)

    def apply_replan_transition(
        self,
        planning: PlanningArtifacts,
        *,
        decision: ReplanDecision,
        failure: FailureClassification,
    ) -> PlanningArtifacts:
        return self._broker._planning_from_replan_decision(planning, decision=decision, failure=failure)

    def replan(
        self,
        planning: PlanningArtifacts,
        request: CommandRequest,
        excluded_route_ids: tuple[str, ...],
        excluded_capability_ids: tuple[str, ...],
        candidate_domain_ids: tuple[str, ...],
    ) -> PlanningArtifacts:
        return self._broker.plan_turn_from_loop(
            planning,
            request,
            excluded_route_ids,
            excluded_capability_ids,
            candidate_domain_ids,
        )


class BrokerObservationPort(ObservationPort):
    def __init__(self, broker: CapabilityBroker, service: ObservationService) -> None:
        self._broker = broker
        self._service = service

    def observe(
        self,
        *,
        plan: TaskPlan,
        step: TaskStep,
        phase: str,
        level: ObservationLevel,
    ) -> LoopObservation:
        return self._service.observe(plan=plan, step=step, phase=phase, level=level)

    def progressed(
        self,
        plan: TaskPlan,
        step: TaskStep,
        step_result: StepExecutionResult,
        pre_observation: LoopObservation,
        post_observation: LoopObservation,
        request: CommandRequest,
    ) -> bool:
        return self._broker._step_progressed(plan, step, step_result, pre_observation, post_observation, request)


class BrokerReviewPort(ReviewPort):
    def __init__(self, broker: CapabilityBroker) -> None:
        self._broker = broker

    def review_step(
        self,
        plan: TaskPlan,
        step: TaskStep,
        observation: LoopObservation | None,
    ) -> tuple[PermissionReview, StepReviewRecord]:
        return self._broker.review_task_step(plan, step, observation)

    def persist_step_review(
        self,
        utterance: str,
        planning: PlanningArtifacts,
        state: LoopState,
        step: TaskStep,
        reason: str,
    ) -> ReviewRequest:
        return self._broker.create_loop_review(
            utterance=utterance,
            planning=planning,
            loop_state=state,
            step=step,
            reason=reason,
        )

    def persist_user_input(
        self,
        utterance: str,
        planning: PlanningArtifacts,
        state: LoopState,
        reason: str,
    ) -> ReviewRequest:
        return self._broker.create_user_input_review(
            utterance=utterance,
            planning=planning,
            loop_state=state,
            reason=reason,
        )


class BrokerExecutionPort(ExecutionPort):
    def __init__(self, broker: CapabilityBroker) -> None:
        self._broker = broker

    def execute_step(self, plan: TaskPlan, step: TaskStep, request: CommandRequest, attempt_id: str) -> StepExecutionResult:
        return self._broker.execute_task_step(
            plan,
            step,
            dry_run=request.dry_run,
            transport=request.transport,
            review_id=request.review_id,
            attempt_id=attempt_id,
        )


class BrokerAcceptancePort(AcceptancePort):
    def __init__(self, broker: CapabilityBroker) -> None:
        self._broker = broker

    def assess(
        self,
        plan: TaskPlan,
        step_results: tuple[StepExecutionResult, ...],
        request: CommandRequest,
        _run_id: str,
        understanding_id: str | None,
        candidate_set_id: str | None,
        route_decision_id: str | None,
    ) -> PlanExecutionResult:
        return self._broker.assess_task_plan_execution(
            plan,
            step_results,
            dry_run=request.dry_run,
            understanding_id=understanding_id,
            candidate_set_id=candidate_set_id,
            route_decision_id=route_decision_id,
        )


class BrokerRecoveryPort(RecoveryPort):
    def __init__(self, broker: CapabilityBroker) -> None:
        self._broker = broker

    def classify(self, plan: TaskPlan, execution: PlanExecutionResult) -> FailureClassification:
        return self._broker.failure_classifier.classify(plan, execution)

    def decide(
        self,
        utterance: str,
        plan: TaskPlan,
        attempts: tuple[PlanAttempt, ...],
        failure: FailureClassification,
        understanding_id: str | None,
        candidate_set_id: str | None,
        available_domain_ids: tuple[str, ...],
    ) -> ReplanDecision:
        return self._broker.replanner.decide(
            utterance=utterance,
            current_plan=plan,
            attempts=attempts,
            failure=failure,
            understanding_id=understanding_id,
            candidate_set_id=candidate_set_id,
            available_domain_ids=available_domain_ids,
        )
