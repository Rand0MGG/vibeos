from __future__ import annotations

import json
from dataclasses import asdict
from hashlib import sha256

from .legacy_review_migration import legacy_plan_review_binding
from .loop_models import LoopObservation, LoopPolicy, LoopState
from .loop_policy import contextualize_step_review
from .loop_snapshot import encode_loop_snapshot
from .models import Intent, PermissionReview, ReviewRequest
from .permissions import PermissionPolicy
from .planner import PlanningArtifacts
from .planning_service import PlanningService
from .reviews import ReviewStore
from .task_models import StepReviewRecord, TaskPlan, TaskPlanReviewResult, TaskStep, canonicalize_target_for_action
from .task_trace import record_trace_event
from .task_validation import validate_plan


class ReviewService:
    """Owns task safety decisions and durable GoalLoop review suspension."""

    def __init__(
        self,
        *,
        policy: PermissionPolicy,
        reviews: ReviewStore,
        planning: PlanningService,
        loop_policy: LoopPolicy,
    ) -> None:
        self.policy = policy
        self.reviews = reviews
        self.planning = planning
        self.loop_policy = loop_policy

    def review_task_plan(self, plan: TaskPlan, stored_payload: dict[str, object] | None = None) -> TaskPlanReviewResult:
        _ensure_task_plan(plan)
        validation = validate_plan(plan)
        if not validation.ok:
            result = TaskPlanReviewResult(
                plan_id=plan.plan_id,
                status="rejected",
                max_risk_level="L3",
                message="task plan failed validation before permission review",
            )
            self._record_plan_decision(result)
            return result

        step_reviews: list[StepReviewRecord] = []
        review_required = False
        allowed = True
        max_risk = "L0"
        rejection_reason = ""
        for step in plan.steps:
            review, record = self.review_step(plan, step, None)
            step_reviews.append(record)
            max_risk = max_risk_level(max_risk, review.risk_level)
            review_required = review_required or review.review_required
            if not review.allowed and allowed:
                allowed = False
                rejection_reason = review.reason

        if not allowed:
            result = TaskPlanReviewResult(
                plan_id=plan.plan_id,
                status="rejected",
                max_risk_level=max_risk,
                step_reviews=tuple(step_reviews),
                message=rejection_reason or "task plan contains a rejected step",
            )
            self._record_plan_decision(result)
            return result

        if review_required:
            review_payload = dict(stored_payload if stored_payload is not None else asdict(plan))
            review_payload["legacy_review_binding"] = legacy_plan_review_binding(plan, tuple(step_reviews))
            request = self.reviews.create_plan_review(
                plan.utterance,
                review_payload,
                TaskPlanReviewResult(
                    plan_id=plan.plan_id,
                    status="review_required",
                    max_risk_level=max_risk,
                    step_reviews=tuple(step_reviews),
                ),
            )
            result = TaskPlanReviewResult(
                plan_id=plan.plan_id,
                status="review_required",
                max_risk_level=max_risk,
                review_id=request.review_id,
                step_reviews=tuple(step_reviews),
                message=f"explicit approval is required; run `vibe approve {request.review_id}` after reviewing the request",
            )
            self._record_plan_decision(result)
            return result

        result = TaskPlanReviewResult(
            plan_id=plan.plan_id,
            status="allowed",
            max_risk_level=max_risk,
            step_reviews=tuple(step_reviews),
            message="task plan is allowed without additional review",
        )
        self._record_plan_decision(result)
        return result

    def review_step(
        self,
        plan: TaskPlan,
        step: TaskStep,
        observation: LoopObservation | None,
    ) -> tuple[PermissionReview, StepReviewRecord]:
        _ensure_task_plan(plan)
        review = self.policy.review(intent_from_task_step(step))
        review = contextualize_step_review(
            policy=self.loop_policy,
            step_action=step.action,
            review=review,
            pre_observation=observation,
        )
        record = StepReviewRecord(
            step_safety_review_id=_make_step_safety_review_id(
                plan_id=plan.plan_id,
                step_id=step.id,
                action=step.action,
                risk_level=review.risk_level,
                review_required=review.review_required,
                allowed=review.allowed,
                reason=review.reason,
                observation_fingerprint=_observation_fingerprint(observation),
            ),
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
            data={
                "artifact_type": "step_safety_review",
                "artifact_id": record.step_safety_review_id,
                "step_id": step.id,
                "action": step.action,
                "risk_level": review.risk_level,
                "review_required": review.review_required,
                "allowed": review.allowed,
                "reason": review.reason,
            },
        )
        return review, record

    def persist_step_review(
        self,
        utterance: str,
        planning: PlanningArtifacts,
        state: LoopState,
        step: TaskStep,
        reason: str,
    ) -> ReviewRequest:
        payload = self.planning.payload(planning)
        snapshot = encode_loop_snapshot(state)
        payload["loop_snapshot"] = snapshot
        return self.reviews.create_loop_review(
            utterance,
            plan_payload=payload,
            snapshot_payload=snapshot,
            pending_reason=reason,
            step_id=step.id,
            review_kind="loop",
        )

    def persist_user_input(
        self,
        utterance: str,
        planning: PlanningArtifacts,
        state: LoopState,
        reason: str,
    ) -> ReviewRequest:
        payload = self.planning.payload(planning)
        snapshot = encode_loop_snapshot(state)
        payload["loop_snapshot"] = snapshot
        return self.reviews.create_loop_review(
            utterance,
            plan_payload=payload,
            snapshot_payload=snapshot,
            pending_reason=reason,
            step_id=None,
            review_kind="user_input",
        )

    @staticmethod
    def _record_plan_decision(result: TaskPlanReviewResult) -> None:
        record_trace_event(
            phase="review",
            event_type="review_decided",
            status=result.status,
            actor="review_service",
            plan_id=result.plan_id,
            review_id=result.review_id,
            data=asdict(result),
        )


