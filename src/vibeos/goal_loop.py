from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from hashlib import sha256
from typing import Any

from .candidate_selection import CandidateSelectionDecision
from .loop_models import GoalLoopResult, LoopObservation, LoopPolicy, LoopState, MigratedStepApprovalBinding
from .goal_ports import GoalLoopPorts
from .loop_policy import enforce_replan_policy, loop_budget_exhausted, next_observation_level
from .models import CommandRequest, utc_now_iso
from .observation_service import observation_progressed
from .planner import PlanningArtifacts
from .run_context import RunContext
from .task_models import (
    AcceptanceStatus,
    ExecutionStatus,
    FailureClassification,
    OverallStatus,
    PlanAttempt,
    PlanExecutionResult,
    ReplanDecision,
    StepExecutionResult,
    TaskPlan,
    TaskStep,
    UtteranceAnalysis,
    canonicalize_target_for_action,
    task_plan_from_payload,
)
from .task_trace import record_trace_event


@dataclass(frozen=True)
class _StepReviewTransition:
    state: LoopState
    outcome: GoalLoopResult | None = None


@dataclass(frozen=True)
class _ExecutedStepTransition:
    state: LoopState
    attempt_id: str
    step_result: StepExecutionResult
    post_observation: LoopObservation


class GoalLoop:
    def __init__(self, *, ports: GoalLoopPorts) -> None:
        self.ports = ports
        self.planning = ports.planning
        self.observation = ports.observation
        self.review = ports.review
        self.execution = ports.execution
        self.acceptance = ports.acceptance
        self.recovery = ports.recovery
        self.policy = ports.policy

    def run(
        self,
        *,
        request: CommandRequest,
        planning: PlanningArtifacts,
        run_id: str,
        goal_id: str,
        state: LoopState | None = None,
        step_results: tuple[StepExecutionResult, ...] = (),
        attempts: tuple[PlanAttempt, ...] = (),
    ) -> GoalLoopResult:
        context = RunContext.from_request(request, run_id=run_id, goal_id=goal_id)
        state = state or self._initial_state(planning=planning, run_id=run_id, goal_id=goal_id)
        state = sync_loop_state_with_planning(state, planning)
        attempts_list: list[PlanAttempt] = list(attempts or state.attempt_records)
        restored_results = _restored_step_results(
            plan=planning.plan,
            completed_step_ids=state.completed_step_ids,
            attempts=tuple(attempts_list),
        )
        step_results_list: list[StepExecutionResult] = list(step_results or restored_results)
        excluded_route_ids: tuple[str, ...] = ()
        excluded_capability_ids: tuple[str, ...] = ()
        candidate_domain_ids: tuple[str, ...] = ()
        trigger = "initial_plan"

        record_trace_event(
            phase="goal_loop",
            event_type="loop_started",
            status="running",
            actor="goal_loop",
            goal_id=goal_id,
            data=asdict(state),
        )

        while True:
            planning = self.planning.resolve_understanding_transition(planning, trigger=trigger)
            state = sync_loop_state_with_planning(state, planning)
            payload = self.planning.payload(planning)
            plan = planning.plan
            analysis = planning.analysis
            route_decision = planning.route_decision

            if plan is None:
                return self._handle_missing_plan(
                    request=request,
                    planning=planning,
                    state=state,
                    analysis=analysis,
                    route_decision=route_decision,
                    payload=payload,
                    attempts=tuple(attempts_list),
                    goal_id=goal_id,
                )

            if loop_budget_exhausted(state, self.policy):
                return self._budget_exhausted_result(state=state, payload=payload, attempts=tuple(attempts_list), goal_id=goal_id)

            next_step = _next_step(plan, state.completed_step_ids)
            if next_step is None:
                execution = self.acceptance.assess(
                    plan,
                    tuple(step_results_list),
                    request,
                    run_id,
                    state.primary_understanding_id,
                    state.candidate_set_id,
                    state.selected_route_decision_id,
                )
                payload["execution"] = asdict(execution)
                failure = self.recovery.classify(plan, execution)
                terminal_message = execution.error or (
                    (execution.acceptance_result or {}).get("message", "") if isinstance(execution.acceptance_result, dict) else ""
                )
                if failure.failure_class == "none":
                    terminal_state = replace(state, stage="complete")
                    _record_loop_completed(
                        goal_id=goal_id,
                        state=terminal_state,
                        overall_status=execution.overall_status,
                        message=terminal_message,
                        plan_id=plan.plan_id,
                    )
                    return GoalLoopResult(
                        decision="complete",
                        state=terminal_state,
                        message=terminal_message,
                        selected_target=_selected_target(step_results_list),
                        execution_status=execution.execution_status,
                        acceptance_status=execution.acceptance_status,
                        overall_status=execution.overall_status,
                        payload=payload,
                        attempt_records=tuple(attempts_list),
                    )

                attempt_id = _make_attempt_id(run_id, len(attempts_list) + 1, plan.selected_route_id or "plan_complete")
                current_attempt = _execution_attempt_record(
                    run_id=run_id,
                    attempt_index=len(attempts_list) + 1,
                    trigger=trigger,
                    plan=plan,
                    execution_result=execution,
                    understanding_id=state.primary_understanding_id,
                    candidate_set_id=state.candidate_set_id,
                    route_decision_id=state.selected_route_decision_id,
                    attempt_id=attempt_id,
                    failure=failure,
                )
                state = replace(state, failure_history=(*state.failure_history, failure.failure_class), stage="decide")
                record_trace_event(
                    phase="goal_loop",
                    event_type="failure_classified",
                    status=failure.failure_class,
                    actor="goal_loop",
                    goal_id=goal_id,
                    plan_id=plan.plan_id,
                    attempt_id=attempt_id,
                    data=asdict(failure),
                )
                decision = self.recovery.decide(
                    request.utterance,
                    plan,
                    (*tuple(attempts_list), current_attempt),
                    failure,
                    state.primary_understanding_id,
                    state.candidate_set_id,
                    tuple(dict.fromkeys(route.domain_id for route in plan.routes if route.domain_id)),
                )
                decision = enforce_replan_policy(policy=self.policy, state=state, failure=failure, decision=decision)
                _record_recovery_decision(
                    goal_id=goal_id,
                    plan_id=plan.plan_id,
                    attempt_id=attempt_id,
                    step_id=None,
                    decision=decision,
                )
                attempts_list.append(replace(current_attempt, replan_decision=decision))
                state = replace(state, attempt_records=tuple(attempts_list))
                if decision.action == "ask_user":
                    review_request = self.review.persist_user_input(
                        request.utterance,
                        planning,
                        replace(state, stage="needs_user_input"),
                        decision.reason or failure.message or terminal_message,
                    )
                    state = replace(state, pending_user_input_id=review_request.review_id, stage="needs_user_input")
                    return GoalLoopResult(
                        decision="needs_user_input",
                        state=state,
                        message=review_request.pending_reason or decision.reason or terminal_message,
                        review_id=review_request.review_id,
                        selected_target=_selected_target(step_results_list),
                        execution_status=execution.execution_status,
                        acceptance_status=execution.acceptance_status,
                        overall_status="needs_user_input",
                        payload=payload,
                        attempt_records=tuple(attempts_list),
                    )
                if decision.action == "stop":
                    overall_status = (
                        execution.overall_status
                        if failure.failure_class in {"acceptance_unverified", "acceptance_failed"}
                        else ("blocked" if failure.failure_class in {"same_action_no_progress", "state_changed_externally"} else "failed")
                    )
                    terminal_state = replace(state, stage="blocked")
                    _record_loop_completed(
                        goal_id=goal_id,
                        state=terminal_state,
                        overall_status=overall_status,
                        message=decision.reason or failure.message or terminal_message,
                        plan_id=plan.plan_id,
                    )
                    return GoalLoopResult(
                        decision="blocked",
                        state=terminal_state,
                        message=decision.reason or failure.message or terminal_message,
                        selected_target=_selected_target(step_results_list),
                        execution_status=execution.execution_status,
                        acceptance_status=execution.acceptance_status,
                        overall_status=overall_status,
                        payload=payload,
                        attempt_records=tuple(attempts_list),
                    )
                if decision.action in {"retry_same_attempt", "repair"}:
                    state = _reset_for_same_plan_recovery(state, self.policy, repair=decision.action == "repair")
                    step_results_list = []
                    trigger = decision.action
                    continue
                planning = self.planning.apply_replan_transition(planning, decision=decision, failure=failure)
                excluded_route_ids = tuple(dict.fromkeys((*excluded_route_ids, *decision.do_not_repeat_route_ids)))
                excluded_capability_ids = tuple(dict.fromkeys((*excluded_capability_ids, *decision.do_not_repeat_capability_ids)))
                candidate_domain_ids = tuple(dict.fromkeys((*candidate_domain_ids, *decision.candidate_domain_ids)))
                planning = self.planning.replan(planning, request, excluded_route_ids, excluded_capability_ids, candidate_domain_ids)
                state, step_results_list = _restore_replanned_state(state=state, planning=planning, attempts=tuple(attempts_list))
                trigger = decision.action
                continue

            state = replace(state, current_step_id=next_step.id, selected_plan_id=plan.plan_id, selected_route_id=plan.selected_route_id, stage="observe_pre")
            record_trace_event(
                phase="goal_loop",
                event_type="step_selected",
                status="selected",
                actor="goal_loop",
                goal_id=goal_id,
                plan_id=plan.plan_id,
                step_id=next_step.id,
                data={"completed_step_ids": list(state.completed_step_ids)},
            )
            pre_observation = self.observation.observe(plan=plan, step=next_step, phase="pre", level=state.observation_level)
            state = replace(state, pre_observation_id=pre_observation.observation_id)
            record_trace_event(
                phase="goal_loop",
                event_type="observe_pre_completed",
                status=pre_observation.level,
                actor="goal_loop",
                goal_id=goal_id,
                step_id=next_step.id,
                data=asdict(pre_observation),
            )

            review_transition = self._review_or_suspend_step(
                request=request,
                planning=planning,
                state=state,
                plan=plan,
                step=next_step,
                observation=pre_observation,
                payload=payload,
                attempts=tuple(attempts_list),
                goal_id=goal_id,
            )
            if review_transition.outcome is not None:
                return review_transition.outcome
            state = review_transition.state

            executed = self._execute_and_observe_step(
                context=context,
                request=request,
                state=state,
                plan=plan,
                step=next_step,
                run_id=run_id,
                step_result_count=state.step_count,
                goal_id=goal_id,
            )
            state = executed.state
            attempt_id = executed.attempt_id
            step_result = executed.step_result
            post_observation = executed.post_observation
            state = replace(state, stage="verify")
            progress_made = self.observation.progressed(
                plan,
                next_step,
                step_result,
                pre_observation,
                post_observation,
                request,
            )
            if step_result.status == "succeeded" and progress_made:
                # This collection is the acceptance receipt set, not attempt
                # history. Failed/no-progress receipts remain in
                # ``attempt_records`` below and must never poison final
                # acceptance after a later repair succeeds.
                step_results_list.append(step_result)
                attempts_list.append(
                    _attempt_record(
                        run_id=run_id,
                        attempt_index=len(attempts_list) + 1,
                        trigger=trigger,
                        plan=plan,
                        step_result=step_result,
                        execution_result=_partial_execution(plan, step_result, progress_made=True),
                        understanding_id=state.primary_understanding_id,
                        candidate_set_id=state.candidate_set_id,
                        route_decision_id=state.selected_route_decision_id,
                        attempt_id=attempt_id,
                    )
                )
                completed_step_ids = (*state.completed_step_ids, next_step.id)
                state = replace(state, completed_step_ids=completed_step_ids, attempt_records=tuple(attempts_list))
                record_trace_event(
                    phase="goal_loop",
                    event_type="step_verified",
                    status="progressed",
                    actor="goal_loop",
                    goal_id=goal_id,
                    step_id=next_step.id,
                    data={"completed_step_ids": list(completed_step_ids)},
                )
                trigger = "continue"
                continue

            partial_execution = _partial_execution(plan, step_result, progress_made)
            failure = self.recovery.classify(plan, partial_execution)
            current_attempt = _attempt_record(
                run_id=run_id,
                attempt_index=len(attempts_list) + 1,
                trigger=trigger,
                plan=plan,
                step_result=step_result,
                execution_result=partial_execution,
                failure=failure,
                understanding_id=state.primary_understanding_id,
                candidate_set_id=state.candidate_set_id,
                route_decision_id=state.selected_route_decision_id,
                attempt_id=attempt_id,
            )
            state = replace(
                state,
                failure_history=(*state.failure_history, failure.failure_class),
                observation_level=next_observation_level(state, self.policy, escalate=True),
                stage="decide",
            )
            record_trace_event(
                phase="goal_loop",
                event_type="failure_classified",
                status=failure.failure_class,
                actor="goal_loop",
                goal_id=goal_id,
                step_id=next_step.id,
                data=asdict(failure),
            )
            decision = self.recovery.decide(
                request.utterance,
                plan,
                (*tuple(attempts_list), current_attempt),
                failure,
                state.primary_understanding_id,
                state.candidate_set_id,
                tuple(dict.fromkeys(route.domain_id for route in plan.routes if route.domain_id)),
            )
            decision = enforce_replan_policy(policy=self.policy, state=state, failure=failure, decision=decision)
            _record_recovery_decision(
                goal_id=goal_id,
                plan_id=plan.plan_id,
                attempt_id=attempt_id,
                step_id=next_step.id,
                decision=decision,
            )
            attempts_list.append(replace(current_attempt, replan_decision=decision))
            state = replace(state, attempt_records=tuple(attempts_list))
            if decision.action == "ask_user":
                review_request = self.review.persist_user_input(
                    request.utterance, planning, replace(state, stage="needs_user_input"), decision.reason or failure.message
                )
                state = replace(state, pending_user_input_id=review_request.review_id, stage="needs_user_input")
                return GoalLoopResult(
                    decision="needs_user_input",
                    state=state,
                    message=review_request.pending_reason or decision.reason,
                    review_id=review_request.review_id,
                    execution_status="not_started",
                    acceptance_status="skipped",
                    overall_status="needs_user_input",
                    payload=payload,
                    attempt_records=tuple(attempts_list),
                )
            if decision.action == "stop":
                overall_status = "blocked" if failure.failure_class in {"same_action_no_progress", "state_changed_externally"} else "failed"
                blocked_state = replace(state, stage="blocked")
                _record_loop_completed(
                    goal_id=goal_id,
                    state=blocked_state,
                    overall_status=overall_status,
                    message=decision.reason or failure.message,
                    plan_id=plan.plan_id,
                    step_id=next_step.id,
                )
                return GoalLoopResult(
                    decision="blocked",
                    state=blocked_state,
                    message=decision.reason or failure.message,
                    selected_target=_selected_target(step_results_list),
                    execution_status="failed" if step_result.status != "succeeded" else "succeeded",
                    acceptance_status="failed" if failure.failure_class == "same_action_no_progress" else "skipped",
                    overall_status=overall_status,
                    payload=payload,
                    attempt_records=tuple(attempts_list),
                )
            if decision.action in {"retry_same_attempt", "repair"}:
                if decision.action == "repair":
                    state = replace(state, observation_level=next_observation_level(state, self.policy, escalate=True))
                trigger = decision.action
                continue
            planning = self.planning.apply_replan_transition(planning, decision=decision, failure=failure)
            excluded_route_ids = tuple(dict.fromkeys((*excluded_route_ids, *decision.do_not_repeat_route_ids)))
            excluded_capability_ids = tuple(dict.fromkeys((*excluded_capability_ids, *decision.do_not_repeat_capability_ids)))
            candidate_domain_ids = tuple(dict.fromkeys((*candidate_domain_ids, *decision.candidate_domain_ids)))
            planning = self.planning.replan(planning, request, excluded_route_ids, excluded_capability_ids, candidate_domain_ids)
            state, step_results_list = _restore_replanned_state(state=state, planning=planning, attempts=tuple(attempts_list))
            trigger = decision.action

    def _review_or_suspend_step(
        self,
        *,
        request: CommandRequest,
        planning: PlanningArtifacts,
        state: LoopState,
        plan: TaskPlan,
        step: TaskStep,
        observation: LoopObservation,
        payload: dict[str, object],
        attempts: tuple[PlanAttempt, ...],
        goal_id: str,
    ) -> _StepReviewTransition:
        state = replace(state, stage="step_review")
        review, step_review = self.review.review_step(plan, step, observation)
        record_trace_event(
            phase="goal_loop",
            event_type="step_review_completed",
            status="allowed" if review.allowed and not review.review_required else ("review_required" if review.review_required else "rejected"),
            actor="goal_loop",
            goal_id=goal_id,
            step_id=step.id,
            data=asdict(step_review),
        )
        approved_pending_review = (
            bool(request.approve)
            and request.review_id is not None
            and request.review_id == state.pending_review_id
            and state.pending_step_safety_review_id == step_review.step_safety_review_id
        )
        migrated_approval = state.migrated_step_approval
        approved_migrated_review = (
            bool(request.approve)
            and request.review_id is not None
            and migrated_approval is not None
            and request.review_id == migrated_approval.review_id == state.pending_review_id
            and step.id == migrated_approval.step_id
            and step.action == migrated_approval.action
            and review.allowed == migrated_approval.allowed
            and review.review_required == migrated_approval.review_required
            and review.risk_level == migrated_approval.risk_level
            and review.reason == migrated_approval.reason
        )
        approved_pending_review = approved_pending_review or approved_migrated_review
        if not review.allowed:
            blocked_state = replace(state, stage="blocked")
            _record_loop_completed(
                goal_id=goal_id,
                state=blocked_state,
                overall_status="failed",
                message=review.reason,
                plan_id=plan.plan_id,
                step_id=step.id,
            )
            return _StepReviewTransition(
                state=blocked_state,
                outcome=GoalLoopResult(
                    decision="blocked",
                    state=blocked_state,
                    message=review.reason,
                    execution_status="not_started",
                    acceptance_status="skipped",
                    overall_status="failed",
                    payload=payload,
                    attempt_records=attempts,
                ),
            )
        if review.review_required and not approved_pending_review:
            pending_review_state = replace(
                state,
                pending_step_safety_review_id=step_review.step_safety_review_id,
                stage="needs_review",
            )
            review_request = self.review.persist_step_review(
                request.utterance,
                planning,
                pending_review_state,
                step,
                review.reason,
            )
            suspended_state = replace(pending_review_state, pending_review_id=review_request.review_id)
            record_trace_event(
                phase="goal_loop",
                event_type="loop_suspended",
                status="needs_review",
                actor="goal_loop",
                goal_id=goal_id,
                review_id=review_request.review_id,
                step_id=step.id,
                data=asdict(suspended_state),
            )
            return _StepReviewTransition(
                state=suspended_state,
                outcome=GoalLoopResult(
                    decision="needs_review",
                    state=suspended_state,
                    message=review.reason,
                    review_id=review_request.review_id,
                    execution_status="not_started",
                    acceptance_status="skipped",
                    overall_status="needs_review",
                    payload=payload,
                    attempt_records=attempts,
                ),
            )
        if approved_pending_review:
            state = replace(state, pending_review_id=None, pending_step_safety_review_id=None, migrated_step_approval=None)
        return _StepReviewTransition(state=state)

    def _execute_and_observe_step(
        self,
        *,
        context: RunContext,
        request: CommandRequest,
        state: LoopState,
        plan: TaskPlan,
        step: TaskStep,
        run_id: str,
        step_result_count: int,
        goal_id: str,
    ) -> _ExecutedStepTransition:
        state = replace(state, stage="act")
        attempt_id = _make_attempt_id(run_id, step_result_count + 1, step.id)
        step_result = self.execution.execute_step(context, plan, step, request, attempt_id)
        state = replace(state, attempt_count=state.attempt_count + 1, step_count=state.step_count + 1)
        record_trace_event(
            phase="goal_loop",
            event_type="step_executed",
            status=step_result.status,
            actor="goal_loop",
            goal_id=goal_id,
            step_id=step.id,
            attempt_id=attempt_id,
            data=asdict(step_result),
        )
        state = replace(state, stage="observe_post")
        post_observation = self.observation.observe(
            plan=plan,
            step=step,
            phase="post",
            level=next_observation_level(state, self.policy, escalate=step_result.status != "succeeded"),
            attempt_id=attempt_id,
        )
        state = replace(state, post_observation_id=post_observation.observation_id)
        record_trace_event(
            phase="goal_loop",
            event_type="observe_post_completed",
            status=post_observation.level,
            actor="goal_loop",
            goal_id=goal_id,
            step_id=step.id,
            data=asdict(post_observation),
        )
        return _ExecutedStepTransition(
            state=state,
            attempt_id=attempt_id,
            step_result=step_result,
            post_observation=post_observation,
        )

    def resume_from_review(
        self,
        *,
        request: CommandRequest,
        planning: PlanningArtifacts,
        state: LoopState,
        run_id: str,
        goal_id: str,
    ) -> GoalLoopResult:
        resumed_state = sync_loop_state_with_planning(state, planning)
        _record_loop_resumed(
            goal_id=goal_id,
            state=resumed_state,
            review_id=request.review_id,
            resume_kind="review",
        )
        resumed_request = replace(request, approve=True)
        return self.run(
            request=resumed_request,
            planning=planning,
            run_id=run_id,
            goal_id=goal_id,
            state=replace(resumed_state, stage="step_review"),
            attempts=resumed_state.attempt_records,
        )

    def resume_from_user_input(
        self,
        *,
        request: CommandRequest,
        planning: PlanningArtifacts,
        state: LoopState,
        run_id: str,
        goal_id: str,
    ) -> GoalLoopResult:
        resumed_state = sync_loop_state_with_planning(state, planning)
        _record_loop_resumed(
            goal_id=goal_id,
            state=resumed_state,
            review_id=request.review_id,
            resume_kind="user_input",
        )
        return self.run(
            request=request,
            planning=planning,
            run_id=run_id,
            goal_id=goal_id,
            state=replace(resumed_state, pending_user_input_id=None, stage="init_loop"),
            attempts=resumed_state.attempt_records,
        )

    def _handle_missing_plan(
        self,
        *,
        request: CommandRequest,
        planning: PlanningArtifacts,
        state: LoopState,
        analysis: UtteranceAnalysis,
        route_decision: CandidateSelectionDecision | None,
        payload: dict[str, object],
        attempts: tuple[PlanAttempt, ...],
        goal_id: str,
    ) -> GoalLoopResult:
        route_action = route_decision.action if route_decision is not None else None
        if analysis.type == "clarification" or route_action == "clarify":
            reason = analysis.chat_response or analysis.explanation or "clarification required"
            review_request = self.review.persist_user_input(request.utterance, planning, replace(state, stage="needs_user_input"), reason)
            suspended_state = replace(state, pending_user_input_id=review_request.review_id, stage="needs_user_input")
            record_trace_event(
                phase="goal_loop",
                event_type="loop_suspended",
                status="needs_user_input",
                actor="goal_loop",
                goal_id=goal_id,
                review_id=review_request.review_id,
                data=asdict(suspended_state),
            )
            return GoalLoopResult(
                decision="needs_user_input",
                state=suspended_state,
                message=review_request.pending_reason or "clarification required",
                review_id=review_request.review_id,
                execution_status="not_started",
                acceptance_status="skipped",
                overall_status="needs_user_input",
                payload=payload,
                attempt_records=attempts,
            )
        overall_status = "blocked" if route_action == "blocked" else "failed"
        message = (route_decision.reason if route_decision is not None else "") or analysis.explanation or "planner did not produce a task plan"
        terminal_state = replace(state, stage="blocked" if overall_status == "blocked" else "complete")
        _record_loop_completed(goal_id=goal_id, state=terminal_state, overall_status=overall_status, message=message)
        return GoalLoopResult(
            decision="blocked" if overall_status == "blocked" else "complete",
            state=terminal_state,
            message=message,
            execution_status="not_started",
            acceptance_status="skipped",
            overall_status=overall_status,
            payload=payload,
            attempt_records=attempts,
        )

    def _budget_exhausted_result(
        self,
        *,
        state: LoopState,
        payload: dict[str, object],
        attempts: tuple[PlanAttempt, ...],
        goal_id: str,
    ) -> GoalLoopResult:
        terminal_state = replace(state, stage="budget_exhausted")
        _record_loop_completed(goal_id=goal_id, state=terminal_state, overall_status="blocked", message="goal loop budget exhausted")
        return GoalLoopResult(
            decision="budget_exhausted",
            state=terminal_state,
            message="goal loop budget exhausted",
            execution_status="failed",
            acceptance_status="skipped",
            overall_status="blocked",
            payload=payload,
            attempt_records=attempts,
        )

    def _initial_state(self, *, planning: PlanningArtifacts, run_id: str, goal_id: str) -> LoopState:
        understanding = planning.understanding
        candidate_set = planning.candidate_set
        route_decision = planning.route_decision
        model_artifact_ids: dict[str, str] = {}
        if understanding is not None:
            model_artifact_ids["understanding_id"] = understanding.understanding_id
        if route_decision is not None:
            model_artifact_ids["route_decision_id"] = route_decision.route_decision_id
        if candidate_set is not None:
            model_artifact_ids["candidate_set_id"] = candidate_set.candidate_set_id
        return LoopState(
            loop_snapshot_id=_make_snapshot_id(run_id, goal_id),
            trace_run_id=run_id,
            goal_id=goal_id,
            primary_understanding_id=(understanding.primary_understanding_id or understanding.understanding_id) if understanding is not None else None,
            candidate_set_id=candidate_set.candidate_set_id if candidate_set is not None else None,
            selected_route_decision_id=route_decision.route_decision_id if route_decision is not None else None,
            current_step_id=None,
            model_artifact_ids=model_artifact_ids,
        )


