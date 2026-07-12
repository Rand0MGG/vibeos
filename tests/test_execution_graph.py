from vibeos.execution_graph import execute_plan_graph
from vibeos.task_models import DisplayFields, StepExecutionResult, TaskPlan, TaskRoute, TaskStep


def test_execute_plan_graph_groups_parallel_safe_l0_steps() -> None:
    plan = TaskPlan(
        schema_version="v0.3",
        plan_id="plan_parallel",
        utterance="inspect apps and windows",
        display=DisplayFields(goal="inspect apps and windows"),
        selected_route_id="inspect_route",
        routes=(TaskRoute(id="inspect_route", score=1.0, required_capabilities=("app.list", "window.list")),),
        steps=(
            TaskStep(id="step_apps", action="app.list", capability_id="app.list", parallel_group="g1"),
            TaskStep(id="step_windows", action="window.list", capability_id="window.list", parallel_group="g1"),
        ),
    )
    batches: list[tuple[str, ...]] = []

    def execute_batch(batch: tuple[TaskStep, ...]) -> tuple[StepExecutionResult, ...]:
        batches.append(tuple(step.id for step in batch))
        return tuple(
            StepExecutionResult(
                step_id=step.id,
                layer="adapter_execute",
                status="succeeded",
                capability_id=step.capability_id,
            )
            for step in batch
        )

    execution = execute_plan_graph(
        plan,
        execute_step=lambda step: StepExecutionResult(step_id=step.id, layer="adapter_execute", status="succeeded", capability_id=step.capability_id),
        execute_batch=execute_batch,
    )

    assert execution.status == "succeeded"
    assert batches == [("step_apps", "step_windows")]
    assert execution.step_results[0].diagnostics["parallel_batch_size"] == 2
    assert execution.step_results[1].diagnostics["parallel_batch_size"] == 2


def test_execute_plan_graph_keeps_state_changing_steps_sequential() -> None:
    plan = TaskPlan(
        schema_version="v0.3",
        plan_id="plan_sequential",
        utterance="open app and notify",
        display=DisplayFields(goal="open app and notify"),
        selected_route_id="sequential_route",
        routes=(TaskRoute(id="sequential_route", score=1.0, required_capabilities=("app.open", "notification.send")),),
        steps=(
            TaskStep(id="step_open", action="app.open", capability_id="app.open", target={"name": "browser"}, parallel_group="g1"),
            TaskStep(id="step_notify", action="notification.send", capability_id="notification.send", target={"title": "done"}, parallel_group="g1"),
        ),
    )
    order: list[str] = []
    batch_sizes: list[int] = []

    def execute_step(step: TaskStep) -> StepExecutionResult:
        order.append(step.id)
        return StepExecutionResult(step_id=step.id, layer="adapter_execute", status="succeeded", capability_id=step.capability_id)

    def execute_batch(batch: tuple[TaskStep, ...]) -> tuple[StepExecutionResult, ...]:
        batch_sizes.append(len(batch))
        return tuple(execute_step(step) for step in batch)

    execution = execute_plan_graph(plan, execute_step=execute_step, execute_batch=execute_batch)

    assert execution.status == "succeeded"
    assert order == ["step_open", "step_notify"]
    assert batch_sizes == []
    assert execution.step_results[0].diagnostics["parallel_batch_size"] == 1
    assert execution.step_results[1].diagnostics["parallel_batch_size"] == 1