def intent_from_task_step(step: TaskStep) -> Intent:
    return Intent(
        action=step.action,
        target=canonicalize_target_for_action(step.action, dict(step.target)),
        reason=f"task step {step.id}",
        requires_confirmation=False,
    )


def _ensure_task_plan(plan: TaskPlan) -> None:
    if not isinstance(plan, TaskPlan):
        raise TypeError("executors only accept validated TaskPlan objects, never raw utterances or arbitrary payloads")


def _make_step_safety_review_id(
    *,
    plan_id: str,
    step_id: str,
    action: str,
    risk_level: str,
    review_required: bool,
    allowed: bool,
    reason: str,
    observation_fingerprint: str | None,
) -> str:
    digest = sha256(
        f"{plan_id}:{step_id}:{action}:{risk_level}:{review_required}:{allowed}:{reason}:{observation_fingerprint or ''}".encode("utf-8")
    ).hexdigest()[:12]
    return f"srev_{digest}"


def _observation_fingerprint(observation: LoopObservation | None) -> str | None:
    if observation is None:
        return None
    volatile = {"attempt_id", "captured_at", "freshness_ts", "run_id"}

    def normalize(value: object) -> object:
        if isinstance(value, dict):
            return {str(key): normalize(item) for key, item in sorted(value.items(), key=lambda pair: str(pair[0])) if str(key) not in volatile}
        if isinstance(value, (list, tuple)):
            return [normalize(item) for item in value]
        return value

    payload = {
        "route_id": observation.route_id,
        "step_id": observation.step_id,
        "packages": normalize(observation.packages),
    }
    return sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")).hexdigest()[:16]


def max_risk_level(left: str, right: str) -> str:
    order = {"L0": 0, "L1": 1, "L2": 2, "L3": 3}
    return left if order.get(left, 99) >= order.get(right, 99) else right