def _next_step(plan: TaskPlan, completed_step_ids: tuple[str, ...]) -> TaskStep | None:
    completed = set(completed_step_ids)
    for step in plan.steps:
        if step.id in completed:
            continue
        if all(dependency in completed for dependency in step.depends_on):
            return step
    return None


def _default_step_progressed(
    _plan: TaskPlan,
    _step: TaskStep,
    step_result: StepExecutionResult,
    pre_observation: LoopObservation,
    post_observation: LoopObservation,
    request: CommandRequest,
) -> bool:
    """Default to evidence from observation, with dry-runs completing by design.

    The broker may provide a narrower route-aware policy. Keeping this default
    strict is important for direct GoalLoop users that do not declare a route
    acceptance contract.
    """

    return bool(step_result.status == "succeeded" and (request.dry_run or observation_progressed(pre_observation, post_observation)))


def _partial_execution(plan: TaskPlan, step_result: StepExecutionResult, progress_made: bool) -> PlanExecutionResult:
    acceptance_result: dict[str, Any] | None = None
    acceptance_status: AcceptanceStatus = "skipped"
    execution_status: ExecutionStatus = "failed" if step_result.status != "succeeded" else "succeeded"
    overall_status: OverallStatus = "failed" if step_result.status != "succeeded" else "incomplete"
    if step_result.status == "succeeded" and not progress_made:
        acceptance_result = {
            "same_action_no_progress": True,
            "message": "step executed successfully but the observed state did not change",
        }
        acceptance_status = "failed"
    return PlanExecutionResult(
        plan_id=plan.plan_id,
        status="succeeded" if step_result.status == "succeeded" else "failed",
        step_results=(step_result,),
        execution_status=execution_status,
        acceptance_status=acceptance_status,
        overall_status=overall_status,
        acceptance_result=acceptance_result,
        error=step_result.error,
    )


