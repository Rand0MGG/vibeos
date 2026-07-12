from __future__ import annotations

from .capabilities import CAPABILITIES
from .expected_states import validate_expected_state
from .models import Intent
from .permissions import (
    MAX_NAME_LENGTH,
    validate_target,
)
from .task_models import PlanValidationResult, TaskPlan


SUPPORTED_TASK_PLAN_SCHEMAS = {"v0.3", "v0.4", "v0.5"}
MAX_DISPLAY_GOAL_LENGTH = MAX_NAME_LENGTH
MAX_DISPLAY_EXPLANATION_LENGTH = 240
MAX_DISPLAY_ASSUMPTION_LENGTH = MAX_NAME_LENGTH
MAX_DISPLAY_ASSUMPTION_COUNT = 5


def validate_plan(plan: TaskPlan) -> PlanValidationResult:
    errors: list[str] = []
    warnings: list[str] = []

    if plan.schema_version not in SUPPORTED_TASK_PLAN_SCHEMAS:
        errors.append(f"unsupported schema_version: {plan.schema_version}")
    if not plan.plan_id:
        errors.append("plan_id is required")

    route_ids = [route.id for route in plan.routes]
    step_ids = [step.id for step in plan.steps]

    if len(route_ids) != len(set(route_ids)):
        errors.append("route ids must be unique")
    if len(step_ids) != len(set(step_ids)):
        errors.append("step ids must be unique")
    if not plan.routes:
        errors.append("at least one route is required")
    if not plan.steps:
        errors.append("at least one step is required")
    if plan.selected_route_id and plan.selected_route_id not in route_ids:
        errors.append(f"selected_route_id {plan.selected_route_id!r} is not present in routes")
    if not plan.selected_route_id:
        errors.append("selected_route_id is required")

    _validate_display_fields(plan, errors)
    _validate_steps(plan, errors)
    _validate_dependency_graph(plan, errors)

    return PlanValidationResult(ok=not errors, plan_id=plan.plan_id, errors=tuple(errors), warnings=tuple(warnings))


def _validate_display_fields(plan: TaskPlan, errors: list[str]) -> None:
    if len(plan.display.goal) > MAX_DISPLAY_GOAL_LENGTH:
        errors.append(f"display.goal exceeds {MAX_DISPLAY_GOAL_LENGTH} characters")
    if len(plan.display.explanation) > MAX_DISPLAY_EXPLANATION_LENGTH:
        errors.append(f"display.explanation exceeds {MAX_DISPLAY_EXPLANATION_LENGTH} characters")
    if len(plan.display.assumptions) > MAX_DISPLAY_ASSUMPTION_COUNT:
        errors.append(f"display.assumptions exceeds {MAX_DISPLAY_ASSUMPTION_COUNT} items")
    for assumption in plan.display.assumptions:
        if len(assumption) > MAX_DISPLAY_ASSUMPTION_LENGTH:
            errors.append(f"display.assumptions item exceeds {MAX_DISPLAY_ASSUMPTION_LENGTH} characters")


def _validate_steps(plan: TaskPlan, errors: list[str]) -> None:
    step_ids = {step.id for step in plan.steps}
    for step in plan.steps:
        if step.action not in CAPABILITIES:
            errors.append(f"step {step.id} uses unknown capability {step.action!r}")
        if step.capability_id != step.action:
            errors.append(f"step {step.id} capability_id must match action")
        target_error = validate_target(Intent(action=step.action, target=step.target))
        if target_error:
            errors.append(f"step {step.id} target invalid: {target_error}")
        for dep in step.depends_on:
            if dep not in step_ids:
                errors.append(f"step {step.id} depends on unknown step {dep!r}")
        if step.expected_state is None:
            errors.append(f"step {step.id} missing expected_state")
        else:
            expected_state_error = validate_expected_state(step.expected_state.kind, step.expected_state.fields)
            if expected_state_error:
                errors.append(f"step {step.id} {expected_state_error}")
        if step.provenance is None:
            errors.append(f"step {step.id} missing provenance")
        for precondition in step.preconditions:
            if precondition.kind == "capability_available" and precondition.capability_id not in CAPABILITIES:
                errors.append(f"step {step.id} precondition references unknown capability {precondition.capability_id!r}")


def _validate_dependency_graph(plan: TaskPlan, errors: list[str]) -> None:
    adjacency = {step.id: tuple(step.depends_on) for step in plan.steps}
    visited: set[str] = set()
    stack: set[str] = set()

    def visit(node: str) -> None:
        if node in stack:
            errors.append("step dependency graph contains a cycle")
            return
        if node in visited:
            return
        visited.add(node)
        stack.add(node)
        for dep in adjacency.get(node, ()):
            visit(dep)
        stack.remove(node)

    for step_id in adjacency:
        visit(step_id)
