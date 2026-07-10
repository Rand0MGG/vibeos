from __future__ import annotations

from dataclasses import replace

from .loop_models import LoopObservation, LoopPolicy, LoopState, ObservationLevel
from .models import PermissionReview
from .task_models import FailureClassification, ReplanDecision


def default_loop_policy() -> LoopPolicy:
    return LoopPolicy()


def loop_budget_exhausted(state: LoopState, policy: LoopPolicy) -> bool:
    return state.step_count >= policy.max_steps or state.attempt_count >= policy.max_attempts


def next_observation_level(state: LoopState, policy: LoopPolicy, *, escalate: bool) -> ObservationLevel:
    if not policy.observation_escalation_enabled or not escalate:
        return state.observation_level
    if state.observation_level == "L0":
        return "L1"
    if state.observation_level == "L1":
        return "L2"
    return "L2"


def contextualize_step_review(
    *,
    policy: LoopPolicy,
    step_action: str,
    review: PermissionReview,
    pre_observation: LoopObservation | None,
) -> PermissionReview:
    if not policy.review_escalation_enabled or pre_observation is None:
        return review
    if step_action in {"browser.search_web", "app.search_history"} and observation_requires_search_review(pre_observation):
        return PermissionReview(
            risk_level="L2" if review.risk_level in {"L0", "L1"} else review.risk_level,
            review_required=True,
            allowed=review.allowed,
            reason="current observed surface may expose sensitive content; explicit review is required before searching",
            effects=review.effects,
            reversible=review.reversible,
        )
    return review


def observation_requires_search_review(pre_observation: LoopObservation) -> bool:
    for payload in pre_observation.packages.values():
        if not isinstance(payload, dict):
            continue
        if bool(payload.get("contains_sensitive_content")):
            return True
        if bool(payload.get("search_mode_exposes_sensitive_content")):
            return True
        tags = payload.get("sensitivity_tags")
        if isinstance(tags, (list, tuple)):
            normalized = {str(item).strip().lower() for item in tags if str(item).strip()}
            if normalized.intersection({"sensitive", "private", "financial", "health", "messages", "email"}):
                return True
    return False


def consecutive_failure_count(state: LoopState, *, failure_class: str) -> int:
    count = 0
    for item in reversed(state.failure_history):
        if item != failure_class:
            break
        count += 1
    return count


def enforce_replan_policy(
    *,
    policy: LoopPolicy,
    state: LoopState,
    failure: FailureClassification,
    decision: ReplanDecision,
) -> ReplanDecision:
    if decision.action not in {"retry_same_attempt", "repair"}:
        return decision
    failure_streak = consecutive_failure_count(state, failure_class=failure.failure_class)
    if failure.failure_class == "same_action_no_progress" and failure_streak >= policy.same_action_no_progress_limit:
        return replace(
            decision,
            action="stop",
            reason=(
                f"loop policy stopped repeated no-progress after {failure_streak} consecutive attempts"
            ),
        )
    if failure_streak >= policy.max_same_failure_count:
        return replace(
            decision,
            action="stop",
            reason=(
                f"loop policy stopped repeated failure '{failure.failure_class}' after {failure_streak} consecutive attempts"
            ),
        )
    return decision