def _selected_target(step_results: list[StepExecutionResult]) -> str | None:
    for step in reversed(step_results):
        target = step.diagnostics.get("selected_target") or step.result.get("selected_target") or step.result.get("uri")
        if target is not None:
            return str(target)
    return None


def _make_snapshot_id(run_id: str, goal_id: str) -> str:
    digest = sha256(f"{run_id}:{goal_id}:{utc_now_iso()}".encode("utf-8")).hexdigest()[:12]
    return f"lsnap_{digest}"


def _make_attempt_id(run_id: str, attempt_index: int, step_id: str) -> str:
    digest = sha256(f"{run_id}:{attempt_index}:{step_id}".encode("utf-8")).hexdigest()[:10]
    return f"attempt_{digest}"


def normalize_state_for_plan(state: LoopState, plan: TaskPlan | None) -> LoopState:
    if plan is None:
        return replace(state, completed_step_ids=(), current_step_id=None)
    compatible_completed = tuple(step_id for step_id in state.completed_step_ids if _attempt_supports_step(plan, step_id, state.attempt_records))
    current_step_id = state.current_step_id if state.current_step_id and _attempt_supports_step(plan, state.current_step_id, state.attempt_records) else None
    return replace(state, completed_step_ids=compatible_completed, current_step_id=current_step_id)


