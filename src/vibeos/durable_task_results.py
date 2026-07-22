from __future__ import annotations

from .core.adapters.task_repository import SqliteTaskRepository
from .core.domain.task import TaskRun, TaskStatus
from .durable_task_models import DurableTaskResult
from .durable_task_support import overall_status, public_attempts, restore_step_results, selected_target
from .models import CommandRequest, EffectAssessment
from .planning_models import PlanningArtifacts
from .task_models import PlanExecutionResult


class DurableTaskResultFactory:
    def __init__(self, repository: SqliteTaskRepository) -> None:
        self.repository = repository

    def build(
        self,
        state: TaskRun,
        request: CommandRequest,
        planning: PlanningArtifacts | None,
        *,
        execution: PlanExecutionResult | None = None,
        review: EffectAssessment | None = None,
        review_id: str | None = None,
        message: str = "",
        run_id: str | None = None,
    ) -> DurableTaskResult:
        results = restore_step_results(self.repository.receipts(state.task_id, state.active_plan_revision_id))
        plan = planning.plan if planning is not None else None
        return DurableTaskResult(
            task=state,
            request=request,
            planning=planning,
            step_results=results,
            attempts=public_attempts(run_id or state.task_id, plan, results),
            execution=execution,
            review=review,
            review_id=review_id or state.pending_interaction_id,
            message=message or state.pending_reason or (state.terminal_outcome.reason if state.terminal_outcome else ""),
            selected_target=selected_target(results),
            execution_status=execution.execution_status
            if execution is not None
            else ("dry_run" if state.status is TaskStatus.DRY_RUN else "succeeded" if state.status is TaskStatus.SUCCEEDED else "not_started"),
            acceptance_status=execution.acceptance_status if execution is not None else ("passed" if state.status is TaskStatus.SUCCEEDED else "skipped"),
            overall_status=overall_status(state, execution),
        )
