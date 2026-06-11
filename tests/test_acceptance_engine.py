from vibeos.acceptance import AcceptanceEngine
from vibeos.domain_models import ObservationReceipt, ObservationRequest, ResolvedContextPackage
from vibeos.task_models import PlanExecutionResult, TaskPlan, TaskRoute


def test_browser_acceptance_fails_on_tls_error_fixture() -> None:
    engine = AcceptanceEngine()
    plan = TaskPlan(
        schema_version="v0.5",
        plan_id="plan_tls",
        utterance="open https://example.com",
        selected_route_id="browser_open_url_route",
        routes=(TaskRoute(id="browser_open_url_route", score=1.0, domain_id="browser"),),
        steps=(),
    )
    execution = PlanExecutionResult(plan_id=plan.plan_id, status="succeeded", execution_status="succeeded")
    receipt = ObservationReceipt(
        requested_package_ids=("browser_context",),
        loaded_package_ids=("browser_context",),
        packages=(
            ResolvedContextPackage(
                package_id="browser_context",
                payload={"error_state": "tls_error"},
            ),
        ),
    )

    result = engine.evaluate(
        plan=plan,
        execution=execution,
        verification_results=(),
        observation_request=ObservationRequest(active_domain_ids=("browser",), postcondition_package_ids=("browser_context",)),
        observation_receipt=receipt,
    )

    assert result.status == "failed"
    assert result.reasons == ("tls_error",)


def test_browser_acceptance_fails_on_dns_error_fixture() -> None:
    engine = AcceptanceEngine()
    plan = TaskPlan(
        schema_version="v0.5",
        plan_id="plan_dns",
        utterance="open https://example.com",
        selected_route_id="browser_open_url_route",
        routes=(TaskRoute(id="browser_open_url_route", score=1.0, domain_id="browser"),),
        steps=(),
    )
    execution = PlanExecutionResult(plan_id=plan.plan_id, status="succeeded", execution_status="succeeded")
    receipt = ObservationReceipt(
        requested_package_ids=("browser_context",),
        loaded_package_ids=("browser_context",),
        packages=(
            ResolvedContextPackage(
                package_id="browser_context",
                payload={"error_state": "dns_error"},
            ),
        ),
    )

    result = engine.evaluate(
        plan=plan,
        execution=execution,
        verification_results=(),
        observation_request=ObservationRequest(active_domain_ids=("browser",), postcondition_package_ids=("browser_context",)),
        observation_receipt=receipt,
    )

    assert result.status == "failed"
    assert result.reasons == ("dns_error",)


def test_browser_acceptance_fails_on_http_404_fixture() -> None:
    engine = AcceptanceEngine()
    plan = TaskPlan(
        schema_version="v0.5",
        plan_id="plan_404",
        utterance="open https://example.com/missing",
        selected_route_id="browser_open_url_route",
        routes=(TaskRoute(id="browser_open_url_route", score=1.0, domain_id="browser"),),
        steps=(),
    )
    execution = PlanExecutionResult(plan_id=plan.plan_id, status="succeeded", execution_status="succeeded")
    receipt = ObservationReceipt(
        requested_package_ids=("browser_context",),
        loaded_package_ids=("browser_context",),
        packages=(
            ResolvedContextPackage(
                package_id="browser_context",
                payload={"error_state": "http_404"},
            ),
        ),
    )

    result = engine.evaluate(
        plan=plan,
        execution=execution,
        verification_results=(),
        observation_request=ObservationRequest(active_domain_ids=("browser",), postcondition_package_ids=("browser_context",)),
        observation_receipt=receipt,
    )

    assert result.status == "failed"
    assert result.reasons == ("http_404",)


def test_browser_acceptance_passes_with_matching_query_fixture() -> None:
    engine = AcceptanceEngine()
    plan = TaskPlan(
        schema_version="v0.5",
        plan_id="plan_query",
        utterance="search web for hello",
        selected_route_id="browser_search_web_route",
        routes=(TaskRoute(id="browser_search_web_route", score=1.0, domain_id="browser"),),
        steps=(),
    )
    execution = PlanExecutionResult(plan_id=plan.plan_id, status="succeeded", execution_status="succeeded")

    result = engine.evaluate(
        plan=plan,
        execution=execution,
        verification_results=(
            {
                "verifier_id": "browser_search_route_completed",
                "status": "passed",
                "details": {"query": "hello"},
            },
        ),
        observation_request=ObservationRequest(active_domain_ids=("browser",), postcondition_package_ids=("browser_context",)),
        observation_receipt=ObservationReceipt(),
    )

    assert result.status == "passed"


def test_browser_acceptance_is_indeterminate_when_only_requested_query_exists() -> None:
    engine = AcceptanceEngine()
    plan = TaskPlan(
        schema_version="v0.5",
        plan_id="plan_requested_only",
        utterance="search web for hello",
        selected_route_id="browser_search_web_route",
        routes=(TaskRoute(id="browser_search_web_route", score=1.0, domain_id="browser"),),
        steps=(),
    )
    execution = PlanExecutionResult(plan_id=plan.plan_id, status="succeeded", execution_status="succeeded")
    receipt = ObservationReceipt(
        requested_package_ids=("browser_context",),
        loaded_package_ids=("browser_context",),
        packages=(
            ResolvedContextPackage(
                package_id="browser_context",
                payload={
                    "status": "requested",
                    "requested_url": "https://www.google.com/search?q=hello",
                    "requested_query": "hello",
                    "active_url": None,
                    "query": None,
                },
            ),
        ),
    )

    result = engine.evaluate(
        plan=plan,
        execution=execution,
        verification_results=(
            {
                "verifier_id": "browser_search_route_completed",
                "status": "failed",
                "message": "search verifier did not observe a browser search query",
                "details": {"expected_query": "hello"},
            },
        ),
        observation_request=ObservationRequest(active_domain_ids=("browser",), postcondition_package_ids=("browser_context",)),
        observation_receipt=receipt,
    )

    assert result.status == "indeterminate"