def sync_loop_state_with_planning(state: LoopState, planning: PlanningArtifacts) -> LoopState:
    understanding = planning.understanding
    candidate_set = planning.candidate_set
    route_decision = planning.route_decision
    plan = planning.plan
    model_artifact_ids: dict[str, str] = {}
    if understanding is not None:
        model_artifact_ids["understanding_id"] = understanding.understanding_id
    if route_decision is not None:
        model_artifact_ids["route_decision_id"] = route_decision.route_decision_id
    if candidate_set is not None:
        model_artifact_ids["candidate_set_id"] = candidate_set.candidate_set_id
    return replace(
        state,
        primary_understanding_id=(understanding.primary_understanding_id or understanding.understanding_id) if understanding is not None else None,
        candidate_set_id=candidate_set.candidate_set_id if candidate_set is not None else None,
        selected_route_decision_id=route_decision.route_decision_id if route_decision is not None else None,
        selected_route_id=plan.selected_route_id if plan is not None else state.selected_route_id,
        selected_plan_id=plan.plan_id if plan is not None else state.selected_plan_id,
        model_artifact_ids=model_artifact_ids,
    )


def loop_state_from_payload(payload: dict[str, Any]) -> LoopState:
    return LoopState(
        loop_snapshot_id=str(payload["loop_snapshot_id"]),
        trace_run_id=str(payload["trace_run_id"]),
        goal_id=str(payload["goal_id"]),
        primary_understanding_id=str(payload["primary_understanding_id"]) if payload.get("primary_understanding_id") is not None else None,
        candidate_set_id=str(payload["candidate_set_id"]) if payload.get("candidate_set_id") is not None else None,
        selected_route_decision_id=str(payload["selected_route_decision_id"]) if payload.get("selected_route_decision_id") is not None else None,
        current_step_id=str(payload["current_step_id"]) if payload.get("current_step_id") is not None else None,
        completed_step_ids=tuple(str(item) for item in payload.get("completed_step_ids", ())),
        pending_review_id=str(payload["pending_review_id"]) if payload.get("pending_review_id") is not None else None,
        pending_step_safety_review_id=(str(payload["pending_step_safety_review_id"]) if payload.get("pending_step_safety_review_id") is not None else None),
        pending_user_input_id=str(payload["pending_user_input_id"]) if payload.get("pending_user_input_id") is not None else None,
        pre_observation_id=str(payload["pre_observation_id"]) if payload.get("pre_observation_id") is not None else None,
        post_observation_id=str(payload["post_observation_id"]) if payload.get("post_observation_id") is not None else None,
        failure_history=tuple(str(item) for item in payload.get("failure_history", ())),
        attempt_records=tuple(plan_attempt_from_payload(item) for item in payload.get("attempt_records", ())),
        model_artifact_ids={str(key): str(value) for key, value in (payload.get("model_artifact_ids", {}) or {}).items()},
        stage=str(payload.get("stage", "init_loop")),
        step_count=int(payload.get("step_count", 0)),
        attempt_count=int(payload.get("attempt_count", 0)),
        observation_level=str(payload.get("observation_level", "L0")),
        selected_route_id=str(payload["selected_route_id"]) if payload.get("selected_route_id") is not None else None,
        selected_plan_id=str(payload["selected_plan_id"]) if payload.get("selected_plan_id") is not None else None,
        migrated_step_approval=_migrated_step_approval_from_payload(payload.get("migrated_step_approval")),
    )


