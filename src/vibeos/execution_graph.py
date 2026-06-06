from __future__ import annotations

from typing import Callable

from .capabilities import CAPABILITIES
from .task_models import ExecutionState, PlanExecutionResult, StepExecutionResult, TaskPlan, TaskStep


def topological_steps(plan: TaskPlan) -> list[TaskStep]:
    steps_by_id = {step.id: step for step in plan.steps}
    visited: set[str] = set()
    temp: set[str] = set()
    ordered: list[TaskStep] = []

    def visit(step_id: str) -> None:
        if step_id in visited:
            return
        if step_id in temp:
            raise ValueError("task plan contains a dependency cycle")
        temp.add(step_id)
        step = steps_by_id[step_id]
        for dep in step.depends_on:
            visit(dep)
        temp.remove(step_id)
        visited.add(step_id)
        ordered.append(step)

    for step in plan.steps:
        visit(step.id)
    return ordered


def execute_plan_graph(
    plan: TaskPlan,
    execute_step: Callable[[TaskStep], StepExecutionResult],
    execute_batch: Callable[[tuple[TaskStep, ...]], tuple[StepExecutionResult, ...]] | None = None,
) -> PlanExecutionResult:
    ordered_steps = topological_steps(plan)
    ordered_ids = [step.id for step in ordered_steps]
    steps_by_id = {step.id: step for step in ordered_steps}
    results_by_id: dict[str, StepExecutionResult] = {}
    all_results: list[StepExecutionResult] = []
    completed: set[str] = set()

    while len(completed) < len(ordered_steps):
        ready_steps = [
            steps_by_id[step_id]
            for step_id in ordered_ids
            if step_id not in completed and all(dep in results_by_id for dep in steps_by_id[step_id].depends_on)
        ]
        if not ready_steps:
            raise ValueError("task plan execution reached a dead end")

        blocked_steps = [
            step
            for step in ready_steps
            if any(results_by_id[dep].status != "succeeded" for dep in step.depends_on)
        ]
        for step in blocked_steps:
            blocked_dep = next(dep for dep in step.depends_on if results_by_id[dep].status != "succeeded")
            blocked = StepExecutionResult(
                step_id=step.id,
                layer="executor_schedule",
                status="blocked",
                diagnostics={"parallel_batch_size": 1, "parallel_group": step.parallel_group},
                result={"blocked_by": blocked_dep},
                error=f"blocked by dependency {blocked_dep}",
            )
            results_by_id[step.id] = blocked
            all_results.append(blocked)
            completed.add(step.id)

        executable_ready = [step for step in ready_steps if step.id not in completed]
        if not executable_ready:
            continue

        batch = next_batch(executable_ready)
        if len(batch) == 1:
            result = execute_step(batch[0])
            result = with_scheduler_batch(result, batch)
            results_by_id[batch[0].id] = result
            all_results.append(result)
            completed.add(batch[0].id)
            continue

        if execute_batch is None:
            batch_results = tuple(with_scheduler_batch(execute_step(step), batch) for step in batch)
        else:
            batch_results = tuple(with_scheduler_batch(result, batch) for result in execute_batch(batch))

        if len(batch_results) != len(batch):
            raise ValueError("execute_batch must return one result per step")

        for step, result in zip(batch, batch_results):
            results_by_id[step.id] = result
            all_results.append(result)
            completed.add(step.id)

    overall = overall_status(tuple(all_results))
    error = next((item.error for item in all_results if item.error), None)
    return PlanExecutionResult(plan_id=plan.plan_id, status=overall, step_results=tuple(all_results), error=error)


def overall_status(step_results: tuple[StepExecutionResult, ...]) -> ExecutionState:
    statuses = {item.status for item in step_results}
    if not step_results:
        return "rejected"
    if "failed" in statuses:
        return "failed"
    if "rejected" in statuses:
        return "rejected"
    if "needs_user_input" in statuses:
        return "needs_user_input"
    if "blocked" in statuses:
        return "blocked"
    if statuses == {"succeeded"}:
        return "succeeded"
    return "failed"


def next_batch(ready_steps: list[TaskStep]) -> tuple[TaskStep, ...]:
    first = ready_steps[0]
    if not is_parallel_eligible(first) or not first.parallel_group:
        return (first,)
    return tuple(
        step
        for step in ready_steps
        if step.parallel_group == first.parallel_group and is_parallel_eligible(step)
    )


def is_parallel_eligible(step: TaskStep) -> bool:
    spec = CAPABILITIES.get(step.capability_id)
    if spec is None:
        return False
    return spec.risk_level == "L0" or spec.parallel_safe


def with_scheduler_batch(result: StepExecutionResult, batch: tuple[TaskStep, ...]) -> StepExecutionResult:
    diagnostics = {
        **result.diagnostics,
        "parallel_batch_size": len(batch),
        "parallel_group": batch[0].parallel_group,
    }
    return StepExecutionResult(
        step_id=result.step_id,
        layer=result.layer,
        status=result.status,
        adapter=result.adapter,
        capability_id=result.capability_id,
        attempt=result.attempt,
        duration_ms=result.duration_ms,
        adapter_status=result.adapter_status,
        diagnostics=diagnostics,
        error_code=result.error_code,
        result=result.result,
        error=result.error,
        audit_id=result.audit_id,
    )
