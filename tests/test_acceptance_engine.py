from vibeos.acceptance import AcceptanceEngine
from vibeos.domain_models import ObservationReceipt, ObservationRequest, ResolvedContextPackage
from vibeos.semantic_acceptance import SemanticAcceptanceDecision, SemanticAcceptanceProvider, SemanticEvidenceSummary
from vibeos.task_models import PlanExecutionResult, TaskPlan, TaskRoute


def test_browser_acceptance_fails_on_tls_error_fixture() -> None:
    engine = AcceptanceEngine()
    plan = TaskPlan(
        schema_version="v2",
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
    assert result.semantic_decision == "semantic_failure"
    assert result.semantic_summary_id is not None
    assert result.semantic_acceptance_decision_id is not None


def test_browser_acceptance_fails_on_dns_error_fixture() -> None:
    engine = AcceptanceEngine()
    plan = TaskPlan(
        schema_version="v2",
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
    assert result.semantic_decision == "semantic_failure"


def test_browser_acceptance_fails_on_http_404_fixture() -> None:
    engine = AcceptanceEngine()
    plan = TaskPlan(
        schema_version="v2",
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
    assert result.semantic_decision == "semantic_failure"


def test_browser_acceptance_passes_with_matching_query_fixture() -> None:
    engine = AcceptanceEngine()
    plan = TaskPlan(
        schema_version="v2",
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
    assert result.semantic_decision == "complete"
    assert result.semantic_acceptance_decision_id is not None
    assert result.semantic_summary_id is not None


def test_browser_acceptance_is_indeterminate_when_only_requested_query_exists() -> None:
    engine = AcceptanceEngine()
    plan = TaskPlan(
        schema_version="v2",
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
    assert result.semantic_decision == "incomplete"


def test_browser_acceptance_is_indeterminate_when_navigation_was_only_requested() -> None:
    engine = AcceptanceEngine()
    plan = TaskPlan(
        schema_version="v2",
        plan_id="plan_requested_navigation_only",
        utterance="open https://www.baidu.com",
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
                payload={
                    "status": "requested",
                    "requested_url": "https://www.baidu.com",
                    "active_url": None,
                    "query": None,
                    "page_title": None,
                },
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

    assert result.status == "indeterminate"
    assert result.semantic_decision == "incomplete"


def test_browser_acceptance_summary_and_decision_remain_deterministic_by_default() -> None:
    engine = AcceptanceEngine()
    plan = TaskPlan(
        schema_version="v2",
        plan_id="plan_complete",
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
        understanding_id="und_1",
        candidate_set_id="cset_1",
        route_decision_id="rdec_1",
    )

    assert result.status == "passed"
    assert result.semantic_decision == "complete"


class FixedSemanticProvider(SemanticAcceptanceProvider):
    provider_name = "fake_semantic_acceptance"
    model_name = "fake-structured"

    def summarize(self, *, input_payload):
        return SemanticEvidenceSummary(
            semantic_summary_id="ssum_fixed",
            understanding_id=str(input_payload.get("understanding_id") or ""),
            candidate_set_id=str(input_payload.get("candidate_set_id") or ""),
            route_decision_id=str(input_payload.get("route_decision_id") or ""),
            route_domain=str(input_payload.get("route_domain") or ""),
            summary_text="fake provider says evidence is sufficient",
            structured_findings={
                "supports_completion": True,
                "evidence_incomplete": False,
                "contradiction_detected": False,
                "clarification_needed": False,
            },
            provider_name=self.provider_name,
            model_name=self.model_name,
        )

    def decide(self, *, summary: SemanticEvidenceSummary, allowed_decisions):
        return SemanticAcceptanceDecision(
            semantic_acceptance_decision_id="sacc_fixed",
            semantic_summary_id=summary.semantic_summary_id,
            understanding_id=summary.understanding_id,
            candidate_set_id=summary.candidate_set_id,
            route_decision_id=summary.route_decision_id,
            decision="complete",
            acceptance_status="passed",
            reason="fake provider selected complete",
            provider_name=self.provider_name,
            model_name=self.model_name,
        )


class CapturingSemanticProvider(SemanticAcceptanceProvider):
    provider_name = "capturing_semantic_acceptance"
    model_name = "fake-structured"

    def __init__(self) -> None:
        self.summary_input = None

    def summarize(self, *, input_payload):
        self.summary_input = input_payload
        return SemanticEvidenceSummary(
            semantic_summary_id="ssum_capture",
            understanding_id=str(input_payload.get("understanding_id") or ""),
            candidate_set_id=str(input_payload.get("candidate_set_id") or ""),
            route_decision_id=str(input_payload.get("route_decision_id") or ""),
            route_domain=str(input_payload.get("route_domain") or ""),
            summary_text="captured structured evidence",
            structured_findings={
                "supports_completion": False,
                "evidence_incomplete": True,
                "contradiction_detected": False,
                "clarification_needed": False,
            },
            provider_name=self.provider_name,
            model_name=self.model_name,
        )

    def decide(self, *, summary: SemanticEvidenceSummary, allowed_decisions):
        return SemanticAcceptanceDecision(
            semantic_acceptance_decision_id="sacc_capture",
            semantic_summary_id=summary.semantic_summary_id,
            understanding_id=summary.understanding_id,
            candidate_set_id=summary.candidate_set_id,
            route_decision_id=summary.route_decision_id,
            decision="incomplete",
            acceptance_status="indeterminate",
            reason="captured evidence is intentionally incomplete",
            provider_name=self.provider_name,
            model_name=self.model_name,
        )


class CountingSemanticProvider(SemanticAcceptanceProvider):
    provider_name = "counting_semantic_acceptance"
    model_name = "fake-structured"

    def __init__(self) -> None:
        self.summary_calls = 0

    def summarize(self, *, input_payload):
        self.summary_calls += 1
        return SemanticEvidenceSummary(
            semantic_summary_id="ssum_counting",
            understanding_id=str(input_payload.get("understanding_id") or ""),
            candidate_set_id=str(input_payload.get("candidate_set_id") or ""),
            route_decision_id=str(input_payload.get("route_decision_id") or ""),
            route_domain=str(input_payload.get("route_domain") or ""),
            summary_text="counting provider generated one reusable summary",
            structured_findings={
                "supports_completion": True,
                "evidence_incomplete": False,
                "contradiction_detected": False,
                "clarification_needed": False,
            },
            provider_name=self.provider_name,
            model_name=self.model_name,
        )

    def decide(self, *, summary: SemanticEvidenceSummary, allowed_decisions):
        return SemanticAcceptanceDecision(
            semantic_acceptance_decision_id="sacc_counting",
            semantic_summary_id=summary.semantic_summary_id,
            understanding_id=summary.understanding_id,
            candidate_set_id=summary.candidate_set_id,
            route_decision_id=summary.route_decision_id,
            decision="complete",
            acceptance_status="passed",
            reason="counting provider selected complete",
            provider_name=self.provider_name,
            model_name=self.model_name,
        )


def test_acceptance_engine_supports_bounded_provider_override() -> None:
    engine = AcceptanceEngine(provider=FixedSemanticProvider())
    plan = TaskPlan(
        schema_version="v2",
        plan_id="plan_provider_override",
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
        understanding_id="und_1",
        candidate_set_id="cset_1",
        route_decision_id="rdec_1",
    )

    assert result.status == "passed"
    assert result.semantic_summary_id == "ssum_fixed"
    assert result.semantic_acceptance_decision_id == "sacc_fixed"
    assert result.semantic_decision == "complete"


def test_acceptance_engine_passes_structured_browser_evidence_to_provider() -> None:
    provider = CapturingSemanticProvider()
    engine = AcceptanceEngine(provider=provider)
    plan = TaskPlan(
        schema_version="v2",
        plan_id="plan_structured_evidence",
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
                    "status": "loaded",
                    "requested_url": "https://www.google.com/search?q=hello",
                    "active_url": "https://www.google.com/search?q=hello",
                    "requested_query": "hello",
                    "query": "hello",
                    "page_title": "hello - Search",
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
                "status": "passed",
                "message": "browser verifier observed the requested search query",
                "details": {"query": "hello"},
            },
        ),
        observation_request=ObservationRequest(active_domain_ids=("browser",), postcondition_package_ids=("browser_context",)),
        observation_receipt=receipt,
        understanding_id="und_1",
        candidate_set_id="cset_1",
        route_decision_id="rdec_1",
    )

    assert result.status == "indeterminate"
    assert provider.summary_input is not None
    assert provider.summary_input["browser_context"]["active_url"] == "https://www.google.com/search?q=hello"
    assert provider.summary_input["browser_context"]["page_title"] == "hello - Search"
    assert provider.summary_input["verification_evidence"][0]["verifier_id"] == "browser_search_route_completed"
    assert provider.summary_input["verification_evidence"][0]["details"]["query"] == "hello"


def test_acceptance_engine_reuses_semantic_summary_for_identical_evidence() -> None:
    provider = CountingSemanticProvider()
    engine = AcceptanceEngine(provider=provider)
    plan = TaskPlan(
        schema_version="v2",
        plan_id="plan_cached_summary",
        utterance="search web for hello",
        selected_route_id="browser_search_web_route",
        routes=(TaskRoute(id="browser_search_web_route", score=1.0, domain_id="browser"),),
        steps=(),
    )
    execution = PlanExecutionResult(plan_id=plan.plan_id, status="succeeded", execution_status="succeeded")
    kwargs = {
        "plan": plan,
        "execution": execution,
        "verification_results": (
            {
                "verifier_id": "browser_search_route_completed",
                "status": "passed",
                "details": {"query": "hello"},
            },
        ),
        "understanding_id": "und_1",
        "candidate_set_id": "cset_1",
        "route_decision_id": "rdec_1",
    }

    first = engine.evaluate(**kwargs)
    second = engine.evaluate(**kwargs)

    assert first.status == "passed"
    assert second.status == "passed"
    assert provider.summary_calls == 1