def _migrated_step_approval_from_payload(payload: object) -> MigratedStepApprovalBinding | None:
    if not isinstance(payload, dict):
        return None
    required = ("review_id", "step_id", "action", "original_safety_review_id", "risk_level", "reason")
    if any(not isinstance(payload.get(field), str) or not payload.get(field) for field in required):
        return None
    return MigratedStepApprovalBinding(
        review_id=str(payload["review_id"]),
        step_id=str(payload["step_id"]),
        action=str(payload["action"]),
        original_safety_review_id=str(payload["original_safety_review_id"]),
        risk_level=str(payload["risk_level"]),
        review_required=bool(payload.get("review_required")),
        allowed=bool(payload.get("allowed")),
        reason=str(payload["reason"]),
    )


def plan_attempt_from_payload(payload: dict[str, Any]) -> PlanAttempt:
    return PlanAttempt(
        attempt_id=str(payload["attempt_id"]),
        run_id=str(payload["run_id"]),
        attempt_index=int(payload["attempt_index"]),
        trigger=str(payload.get("trigger", "goal_loop")),
        understanding_id=str(payload["understanding_id"]) if payload.get("understanding_id") is not None else None,
        candidate_set_id=str(payload["candidate_set_id"]) if payload.get("candidate_set_id") is not None else None,
        route_decision_id=str(payload["route_decision_id"]) if payload.get("route_decision_id") is not None else None,
        replan_decision_id=str(payload["replan_decision_id"]) if payload.get("replan_decision_id") is not None else None,
        semantic_summary_id=str(payload["semantic_summary_id"]) if payload.get("semantic_summary_id") is not None else None,
        semantic_acceptance_decision_id=str(payload["semantic_acceptance_decision_id"]) if payload.get("semantic_acceptance_decision_id") is not None else None,
        step_safety_review_ids=tuple(str(item) for item in payload.get("step_safety_review_ids", ())),
        selected_route_id=str(payload.get("selected_route_id", "")),
        task_plan=task_plan_from_payload(payload["task_plan"]) if isinstance(payload.get("task_plan"), dict) else None,
        execution_result=plan_execution_result_from_payload(payload["execution_result"]) if isinstance(payload.get("execution_result"), dict) else None,
        observation_receipt=dict(payload.get("observation_receipt", {})) if isinstance(payload.get("observation_receipt"), dict) else None,
        acceptance_result=dict(payload.get("acceptance_result", {})) if isinstance(payload.get("acceptance_result"), dict) else None,
        failure=FailureClassification(**payload["failure"]) if isinstance(payload.get("failure"), dict) else None,
        replan_decision=ReplanDecision(**payload["replan_decision"]) if isinstance(payload.get("replan_decision"), dict) else None,
    )


