from __future__ import annotations

from .acceptance_service import AcceptanceService
from .core.adapters.task_repository import SqliteTaskRepository
from .core.domain import transition
from .core.domain.task import TaskEventType, TaskLease, TaskRun, TaskStatus
from .durable_action_executor import DurableActionExecutor
from .durable_task_planning import DurablePlanningCoordinator
from .durable_task_support import event, restore_step_results
from .models import CommandRequest, PermissionReview
from .observation_service import ObservationService
from .planning_models import PlanningArtifacts
from .review_service import ReviewService
from .task_models import PlanExecutionResult, TaskPlan, TaskStep


DriveResult = tuple[
    TaskRun,
    PlanningArtifacts,
    PlanExecutionResult | None,
    PermissionReview | None,
    str | None,
    str | None,
    bool,
]


class DurableTaskDriver:
    """Runs one bounded durable step while the engine owns the task lease."""

    def __init__(
        self,
        repository: SqliteTaskRepository,
        acceptance: AcceptanceService,
        action_executor: DurableActionExecutor,
        observation: ObservationService,
        reviews: ReviewService,
        plans: DurablePlanningCoordinator,
    ) -> None:
        self.repository = repository
        self.acceptance = acceptance
        self.action_executor = action_executor
        self.observation = observation
        self.reviews = reviews
        self.plans = plans

    def drive_once(
        self,
        state: TaskRun,
        planning: PlanningArtifacts,
        request: CommandRequest,
        *,
        run_id: str,
        lease: TaskLease,
        execution: PlanExecutionResult | None,
        review: PermissionReview | None,
        review_id: str | None,
        approved_review_id: str | None,
    ) -> DriveResult:
        results = restore_step_results(self.repository.receipts(state.task_id, state.active_plan_revision_id))
        plan = planning.plan
        assert plan is not None
        if state.status is TaskStatus.READY:
            next_step = _next_step(plan, state.completed_step_ids)
            if next_step is None:
                execution = self.acceptance.assess(
                    plan,
                    results,
                    request,
                    run_id,
                    planning.understanding.understanding_id,
                    planning.candidate_set.candidate_set_id if planning.candidate_set else None,
                    planning.route_decision.route_decision_id if planning.route_decision else None,
                )
                state, planning = self.plans.finish_or_recover(state, planning, execution, request, lease)
                keep_driving = state.status in {TaskStatus.READY, TaskStatus.REPLANNING}
                return state, planning, execution, review, review_id, approved_review_id, not keep_driving
            state, review, review_id = self._prepare_step(
                state,
                plan,
                next_step,
                request,
                approved_review_id=approved_review_id,
                lease=lease,
            )
            approved_review_id = None
            if state.status in {TaskStatus.AWAITING_REVIEW, TaskStatus.FAILED, TaskStatus.BLOCKED}:
                return state, planning, execution, review, review_id, approved_review_id, True
        if state.status is TaskStatus.RUNNING:
            state = self.action_executor.execute(state, plan, request, run_id=run_id, lease=lease)
            if state.status in {TaskStatus.PAUSED, TaskStatus.RECONCILING}:
                return state, planning, execution, review, review_id, approved_review_id, True
            if state.status is TaskStatus.READY and state.last_event == TaskEventType.ACTION_FAILED.value:
                results = restore_step_results(self.repository.receipts(state.task_id, state.active_plan_revision_id))
                execution = self.acceptance.assess(
                    plan,
                    results,
                    request,
                    run_id,
                    planning.understanding.understanding_id,
                    planning.candidate_set.candidate_set_id if planning.candidate_set else None,
                    planning.route_decision.route_decision_id if planning.route_decision else None,
                )
                state, planning = self.plans.finish_or_recover(state, planning, execution, request, lease)
                keep_driving = state.status in {TaskStatus.READY, TaskStatus.REPLANNING}
                return state, planning, execution, review, review_id, approved_review_id, not keep_driving
        if state.status is TaskStatus.VERIFYING:
            state = self._commit(
                state,
                TaskEventType.STEP_EVIDENCE_RECORDED,
                step_id=state.current_step_id,
                reason="step receipt was persisted for final acceptance",
                lease=lease,
            )
        should_break = state.status not in {TaskStatus.READY, TaskStatus.RUNNING, TaskStatus.VERIFYING}
        return state, planning, execution, review, review_id, approved_review_id, should_break

    def _prepare_step(
        self,
        state: TaskRun,
        plan: TaskPlan,
        step: TaskStep,
        request: CommandRequest,
        *,
        approved_review_id: str | None,
        lease: TaskLease,
    ) -> tuple[TaskRun, PermissionReview, str | None]:
        pre = self.observation.observe(plan=plan, step=step, phase="pre", level="L0")
        review, record = self.reviews.review_step(plan, step, pre)
        if not review.allowed:
            state = self._commit(state, TaskEventType.FAIL, reason=review.reason, terminal_status=TaskStatus.BLOCKED, lease=lease)
            return state, review, None
        review_id = "review_" + record.step_safety_review_id.removeprefix("srev_")
        if review.review_required and approved_review_id != review_id:
            state = self._commit(
                state,
                TaskEventType.REVIEW_REQUIRED,
                interaction_id=review_id,
                step_id=step.id,
                reason=review.reason,
                lease=lease,
            )
            return state, review, review_id
        state = self._commit(state, TaskEventType.DISPATCH_REQUESTED, step_id=step.id, reason="step passed safety review", lease=lease)
        return state, review, review_id if review.review_required else None

    def _commit(self, state: TaskRun, kind: TaskEventType, *, lease: TaskLease, reason: str = "", **fields: object) -> TaskRun:
        return self.repository.commit(transition(state, event(state, kind, reason=reason, **fields)), lease=lease)


def _next_step(plan: TaskPlan, completed: tuple[str, ...]) -> TaskStep | None:
    completed_set = set(completed)
    return next((step for step in plan.steps if step.id not in completed_set and all(item in completed_set for item in step.depends_on)), None)
