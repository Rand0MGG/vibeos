from dataclasses import replace

from vibeos.models import Intent
from vibeos.planner import normalize_intent_to_task_plan
from vibeos.task_models import ExpectedState
from vibeos.task_validation import validate_plan


def test_legacy_intent_normalizes_to_valid_one_step_task_plan() -> None:
    plan = normalize_intent_to_task_plan(
        Intent(action="clipboard.write", target={"text": "hello"}, reason="user asked to write clipboard"),
        "clipboard hello",
    )

    validation = validate_plan(plan)

    assert validation.ok is True
    assert len(plan.steps) == 1
    assert plan.steps[0].action == "clipboard.write"
    assert plan.steps[0].expected_state is not None
    assert plan.steps[0].expected_state.kind == "clipboard_content_requested"


def test_validate_plan_rejects_unknown_expected_state_kind() -> None:
    plan = normalize_intent_to_task_plan(
        Intent(action="clipboard.write", target={"text": "hello"}, reason="user asked to write clipboard"),
        "clipboard hello",
    )
    bad_step = replace(plan.steps[0], expected_state=ExpectedState(kind="not_registered"))
    bad_plan = replace(plan, steps=(bad_step,))

    validation = validate_plan(bad_plan)

    assert validation.ok is False
    assert any("unknown expected_state.kind" in error for error in validation.errors)


def test_validate_plan_rejects_cyclic_dependencies() -> None:
    plan = normalize_intent_to_task_plan(
        Intent(action="app.open", target={"name": "browser"}, reason="user asked to open browser"),
        "open browser",
    )
    step = replace(plan.steps[0], depends_on=(plan.steps[0].id,))
    cyclic_plan = replace(plan, steps=(step,))

    validation = validate_plan(cyclic_plan)

    assert validation.ok is False
    assert "step dependency graph contains a cycle" in validation.errors


def test_validate_plan_rejects_missing_selected_route() -> None:
    plan = normalize_intent_to_task_plan(
        Intent(action="app.open", target={"name": "browser"}, reason="user asked to open browser"),
        "open browser",
    )
    bad_plan = replace(plan, selected_route_id="missing_route")

    validation = validate_plan(bad_plan)

    assert validation.ok is False
    assert any("selected_route_id" in error for error in validation.errors)
