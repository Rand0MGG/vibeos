from __future__ import annotations

from dataclasses import asdict
from hashlib import sha256

from .models import Intent, PermissionReview
from .permissions import PermissionPolicy
from .task_models import StepReviewRecord, TaskObservation, TaskPlan, TaskPlanReviewResult, TaskStep, canonicalize_target_for_action
from .task_trace import record_trace_event
from .task_validation import validate_plan


class ReviewService:
    """Makes deterministic safety decisions; task persistence belongs to Task Store."""

    def __init__(self, *, policy: PermissionPolicy, review_escalation_enabled: bool = True) -> None:
        self.policy = policy
        self.review_escalation_enabled = review_escalation_enabled

    def review_task_plan(self, plan: TaskPlan, stored_payload: dict[str, object] | None = None) -> TaskPlanReviewResult:
        del stored_payload
        _ensure_task_plan(plan)
        if not validate_plan(plan).ok:
            return self._record(TaskPlanReviewResult(plan_id=plan.plan_id, status="rejected", max_risk_level="L3", message="task plan failed validation"))
        records: list[StepReviewRecord] = []
        max_risk = "L0"
        requires_review = False
        for step in plan.steps:
            review, record = self.review_step(plan, step, None)
            records.append(record)
            max_risk = max_risk_level(max_risk, review.risk_level)
            if not review.allowed:
                return self._record(
                    TaskPlanReviewResult(
                        plan_id=plan.plan_id,
                        status="rejected",
                        max_risk_level=max_risk,
                        step_reviews=tuple(records),
                        message=review.reason,
                    )
                )
            requires_review = requires_review or review.review_required
        status = "review_required" if requires_review else "allowed"
        review_id = _plan_review_id(plan, tuple(records)) if requires_review else None
        message = "explicit approval is required" if requires_review else "task plan is allowed without additional review"
        return self._record(
            TaskPlanReviewResult(
                plan_id=plan.plan_id,
                status=status,
                max_risk_level=max_risk,
                review_id=review_id,
                step_reviews=tuple(records),
                message=message,
            )
        )

    def review_step(
        self,
        plan: TaskPlan,
        step: TaskStep,
        observation: TaskObservation | None,
    ) -> tuple[PermissionReview, StepReviewRecord]:
        _ensure_task_plan(plan)
        review = self.policy.review(intent_from_task_step(step))
        review = self._contextualize(step.action, review, observation)
        record = StepReviewRecord(
            step_safety_review_id=_step_review_id(plan, step, review, observation),
            step_id=step.id,
            action=step.action,
            risk_level=review.risk_level,
            review_required=review.review_required,
            allowed=review.allowed,
            reason=review.reason,
            effects=review.effects,
            reversible=review.reversible,
        )
        record_trace_event(
            phase="review",
            event_type="step_safety_review_recorded",
            status="allowed" if review.allowed else "rejected",
            actor="review_service",
            plan_id=plan.plan_id,
            step_id=step.id,
            data=asdict(record),
        )
        return review, record

    def _contextualize(self, action: str, review: PermissionReview, observation: TaskObservation | None) -> PermissionReview:
        if not self.review_escalation_enabled or observation is None or action not in {"browser.search_web", "app.search_history"}:
            return review
        if not _observation_requires_review(observation):
            return review
        return PermissionReview(
            risk_level="L2" if review.risk_level in {"L0", "L1"} else review.risk_level,
            review_required=True,
            allowed=review.allowed,
            reason="current observed surface may expose sensitive content; explicit review is required before searching",
            effects=review.effects,
            reversible=review.reversible,
        )

    @staticmethod
    def _record(result: TaskPlanReviewResult) -> TaskPlanReviewResult:
        record_trace_event(
            phase="review",
            event_type="review_decided",
            status=result.status,
            actor="review_service",
            plan_id=result.plan_id,
            review_id=result.review_id,
            data=asdict(result),
        )
        return result


def intent_from_task_step(step: TaskStep) -> Intent:
    return Intent(
        action=step.action,
        target=canonicalize_target_for_action(step.action, dict(step.target)),
        reason=f"task step {step.id}",
        requires_confirmation=False,
    )


def max_risk_level(left: str, right: str) -> str:
    order = {"L0": 0, "L1": 1, "L2": 2, "L3": 3}
    return left if order[left] >= order[right] else right


def _ensure_task_plan(plan: TaskPlan) -> None:
    if not isinstance(plan, TaskPlan):
        raise TypeError("review accepts validated TaskPlan objects only")


def _plan_review_id(plan: TaskPlan, records: tuple[StepReviewRecord, ...]) -> str:
    source = ":".join((plan.plan_id, *(record.step_safety_review_id for record in records)))
    return f"review_{sha256(source.encode('utf-8')).hexdigest()[:20]}"


def _step_review_id(plan: TaskPlan, step: TaskStep, review: PermissionReview, observation: TaskObservation | None) -> str:
    fingerprint = _observation_fingerprint(observation) if step.action in {"browser.search_web", "app.search_history"} else "not-safety-relevant"
    source = f"{plan.plan_id}:{step.id}:{step.action}:{review.risk_level}:{review.review_required}:{review.allowed}:{review.reason}:{fingerprint}"
    return f"srev_{sha256(source.encode('utf-8')).hexdigest()[:20]}"


def _observation_fingerprint(observation: TaskObservation | None) -> str:
    if observation is None:
        return "none"
    safety: list[tuple[str, bool, bool, tuple[str, ...]]] = []
    for package, payload in sorted(observation.packages.items()):
        tags = payload.get("sensitivity_tags")
        normalized_tags = tuple(sorted(str(item).strip().lower() for item in tags)) if isinstance(tags, (list, tuple)) else ()
        safety.append(
            (
                package,
                bool(payload.get("contains_sensitive_content")),
                bool(payload.get("search_mode_exposes_sensitive_content")),
                normalized_tags,
            )
        )
    return sha256(repr(safety).encode("utf-8")).hexdigest()[:12]


def _observation_requires_review(observation: TaskObservation) -> bool:
    sensitive = {"sensitive", "private", "financial", "health", "messages", "email"}
    for payload in observation.packages.values():
        if bool(payload.get("contains_sensitive_content")) or bool(payload.get("search_mode_exposes_sensitive_content")):
            return True
        tags = payload.get("sensitivity_tags")
        if isinstance(tags, (list, tuple)) and sensitive.intersection(str(item).strip().lower() for item in tags):
            return True
    return False
