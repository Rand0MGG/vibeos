from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Callable

from .legacy_review_migration import LegacyPlanReviewMigrator, LegacyReviewUnverifiable
from .goal_loop import GoalLoop, normalize_state_for_plan, sync_loop_state_with_planning
from .loop_models import GoalLoopResult, LoopState
from .loop_snapshot import LoopSnapshotError, decode_loop_snapshot
from .models import CommandRequest, ReviewRequest
from .planner import PlanningArtifacts
from .planning_service import PlanningService, PlanningSnapshotError


class ReviewResumeError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class ResumedGoalLoop:
    request: CommandRequest
    planning: PlanningArtifacts
    run_id: str
    goal_id: str
    loop_result: GoalLoopResult


class ReviewResumeService:
    """Strictly decodes a stored review and resumes it through GoalLoop only."""

    def __init__(self, *, planning: PlanningService, goal_loop_factory: Callable[[], GoalLoop]) -> None:
        self._planning = planning
        self._goal_loop_factory = goal_loop_factory
        self._legacy_plan_migrator = LegacyPlanReviewMigrator(planning=planning)

    def resume_legacy_plan_review(
        self,
        review_request: ReviewRequest,
        *,
        dry_run: bool,
        transport: str | None,
    ) -> ResumedGoalLoop:
        try:
            migrated = self._legacy_plan_migrator.migrate(
                review_request,
                dry_run=dry_run,
                transport=transport,
            )
        except LegacyReviewUnverifiable as exc:
            raise ReviewResumeError("legacy_review_unverifiable", str(exc)) from exc
        loop_result = self._goal_loop_factory().resume_from_review(
            request=migrated.request,
            planning=migrated.planning,
            state=migrated.state,
            run_id=migrated.state.trace_run_id,
            goal_id=migrated.state.goal_id,
        )
        return ResumedGoalLoop(
            migrated.request,
            migrated.planning,
            migrated.state.trace_run_id,
            migrated.state.goal_id,
            loop_result,
        )

    def resume_execution_review(
        self,
        review_request: ReviewRequest,
        *,
        dry_run: bool,
        transport: str | None,
    ) -> ResumedGoalLoop:
        state = self._decode_state(review_request)
        planning = self._decode_planning(review_request)
        state = sync_loop_state_with_planning(state, planning)
        if planning.plan is None:
            raise ReviewResumeError("review_snapshot_invalid", "stored loop plan is missing")
        request = CommandRequest(
            review_request.utterance,
            dry_run=dry_run,
            approve=True,
            review_id=review_request.review_id,
            transport=transport,
        )
        loop_result = self._goal_loop_factory().resume_from_review(
            request=request,
            planning=planning,
            state=state,
            run_id=state.trace_run_id,
            goal_id=state.goal_id,
        )
        return ResumedGoalLoop(request, planning, state.trace_run_id, state.goal_id, loop_result)

    def resume_user_input_review(
        self,
        review_request: ReviewRequest,
        *,
        dry_run: bool,
        transport: str | None,
    ) -> ResumedGoalLoop:
        state = self._decode_state(review_request)
        supplemental_input = (review_request.supplemental_input or "").strip()
        if not supplemental_input:
            raise ReviewResumeError("supplemental_input_required", "supplemental input is required to resume this review")
        try:
            resumed_utterance, planning = self._planning.plan_from_user_input(
                review_request,
                supplemental_input,
                primary_understanding_id=state.primary_understanding_id,
            )
        except PlanningSnapshotError as exc:
            raise ReviewResumeError("review_snapshot_invalid", str(exc)) from exc
        state = sync_loop_state_with_planning(normalize_state_for_plan(state, planning.plan), planning)
        request = CommandRequest(
            resumed_utterance,
            dry_run=dry_run,
            review_id=review_request.review_id,
            supplemental_input=supplemental_input,
            transport=transport,
        )
        loop_result = self._goal_loop_factory().resume_from_user_input(
            request=request,
            planning=planning,
            state=state,
            run_id=state.trace_run_id,
            goal_id=state.goal_id,
        )
        return ResumedGoalLoop(request, planning, state.trace_run_id, state.goal_id, loop_result)

    def _decode_state(self, review_request: ReviewRequest) -> LoopState:
        try:
            return decode_loop_snapshot(review_request.snapshot_payload)
        except LoopSnapshotError as exc:
            raise ReviewResumeError("review_snapshot_invalid", str(exc)) from exc

    def _decode_planning(self, review_request: ReviewRequest) -> PlanningArtifacts:
        try:
            return self._planning.from_snapshot(
                utterance=review_request.utterance,
                payload=review_request.plan_payload or {},
            )
        except PlanningSnapshotError as exc:
            raise ReviewResumeError("review_snapshot_invalid", str(exc)) from exc
