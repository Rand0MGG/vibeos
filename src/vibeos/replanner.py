from __future__ import annotations

from .intent import infer_browser_intent_from_open_request
from .task_models import FailureClassification, PlanAttempt, ReplanDecision, TaskPlan


class Replanner:
    def decide(
        self,
        *,
        utterance: str,
        current_plan: TaskPlan,
        attempts: tuple[PlanAttempt, ...],
        failure: FailureClassification,
    ) -> ReplanDecision:
        raise NotImplementedError


class EvidenceDrivenReplanner(Replanner):
    def __init__(self, max_attempts: int = 3) -> None:
        self.max_attempts = max_attempts

    def decide(
        self,
        *,
        utterance: str,
        current_plan: TaskPlan,
        attempts: tuple[PlanAttempt, ...],
        failure: FailureClassification,
    ) -> ReplanDecision:
        if failure.failure_class == "none":
            return ReplanDecision(action="stop", reason="execution completed")

        if len(attempts) >= self.max_attempts:
            return ReplanDecision(action="stop", reason="attempt budget exhausted")

        if failure.failure_class in {"transport_timeout", "tool_timeout", "provider_timeout", "provider_transient"}:
            same_route_attempts = [item for item in attempts if item.selected_route_id == current_plan.selected_route_id]
            if len(same_route_attempts) < 2:
                return ReplanDecision(action="retry_same_attempt", reason=failure.message or "transient failure may succeed on retry")
            return ReplanDecision(action="stop", reason="transient retry budget exhausted")

        if failure.failure_class == "semantic_mismatch":
            browser_intent = infer_browser_intent_from_open_request(utterance)
            if browser_intent is not None and (current_plan.routes and current_plan.routes[0].domain_id != "browser"):
                return ReplanDecision(
                    action="replan_with_constraints",
                    reason=failure.message or "semantic mismatch suggests a browser route",
                    do_not_repeat_route_ids=(current_plan.selected_route_id,),
                    do_not_repeat_capability_ids=tuple(step.capability_id for step in current_plan.steps),
                    candidate_domain_ids=("browser",),
                )
            return ReplanDecision(
                action="replan_with_constraints",
                reason=failure.message or "semantic mismatch suggests a different route",
                do_not_repeat_route_ids=(current_plan.selected_route_id,),
                do_not_repeat_capability_ids=tuple(step.capability_id for step in current_plan.steps),
            )

        if failure.failure_class in {"acceptance_unverified", "acceptance_failed"}:
            return ReplanDecision(action="stop", reason=failure.message or "acceptance did not produce a safe replanning signal")

        if failure.failure_class == "permission_blocked":
            return ReplanDecision(action="ask_user", reason=failure.message or "user approval or clarification is required")

        if failure.failure_class == "environment_unreachable":
            return ReplanDecision(action="stop", reason=failure.message or "environment does not expose the required capability")

        return ReplanDecision(action="stop", reason=failure.message or "no safe replanning path was identified")
