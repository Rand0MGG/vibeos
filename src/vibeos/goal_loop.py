from __future__ import annotations

from dataclasses import asdict, replace
from hashlib import sha256
from typing import Any, Callable

from .loop_models import GoalLoopResult, LoopPolicy, LoopState
from .loop_policy import default_loop_policy, loop_budget_exhausted, next_observation_level
from .models import CommandRequest, PermissionReview, ReviewRequest, utc_now_iso
from .observation_service import ObservationService, observation_progressed
from .task_models import FailureClassification, PlanExecutionResult, ReplanDecision, StepExecutionResult, TaskPlan, TaskStep
from .task_trace import record_trace_event


class GoalLoop:
    def __init__(
        self,
        *,
        observation_service: ObservationService,
        planning_payload: Callable[[Any], dict[str, Any]],
        resolve_understanding_transition: Callable[[Any, str], Any],
        apply_replan_transition: Callable[[Any, ReplanDecision, FailureClassification], Any],
        plan_again: Callable[[Any, CommandRequest, tuple[str, ...], tuple[str, ...], tuple[str, ...]], Any],
        review_step: Callable[[TaskPlan, TaskStep], tuple[PermissionReview, Any]],
        execute_step: Callable[[TaskPlan, TaskStep, CommandRequest, str], StepExecutionResult],
        assess_plan_execution: Callable[[TaskPlan, tuple[StepExecutionResult, ...], CommandRequest, str, str | None, str | None, str | None], PlanExecutionResult],
        classify_failure: Callable[[TaskPlan, PlanExecutionResult], FailureClassification],
        decide_replan: Callable[[str, TaskPlan, tuple[Any, ...], FailureClassification, str | None, str | None, tuple[str, ...]], ReplanDecision],
        persist_review: Callable[[str, Any, LoopState, TaskStep, str], ReviewRequest],
        persist_user_input: Callable[[str, Any, LoopState, str], ReviewRequest],
        policy: LoopPolicy | None = None,
    ) -> None:
        self.observation_service = observation_service
        self.planning_payload = planning_payload
        self.resolve_understanding_transition = resolve_understanding_transition
        self.apply_replan_transition = apply_replan_transition
        self.plan_again = plan_again
        self.review_step = review_step
        self.execute_step = execute_step
        self.assess_plan_execution = assess_plan_execution
        self.classify_failure = classify_failure
        self.decide_replan = decide_replan
        self.persist_review = persist_review
        self.persist_user_input = persist_user_input
        self.policy = policy or default_loop_policy()

    def run(
        self,
        *,
        request: CommandRequest,
        planning: Any,
        run_id: str,
        goal_id: str,
        state: LoopState | None = None,
        step_results: tuple[StepExecutionResult, ...] = (),
        attempts: tuple[Any, ...] = (),
    ) -> GoalLoopResult:
        state = state or self._initial_state(planning=planning, run_id=run_id, goal_id=goal_id)
        attempts_list: list[Any] = list(attempts)
        step_results_list: list[StepExecutionResult] = list(step_results)
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
            planning = self.resolve_understanding_transition(planning, trigger)
            payload = self.planning_payload(planning)
            plan = getattr(planning, "plan", None)
            analysis = getattr(planning, "analysis", None)
            route_decision = getattr(planning, "route_decision", None)
            if plan is not None and not attempts_list and state.step_result_payloads:
                attempts_list = _seed_attempt_records(state, plan)

            if plan is None:
                route_action = getattr(route_decision, "action", None)
                if getattr(analysis, "type", "") == "clarification" or route_action == "clarify":
                    review_request = self.persist_user_input(
                        request.utterance,
                        planning,
                        replace(state, stage="needs_user_input"),
                        getattr(analysis, "chat_response", None) or getattr(analysis, "explanation", "") or "clarification required",
                    )
                    state = replace(state, pending_user_input_id=review_request.review_id, stage="needs_user_input")
                    record_trace_event(
                        phase="goal_loop",
                        event_type="loop_suspended",
                        status="needs_user_input",
                        actor="goal_loop",
                        goal_id=goal_id,
                        review_id=review_request.review_id,
                        data=asdict(state),
                    )
                    return GoalLoopResult(
                        decision="needs_user_input",
                        state=state,
                        message=review_request.pending_reason or "clarification required",
                        review_id=review_request.review_id,
                        execution_status="not_started",
                        acceptance_status="skipped",
                        overall_status="needs_user_input",
                        payload=payload,
                        attempt_records=tuple(attempts_list),
                    )
                overall_status = "blocked" if route_action == "blocked" else "failed"
                return GoalLoopResult(
                    decision="blocked" if overall_status == "blocked" else "complete",
                    state=replace(state, stage="blocked" if overall_status == "blocked" else "complete"),
                    message=getattr(route_decision, "reason", "") or getattr(analysis, "explanation", "") or "planner did not produce a task plan",
                    execution_status="not_started",
                    acceptance_status="skipped",
                    overall_status=overall_status,
                    payload=payload,
                    attempt_records=tuple(attempts_list),
                )

            if loop_budget_exhausted(state, self.policy):
                state = replace(state, stage="budget_exhausted")
                return GoalLoopResult(
                    decision="budget_exhausted",
                    state=state,
                    message="goal loop budget exhausted",
                    execution_status="failed",
                    acceptance_status="skipped",
                    overall_status="blocked",
                    payload=payload,
                    attempt_records=tuple(attempts_list),
                )

            next_step = _next_step(plan, state.completed_step_ids)
            if next_step is None:
                execution = self.assess_plan_execution(
                    plan,
                    tuple(step_results_list),
                    request,
                    run_id,
                    state.primary_understanding_id,
                    state.candidate_set_id,
                    state.selected_route_decision_id,
                )
                payload["execution"] = asdict(execution)
                failure = self.classify_failure(plan, execution)
                overall_status = execution.overall_status if failure.failure_class in {"none", "acceptance_unverified", "acceptance_failed"} else "failed"
                return GoalLoopResult(
                    decision="complete" if failure.failure_class == "none" else "blocked",
                    state=replace(state, stage="complete" if failure.failure_class == "none" else "blocked"),
                    message=execution.error or (execution.acceptance_result or {}).get("message", "") if isinstance(execution.acceptance_result, dict) else "",
                    selected_target=_selected_target(step_results_list),
                    execution_status=execution.execution_status,
                    acceptance_status=execution.acceptance_status,
                    overall_status=overall_status,
                    payload=payload,
                    attempt_records=tuple(attempts_list),
                )

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
            pre_observation = self.observation_service.observe(plan=plan, step=next_step, phase="pre", level=state.observation_level)
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

            state = replace(state, stage="step_review")
            review, step_review = self.review_step(plan, next_step)
            record_trace_event(
                phase="goal_loop",
                event_type="step_review_completed",
                status="allowed" if review.allowed and not review.review_required else ("review_required" if review.review_required else "rejected"),
                actor="goal_loop",
                goal_id=goal_id,
                step_id=next_step.id,
                data=asdict(step_review),
            )
            if not review.allowed:
                return GoalLoopResult(
                    decision="blocked",
                    state=replace(state, stage="blocked"),
                    message=review.reason,
                    execution_status="not_started",
                    acceptance_status="skipped",
                    overall_status="failed",
                    payload=payload,
                    attempt_records=tuple(attempts_list),
                )
            if review.review_required and not request.approve:
                review_request = self.persist_review(request.utterance, planning, replace(state, stage="needs_review"), next_step, review.reason)
                state = replace(state, pending_review_id=review_request.review_id, stage="needs_review")
                record_trace_event(
                    phase="goal_loop",
                    event_type="loop_suspended",
                    status="needs_review",
                    actor="goal_loop",
                    goal_id=goal_id,
                    review_id=review_request.review_id,
                    step_id=next_step.id,
                    data=asdict(state),
                )
                return GoalLoopResult(
                    decision="needs_review",
                    state=state,
                    message=review.reason,
                    review_id=review_request.review_id,
                    execution_status="not_started",
                    acceptance_status="skipped",
                    overall_status="needs_review",
                    payload=payload,
                    attempt_records=tuple(attempts_list),
                )

            state = replace(state, stage="act")
            attempt_id = _make_attempt_id(run_id, len(step_results_list) + 1, next_step.id)
            step_result = self.execute_step(plan, next_step, request, attempt_id)
            step_results_list.append(step_result)
            attempts_list.append(
                {
                    "attempt_id": attempt_id,
                    "trigger": trigger,
                    "selected_route_id": plan.selected_route_id,
                    "understanding_id": state.primary_understanding_id,
                    "candidate_set_id": state.candidate_set_id,
                    "route_decision_id": state.selected_route_decision_id,
                    "task_plan": asdict(plan),
                    "step_result": asdict(step_result),
                }
            )
            state = replace(
                state,
                attempt_history=(*state.attempt_history, attempt_id),
                attempt_count=state.attempt_count + 1,
                step_count=state.step_count + 1,
                step_result_payloads=(*state.step_result_payloads, asdict(step_result)),
            )
            record_trace_event(
                phase="goal_loop",
                event_type="step_executed",
                status=step_result.status,
                actor="goal_loop",
                goal_id=goal_id,
                step_id=next_step.id,
                attempt_id=attempt_id,
                data=asdict(step_result),
            )

            state = replace(state, stage="observe_post")
            post_observation = self.observation_service.observe(
                plan=plan,
                step=next_step,
                phase="post",
                level=next_observation_level(state, self.policy, escalate=step_result.status != "succeeded"),
            )
            state = replace(state, post_observation_id=post_observation.observation_id)
            record_trace_event(
                phase="goal_loop",
                event_type="observe_post_completed",
                status=post_observation.level,
                actor="goal_loop",
                goal_id=goal_id,
                step_id=next_step.id,
                data=asdict(post_observation),
            )

            state = replace(state, stage="verify")
            progress_made = step_result.status == "succeeded" and observation_progressed(pre_observation, post_observation)
            if step_result.status == "succeeded" and progress_made:
                completed_step_ids = (*state.completed_step_ids, next_step.id)
                state = replace(state, completed_step_ids=completed_step_ids)
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
            failure = self.classify_failure(plan, partial_execution)
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
            decision = self.decide_replan(
                request.utterance,
                plan,
                tuple(attempts_list),
                failure,
                state.primary_understanding_id,
                state.candidate_set_id,
                tuple(dict.fromkeys(route.domain_id for route in plan.routes if route.domain_id)),
            )
            attempts_list[-1] = {
                **attempts_list[-1],
                "failure": asdict(failure),
                "replan_decision": asdict(decision),
            }
            if decision.action == "ask_user":
                review_request = self.persist_user_input(request.utterance, planning, replace(state, stage="needs_user_input"), decision.reason or failure.message)
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
                return GoalLoopResult(
                    decision="blocked",
                    state=replace(state, stage="blocked"),
                    message=decision.reason or failure.message,
                    selected_target=_selected_target(step_results_list),
                    execution_status="failed" if step_result.status != "succeeded" else "succeeded",
                    acceptance_status="failed" if failure.failure_class == "same_action_no_progress" else "skipped",
                    overall_status=overall_status,
                    payload=payload,
                    attempt_records=tuple(attempts_list),
                )
            planning = self.apply_replan_transition(planning, decision, failure)
            excluded_route_ids = tuple(dict.fromkeys((*excluded_route_ids, *decision.do_not_repeat_route_ids)))
            excluded_capability_ids = tuple(dict.fromkeys((*excluded_capability_ids, *decision.do_not_repeat_capability_ids)))
            candidate_domain_ids = tuple(dict.fromkeys((*candidate_domain_ids, *decision.candidate_domain_ids)))
            planning = self.plan_again(planning, request, excluded_route_ids, excluded_capability_ids, candidate_domain_ids)
            trigger = decision.action

    def resume_from_review(self, *, request: CommandRequest, planning: Any, state: LoopState, run_id: str, goal_id: str) -> GoalLoopResult:
        resumed_request = replace(request, approve=True)
        return self.run(
            request=resumed_request,
            planning=planning,
            run_id=run_id,
            goal_id=goal_id,
            state=replace(state, pending_review_id=None, stage="step_review"),
            step_results=tuple(step_result_from_payload(item) for item in state.step_result_payloads),
            attempts=(),
        )

    def _initial_state(self, *, planning: Any, run_id: str, goal_id: str) -> LoopState:
        understanding = getattr(planning, "understanding", None)
        candidate_set = getattr(planning, "candidate_set", None)
        route_decision = getattr(planning, "route_decision", None)
        model_artifact_ids: dict[str, str] = {}
        if understanding is not None and getattr(understanding, "understanding_id", None) is not None:
            model_artifact_ids["understanding_id"] = str(understanding.understanding_id)
        if route_decision is not None and getattr(route_decision, "route_decision_id", None) is not None:
            model_artifact_ids["route_decision_id"] = str(route_decision.route_decision_id)
        if candidate_set is not None and getattr(candidate_set, "candidate_set_id", None) is not None:
            model_artifact_ids["candidate_set_id"] = str(candidate_set.candidate_set_id)
        return LoopState(
            loop_snapshot_id=_make_snapshot_id(run_id, goal_id),
            trace_run_id=run_id,
            goal_id=goal_id,
            primary_understanding_id=_string_attr(understanding, "primary_understanding_id") or _string_attr(understanding, "understanding_id"),
            candidate_set_id=_string_attr(candidate_set, "candidate_set_id"),
            selected_route_decision_id=_string_attr(route_decision, "route_decision_id"),
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


def _partial_execution(plan: TaskPlan, step_result: StepExecutionResult, progress_made: bool) -> PlanExecutionResult:
    acceptance_result: dict[str, Any] | None = None
    acceptance_status = "skipped"
    execution_status = "failed" if step_result.status != "succeeded" else "succeeded"
    overall_status = "failed" if step_result.status != "succeeded" else "incomplete"
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
        execution_status=execution_status,  # type: ignore[arg-type]
        acceptance_status=acceptance_status,  # type: ignore[arg-type]
        overall_status=overall_status,  # type: ignore[arg-type]
        acceptance_result=acceptance_result,
        error=step_result.error,
    )