def plan_execution_result_from_payload(payload: dict[str, Any]) -> PlanExecutionResult:
    return PlanExecutionResult(
        plan_id=str(payload["plan_id"]),
        status=str(payload.get("status", "failed")),
        step_results=tuple(step_result_from_payload(item) for item in payload.get("step_results", ())),
        verification_results=tuple(dict(item) for item in payload.get("verification_results", ())),
        verification_status=str(payload["verification_status"]) if payload.get("verification_status") is not None else None,
        execution_status=str(payload.get("execution_status", "not_started")),
        acceptance_status=str(payload.get("acceptance_status", "skipped")),
        overall_status=str(payload.get("overall_status", "failed")),
        acceptance_result=dict(payload.get("acceptance_result", {})) if isinstance(payload.get("acceptance_result"), dict) else None,
        error=str(payload["error"]) if payload.get("error") is not None else None,
    )


def _attempt_record(
    *,
    run_id: str,
    attempt_index: int,
    trigger: str,
    plan: TaskPlan,
    step_result: StepExecutionResult,
    execution_result: PlanExecutionResult,
    understanding_id: str | None,
    candidate_set_id: str | None,
    route_decision_id: str | None,
    attempt_id: str,
    failure: FailureClassification | None = None,
) -> PlanAttempt:
    step_review_ids = (step_result.step_safety_review_id,) if step_result.step_safety_review_id is not None else ()
    return PlanAttempt(
        attempt_id=attempt_id,
        run_id=run_id,
        attempt_index=attempt_index,
        trigger=trigger,
        understanding_id=understanding_id,
        candidate_set_id=candidate_set_id,
        route_decision_id=route_decision_id,
        step_safety_review_ids=step_review_ids,
        selected_route_id=plan.selected_route_id,
        task_plan=plan,
        execution_result=execution_result,
        acceptance_result=execution_result.acceptance_result,
        failure=failure,
    )


