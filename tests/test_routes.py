from vibeos.routes import select_best_plan
from vibeos.task_models import DisplayFields, TaskPlan, TaskRoute, TaskStep


def test_route_selector_prefers_lower_risk_when_scores_tie() -> None:
    low_risk = make_plan("route_low", ("app.open",), ("L1",))
    high_risk = make_plan("route_high", ("app.open",), ("L2",))

    selected = select_best_plan([high_risk, low_risk], capability_context={"app.open"})

    assert selected is not None
    assert selected.selected_route_id == "route_low"


def test_route_selector_prefers_fewer_steps_when_score_and_risk_tie() -> None:
    short_plan = make_plan("route_short", ("app.list",), ("L0",))
    long_plan = make_plan("route_long", ("app.list",), ("L0", "L0"))

    selected = select_best_plan([long_plan, short_plan], capability_context={"app.list"})

    assert selected is not None
    assert selected.selected_route_id == "route_short"


def test_route_selector_uses_route_id_sort_as_final_tie_break() -> None:
    route_b = make_plan("route_b", ("app.list",), ("L0",))
    route_a = make_plan("route_a", ("app.list",), ("L0",))

    selected = select_best_plan([route_b, route_a], capability_context={"app.list"})

    assert selected is not None
    assert selected.selected_route_id == "route_a"


def make_plan(route_id: str, required_capabilities: tuple[str, ...], risk_levels: tuple[str, ...]) -> TaskPlan:
    steps = tuple(
        TaskStep(
            id=f"step_{index}",
            action=required_capabilities[0],
            capability_id=required_capabilities[0],
            risk_level=risk,
        )
        for index, risk in enumerate(risk_levels, start=1)
    )
    return TaskPlan(
        schema_version="v0.3",
        plan_id=f"plan_{route_id}",
        utterance=route_id,
        display=DisplayFields(goal=route_id),
        selected_route_id=route_id,
        routes=(TaskRoute(id=route_id, score=0.0, required_capabilities=required_capabilities),),
        steps=steps,
    )