def _selected_target(step_results: list[StepExecutionResult]) -> str | None:
    for step in reversed(step_results):
        target = step.result.get("selected_target") or step.result.get("uri")
        if target is not None:
            return str(target)
    return None


def _string_attr(obj: Any, name: str) -> str | None:
    if obj is None:
        return None
    value = getattr(obj, name, None)
    if value is None:
        return None
    return str(value)


def _make_snapshot_id(run_id: str, goal_id: str) -> str:
    digest = sha256(f"{run_id}:{goal_id}:{utc_now_iso()}".encode("utf-8")).hexdigest()[:12]
    return f"lsnap_{digest}"


def _make_attempt_id(run_id: str, attempt_index: int, step_id: str) -> str:
    digest = sha256(f"{run_id}:{attempt_index}:{step_id}:{utc_now_iso()}".encode("utf-8")).hexdigest()[:10]
    return f"attempt_{digest}"


def _seed_attempt_records(state: LoopState, plan: TaskPlan) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for index, step_payload in enumerate(state.step_result_payloads):
        attempt_id = state.attempt_history[index] if index < len(state.attempt_history) else f"attempt_resumed_{index + 1}"
        records.append(
            {
                "attempt_id": attempt_id,
                "trigger": "resume",
                "selected_route_id": state.selected_route_id or plan.selected_route_id,
                "understanding_id": state.primary_understanding_id,
                "candidate_set_id": state.candidate_set_id,
                "route_decision_id": state.selected_route_decision_id,
                "task_plan": asdict(plan),
                "step_result": dict(step_payload),
            }
        )
    return records


def step_result_from_payload(payload: dict[str, Any]) -> StepExecutionResult:
    return StepExecutionResult(
        step_id=str(payload["step_id"]),
        layer=str(payload["layer"]),
        status=str(payload["status"]),
        step_safety_review_id=str(payload["step_safety_review_id"]) if payload.get("step_safety_review_id") is not None else None,
        adapter=str(payload["adapter"]) if payload.get("adapter") is not None else None,
        capability_id=str(payload["capability_id"]) if payload.get("capability_id") is not None else None,
        attempt=int(payload.get("attempt", 1)),
        duration_ms=int(payload["duration_ms"]) if payload.get("duration_ms") is not None else None,
        adapter_status=str(payload["adapter_status"]) if payload.get("adapter_status") is not None else None,
        diagnostics=dict(payload.get("diagnostics", {})),
        error_code=str(payload["error_code"]) if payload.get("error_code") is not None else None,
        result=dict(payload.get("result", {})),
        error=str(payload["error"]) if payload.get("error") is not None else None,
        audit_id=str(payload["audit_id"]) if payload.get("audit_id") is not None else None,
    )