def _execution_attempt_record(
    *,
    run_id: str,
    attempt_index: int,
    trigger: str,
    plan: TaskPlan,
    execution_result: PlanExecutionResult,
    understanding_id: str | None,
    candidate_set_id: str | None,
    route_decision_id: str | None,
    attempt_id: str,
    failure: FailureClassification | None = None,
) -> PlanAttempt:
    acceptance_result = execution_result.acceptance_result if isinstance(execution_result.acceptance_result, dict) else {}
    return PlanAttempt(
        attempt_id=attempt_id,
        run_id=run_id,
        attempt_index=attempt_index,
        trigger=trigger,
        understanding_id=understanding_id,
        candidate_set_id=candidate_set_id,
        route_decision_id=route_decision_id,
        semantic_summary_id=str(acceptance_result.get("semantic_summary_id")) if acceptance_result.get("semantic_summary_id") is not None else None,
        semantic_acceptance_decision_id=(
            str(acceptance_result.get("semantic_acceptance_decision_id")) if acceptance_result.get("semantic_acceptance_decision_id") is not None else None
        ),
        selected_route_id=plan.selected_route_id,
        task_plan=plan,
        execution_result=execution_result,
        acceptance_result=execution_result.acceptance_result,
        failure=failure,
    )


def _restored_step_results(
    *,
    plan: TaskPlan | None,
    completed_step_ids: tuple[str, ...],
    attempts: tuple[PlanAttempt, ...],
) -> tuple[StepExecutionResult, ...]:
    if plan is None or not completed_step_ids:
        return ()
    completed = set(completed_step_ids)
    results: list[StepExecutionResult] = []
    seen: set[str] = set()
    for attempt in attempts:
        execution = attempt.execution_result
        if execution is None:
            continue
        for step_result in execution.step_results:
            if step_result.status != "succeeded" or step_result.step_id not in completed or step_result.step_id in seen:
                continue
            if not _attempt_step_compatible_with_plan(plan, attempt, step_result.step_id):
                continue
            results.append(step_result)
            seen.add(step_result.step_id)
    return tuple(results)


