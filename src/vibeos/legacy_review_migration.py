from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass

from .capabilities import CAPABILITIES
from .loop_models import LoopState, MigratedStepApprovalBinding
from .models import CommandRequest, ReviewRequest
from .planner import PlanningArtifacts
from .planning_service import PlanningService, PlanningSnapshotError
from .task_models import StepReviewRecord, TaskPlan, task_plan_from_payload
from .task_validation import validate_plan


LEGACY_PLAN_BINDING_VERSION = 1


class LegacyReviewUnverifiable(ValueError):
    """A historical plan review lacks proof that its approval still applies."""


@dataclass(frozen=True)
class MigratedLegacyPlanReview:
    request: CommandRequest
    planning: PlanningArtifacts
    state: LoopState


def legacy_plan_review_binding(plan: TaskPlan, step_reviews: tuple[StepReviewRecord, ...]) -> dict[str, object]:
    """Persist the immutable scope needed to safely migrate a plan review."""

    review_by_step = {item.step_id: item for item in step_reviews}
    steps = []
    for step in plan.steps:
        record = review_by_step.get(step.id)
        steps.append(
            {
                "step_id": step.id,
                "action": step.action,
                "target_hash": _hash_value(step.target),
                "step_safety_review_id": record.step_safety_review_id if record is not None else "",
                "allowed": record.allowed if record is not None else False,
            }
        )
    return {
        "version": LEGACY_PLAN_BINDING_VERSION,
        "review_kind": "plan",
        "plan_id": plan.plan_id,
        "plan_hash": _hash_value(asdict(plan)),
        "pending_step_id": plan.steps[0].id if len(plan.steps) == 1 else None,
        "steps": steps,
    }


class LegacyPlanReviewMigrator:
    """Pure compatibility conversion; it validates data but never executes tools."""

    def __init__(self, *, planning: PlanningService) -> None:
        self._planning = planning

    def migrate(self, review_request: ReviewRequest, *, dry_run: bool, transport: str | None) -> MigratedLegacyPlanReview:
        if review_request.review_kind != "plan":
            raise LegacyReviewUnverifiable("review kind is not a historical plan approval")
        payload = review_request.plan_payload
        if not isinstance(payload, dict):
            raise LegacyReviewUnverifiable("historical review has no stored plan payload")
        binding = payload.get("legacy_review_binding")
        if not isinstance(binding, dict):
            raise LegacyReviewUnverifiable("historical review does not contain an immutable approval binding")
        if binding.get("version") != LEGACY_PLAN_BINDING_VERSION or binding.get("review_kind") != "plan":
            raise LegacyReviewUnverifiable("historical review binding version is unsupported")
        try:
            plan = task_plan_from_payload(payload)
        except (KeyError, TypeError, ValueError) as exc:
            raise LegacyReviewUnverifiable("stored historical plan is malformed") from exc
        validation = validate_plan(plan)
        if not validation.ok:
            raise LegacyReviewUnverifiable("stored historical plan no longer validates")
        stored_review = self._validate_binding(review_request, plan, binding)
        planning_snapshot = {
            "snapshot_version": 1,
            "plan": asdict(plan),
            "candidates": [asdict(plan)],
        }
        try:
            planning = self._planning.from_snapshot(utterance=review_request.utterance, payload=planning_snapshot)
        except PlanningSnapshotError as exc:
            raise LegacyReviewUnverifiable("stored historical planning data is malformed") from exc
        step = plan.steps[0]
        bound_step = binding["steps"][0]
        run_id = f"run_migrated_{_hash_value(review_request.review_id)[:12]}"
        goal_id = f"goal_migrated_{plan.plan_id}"
        state = LoopState(
            loop_snapshot_id=f"lsnap_migrated_{_hash_value(review_request.review_id)[:12]}",
            trace_run_id=run_id,
            goal_id=goal_id,
            primary_understanding_id=planning.understanding.primary_understanding_id,
            candidate_set_id=planning.candidate_set.candidate_set_id if planning.candidate_set is not None else None,
            selected_route_decision_id=planning.route_decision.route_decision_id if planning.route_decision is not None else None,
            current_step_id=step.id,
            pending_review_id=review_request.review_id,
            pending_step_safety_review_id=str(bound_step["step_safety_review_id"]),
            stage="needs_review",
            selected_route_id=plan.selected_route_id,
            selected_plan_id=plan.plan_id,
            migrated_step_approval=MigratedStepApprovalBinding(
                review_id=review_request.review_id,
                step_id=step.id,
                action=step.action,
                original_safety_review_id=str(bound_step["step_safety_review_id"]),
                risk_level=str(stored_review["risk_level"]),
                review_required=bool(stored_review.get("review_required")),
                allowed=bool(stored_review["allowed"]),
                reason=str(stored_review["reason"]),
            ),
        )
        return MigratedLegacyPlanReview(
            request=CommandRequest(
                review_request.utterance,
                dry_run=dry_run,
                approve=True,
                review_id=review_request.review_id,
                transport=transport,
            ),
            planning=planning,
            state=state,
        )

    def _validate_binding(
        self,
        review_request: ReviewRequest,
        plan: TaskPlan,
        binding: dict[str, object],
    ) -> dict[str, object]:
        if review_request.plan_id != plan.plan_id or binding.get("plan_id") != plan.plan_id:
            raise LegacyReviewUnverifiable("stored plan identity does not match the approved review")
        if binding.get("plan_hash") != _hash_value(asdict(plan)):
            raise LegacyReviewUnverifiable("stored plan contents changed after approval")
        if len(plan.steps) != 1 or binding.get("pending_step_id") != plan.steps[0].id:
            raise LegacyReviewUnverifiable("historical approval does not bind one exact pending step")
        steps = binding.get("steps")
        if not isinstance(steps, list) or len(steps) != 1 or not isinstance(steps[0], dict):
            raise LegacyReviewUnverifiable("historical approval has no exact step binding")
        step = plan.steps[0]
        bound_step = steps[0]
        if (
            bound_step.get("step_id") != step.id
            or bound_step.get("action") != step.action
            or bound_step.get("target_hash") != _hash_value(step.target)
            or not isinstance(bound_step.get("step_safety_review_id"), str)
            or not str(bound_step.get("step_safety_review_id"))
            or bound_step.get("allowed") is not True
        ):
            raise LegacyReviewUnverifiable("historical step safety binding is incomplete or inconsistent")
        if step.action not in CAPABILITIES or step.capability_id not in CAPABILITIES:
            raise LegacyReviewUnverifiable("historical plan uses a capability that is no longer registered")
        stored_reviews = {
            str(item["step_id"]): dict(item) for item in review_request.step_reviews if isinstance(item, dict) and isinstance(item.get("step_id"), str)
        }
        stored = stored_reviews.get(step.id)
        if stored is None or (
            stored.get("action") != step.action
            or stored.get("step_safety_review_id") != bound_step["step_safety_review_id"]
            or stored.get("allowed") is not True
            or not isinstance(stored.get("risk_level"), str)
            or not isinstance(stored.get("reason"), str)
        ):
            raise LegacyReviewUnverifiable("stored review record does not prove the approved step safety scope")
        return stored


def _hash_value(value: object) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")).hexdigest()
