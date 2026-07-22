from vibeos.domain_models import ObservationReceipt, ResolvedContextPackage
from vibeos.observation_service import ObservationService
from vibeos.task_models import DisplayFields, ExpectedState, StepPrecondition, StepProvenance, TaskPlan, TaskRoute, TaskStep
from vibeos.verifiers import default_verifier_registry


def test_observation_service_uses_route_minimum_context_for_o0_pre_observation(monkeypatch) -> None:
    captured = {}

    def fake_resolve_observation_request(request, registry):
        captured["request"] = request
        return _receipt_for(request.requested_context_package_ids)

    monkeypatch.setattr("vibeos.observation_service.resolve_observation_request", fake_resolve_observation_request)

    service = ObservationService(default_verifier_registry())
    plan = make_plan("browser_search_web_route", "browser", "browser.search_web")
    observation = service.observe(plan=plan, step=plan.steps[0], phase="pre", level="O0")

    assert captured["request"].requested_context_package_ids == ("session_context", "browser_context")
    assert captured["request"].postcondition_package_ids == ()
    assert observation.phase == "pre"
    assert observation.level == "O0"
    assert set(observation.packages) == {"session_context", "browser_context"}


def test_observation_service_uses_route_required_context_for_o1_pre_observation(monkeypatch) -> None:
    captured = {}

    def fake_resolve_observation_request(request, registry):
        captured["request"] = request
        return _receipt_for(request.requested_context_package_ids)

    monkeypatch.setattr("vibeos.observation_service.resolve_observation_request", fake_resolve_observation_request)

    service = ObservationService(default_verifier_registry())
    plan = make_plan("browser_search_web_route", "browser", "browser.search_web")
    service.observe(plan=plan, step=plan.steps[0], phase="pre", level="O1")

    assert captured["request"].requested_context_package_ids == ("session_context", "browser_context", "window_context")
    assert captured["request"].postcondition_package_ids == ()


def test_observation_service_uses_allowed_domain_context_for_o2_post_observation(monkeypatch) -> None:
    captured = {}

    def fake_resolve_post_execution_observation(request, registry, harness):
        captured["request"] = request
        return _receipt_for(request.postcondition_package_ids)

    monkeypatch.setattr("vibeos.observation_service.resolve_post_execution_observation", fake_resolve_post_execution_observation)

    service = ObservationService(default_verifier_registry())
    plan = make_plan("browser_search_web_route", "browser", "browser.search_web")
    observation = service.observe(plan=plan, step=plan.steps[0], phase="post", level="O2")

    assert captured["request"].requested_context_package_ids == ()
    assert captured["request"].postcondition_package_ids == ("session_context", "window_context", "browser_context")
    assert observation.phase == "post"
    assert observation.level == "O2"
    assert set(observation.packages) == {"session_context", "window_context", "browser_context"}


def test_observation_service_does_not_expand_apps_domain_beyond_allowed_context(monkeypatch) -> None:
    captured = {}

    def fake_resolve_post_execution_observation(request, registry, harness):
        captured["request"] = request
        return _receipt_for(request.postcondition_package_ids)

    monkeypatch.setattr("vibeos.observation_service.resolve_post_execution_observation", fake_resolve_post_execution_observation)

    service = ObservationService(default_verifier_registry())
    plan = make_plan("apps_open_route", "apps", "app.open")
    service.observe(plan=plan, step=plan.steps[0], phase="post", level="O2")

    assert captured["request"].postcondition_package_ids == ("session_context",)


def make_plan(route_id: str, domain_id: str, capability_id: str) -> TaskPlan:
    return TaskPlan(
        schema_version="v2",
        plan_id=f"plan_{route_id}",
        utterance="test observation",
        display=DisplayFields(goal="observe", explanation="test plan"),
        selected_route_id=route_id,
        routes=(
            TaskRoute(
                id=route_id,
                score=1.0,
                domain_id=domain_id,
                display=DisplayFields(explanation="test route"),
                score_inputs={},
                required_capabilities=(capability_id,),
                default_verifier_ids=(),
            ),
        ),
        steps=(
            TaskStep(
                id="step_1",
                action=capability_id,
                capability_id=capability_id,
                target={"query": "hello"} if capability_id == "browser.search_web" else {"name": "browser"},
                expected_state=ExpectedState(kind="test_state", fields={}),
                preconditions=(StepPrecondition(kind="capability_available", capability_id=capability_id),),
                provenance=StepProvenance(source_span_id="span_1", planner="test"),
            ),
        ),
        provenance={"planner": "test"},
    )


def _receipt_for(package_ids: tuple[str, ...]) -> ObservationReceipt:
    return ObservationReceipt(
        requested_package_ids=package_ids,
        loaded_package_ids=package_ids,
        skipped_package_ids=(),
        packages=tuple(
            ResolvedContextPackage(
                package_id=package_id,
                status="loaded",
                payload={"package_id": package_id, "status": "loaded"},
                payload_bytes=32,
                freshness_ts="2026-06-24T00:00:00Z",
            )
            for package_id in package_ids
        ),
        warnings=(),
        errors=(),
    )