def _attempt_supports_step(plan: TaskPlan, step_id: str, attempts: tuple[PlanAttempt, ...]) -> bool:
    return any(_attempt_step_compatible_with_plan(plan, attempt, step_id) for attempt in attempts)


def _attempt_step_compatible_with_plan(plan: TaskPlan, attempt: PlanAttempt, step_id: str) -> bool:
    current_step = next((item for item in plan.steps if item.id == step_id), None)
    previous_plan = attempt.task_plan
    if current_step is None or previous_plan is None:
        return False
    previous_step = next((item for item in previous_plan.steps if item.id == step_id), None)
    if previous_step is None:
        return False
    return _steps_are_compatible(current_step, previous_step)


def _steps_are_compatible(current_step: TaskStep, previous_step: TaskStep) -> bool:
    return bool(
        current_step.action == previous_step.action
        and current_step.capability_id == previous_step.capability_id
        and canonicalize_target_for_action(current_step.action, current_step.target)
        == canonicalize_target_for_action(previous_step.action, previous_step.target)
    )


def step_result_from_payload(payload: dict[str, Any]) -> StepExecutionResult:
    return StepExecutionResult(
        step_id=str(payload["step_id"]),
        layer=str(payload["layer"]),
        status=str(payload["status"]),
        step_safety_review_id=str(payload["step_safety_review_id"]) if payload.get("step_safety_review_id") is not None else None,
        adapter=str(payload["adapter"]) if payload.get("adapter") is not None else None,
        capability_id=str(payload["capability_id"]) if payload.get("capability_id") is not None else None,
        attempt=int(payload.get("attempt", 1)),
        attempt_id=str(payload["attempt_id"]) if payload.get("attempt_id") is not None else None,
        duration_ms=int(payload["duration_ms"]) if payload.get("duration_ms") is not None else None,
        adapter_status=str(payload["adapter_status"]) if payload.get("adapter_status") is not None else None,
        diagnostics=dict(payload.get("diagnostics", {})),
        error_code=str(payload["error_code"]) if payload.get("error_code") is not None else None,
        result=dict(payload.get("result", {})),
        error=str(payload["error"]) if payload.get("error") is not None else None,
        audit_id=str(payload["audit_id"]) if payload.get("audit_id") is not None else None,
    )


def _reset_for_same_plan_recovery(state: LoopState, policy: LoopPolicy, *, repair: bool) -> LoopState:
    observation_level = next_observation_level(state, policy, escalate=True) if repair else state.observation_level
    return replace(
        state,
        current_step_id=None,
        completed_step_ids=(),
        pre_observation_id=None,
        post_observation_id=None,
        stage="init_loop",
        observation_level=observation_level,
    )


def _restore_replanned_state(
    *,
    state: LoopState,
    planning: PlanningArtifacts,
    attempts: tuple[PlanAttempt, ...],
) -> tuple[LoopState, list[StepExecutionResult]]:
    plan = planning.plan
    normalized_state = sync_loop_state_with_planning(normalize_state_for_plan(state, plan), planning)
    restored_results = list(
        _restored_step_results(
            plan=plan,
            completed_step_ids=normalized_state.completed_step_ids,
            attempts=attempts,
        )
    )
    return normalized_state, restored_results


def _record_recovery_decision(
    *,
    goal_id: str,
    plan_id: str,
    attempt_id: str,
    step_id: str | None,
    decision: ReplanDecision,
) -> None:
    record_trace_event(
        phase="goal_loop",
        event_type="repair_decided" if decision.action == "repair" else "replan_decided",
        status=decision.action,
        actor="goal_loop",
        goal_id=goal_id,
        plan_id=plan_id,
        attempt_id=attempt_id,
        step_id=step_id,
        data=asdict(decision),
    )


def _record_loop_resumed(*, goal_id: str, state: LoopState, review_id: str | None, resume_kind: str) -> None:
    record_trace_event(
        phase="goal_loop",
        event_type="loop_resumed",
        status=resume_kind,
        actor="goal_loop",
        goal_id=goal_id,
        review_id=review_id,
        data={
            "resume_kind": resume_kind,
            "loop_snapshot_id": state.loop_snapshot_id,
            "current_step_id": state.current_step_id,
            "completed_step_ids": list(state.completed_step_ids),
            "pending_review_id": state.pending_review_id,
            "pending_step_safety_review_id": state.pending_step_safety_review_id,
            "pending_user_input_id": state.pending_user_input_id,
        },
    )


def _record_loop_completed(
    *,
    goal_id: str,
    state: LoopState,
    overall_status: str,
    message: str,
    plan_id: str | None = None,
    step_id: str | None = None,
) -> None:
    record_trace_event(
        phase="goal_loop",
        event_type="loop_completed",
        status=overall_status,
        actor="goal_loop",
        goal_id=goal_id,
        plan_id=plan_id,
        step_id=step_id,
        data={
            "loop_snapshot_id": state.loop_snapshot_id,
            "stage": state.stage,
            "message": message,
            "completed_step_ids": list(state.completed_step_ids),
            "attempt_count": state.attempt_count,
            "step_count": state.step_count,
        },
    )
