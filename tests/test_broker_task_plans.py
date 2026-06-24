from pathlib import Path
from uuid import uuid4
from urllib.parse import parse_qs, urlparse

from vibeos.apps import AppRegistry
from vibeos.audit import AuditLog
from vibeos.browser_state import record_browser_observation
from vibeos.broker import CapabilityBroker
from vibeos.candidate_selection import CandidateSelectionDecision, CandidateSelectionProvider
from vibeos.clarification import ClarificationDecision, ClarificationProvider
from vibeos.goal_models import GoalSpec, GoalSynthesisProvenance, GoalSynthesisResult, ProviderExchange
from vibeos.goal_synthesizer import GoalSynthesisProvider
from vibeos.execution_graph import execute_plan_graph
from vibeos.intent import IntentBroker
from vibeos.models import AppEntry, CommandRequest, Intent
from vibeos.intent import RuleIntentBroker
from vibeos.planner import PlanningArtifacts, browser_media_plan
from vibeos.portal import PortalAdapter
from vibeos.reviews import ReviewStore
from vibeos.semantic_acceptance import SemanticAcceptanceDecision, SemanticAcceptanceProvider, SemanticEvidenceSummary
from vibeos.strategy import StrategySelectionProvider, StrategySelectionResult, make_strategy_decision
from vibeos.task_trace import TaskTraceStore
from vibeos.models import WindowEntry
from vibeos.task_models import DisplayFields, ExpectedState, StepExecutionResult, StepPrecondition, StepProvenance, TaskPlan, TaskRoute, TaskSpan, TaskStep, UtteranceAnalysis
from vibeos.understanding import UnderstandingAnalysisDecision, UnderstandingAnalysisProvider, UnderstandingArtifact, UnderstandingTransitionProvider
from vibeos.verifiers import VerifierHarness


class FakeApps(AppRegistry):
    def list_apps(self):
        return [AppEntry(desktop_id="firefox.desktop", name="Firefox", keywords=("browser",))]

    def open_app(self, app):
        return {"status": "opened", "desktop_id": app.desktop_id}


class FakePortal(PortalAdapter):
    def open_uri(self, uri: str) -> dict[str, object]:
        return {"status": "opened", "uri": uri}


class ObservedPortal(PortalAdapter):
    def open_uri(self, uri: str) -> dict[str, object]:
        parsed = urlparse(uri)
        params = parse_qs(parsed.query)
        observed_query = ""
        for key in ("q", "query", "wd", "p", "text", "search_query"):
            values = params.get(key)
            if values:
                observed_query = str(values[0])
                break
        record_browser_observation(active_url=uri, query=observed_query or None, adapter="fake-browser")
        return {"status": "opened", "uri": uri, "adapter": "fake-browser"}


class FakeNotifications:
    def send(self, title: str, body: str = "") -> dict[str, object]:
        return {"status": "sent", "title": title, "adapter": "/usr/bin/notify-send"}


class FakeWindows:
    def list_windows(self):
        return [WindowEntry(window_id="1", app_id="firefox.desktop", title="Firefox", focused=True)]

    def resolve(self, query):
        return self.list_windows() if query.lower() in {"firefox", "browser", "current"} else []

    def focus(self, window):
        return {"status": "focused", "window_id": window.window_id}

    def minimize(self, window):
        return {"status": "minimized", "window_id": window.window_id}

    def maximize(self, window):
        return {"status": "maximized", "window_id": window.window_id}

    def close(self, window):
        return {"status": "closed", "window_id": window.window_id}


class FailingIntentBroker(IntentBroker):
    def parse(self, utterance: str) -> Intent:
        raise AssertionError("raw utterance should not be reparsed for compatibility intent")


class TimeoutClipboard:
    def write(self, text: str) -> dict[str, object]:
        return {"status": "timeout", "error": "clipboard helper timed out", "adapter": "/usr/bin/wl-copy"}


class FakeClipboard:
    def write(self, text: str) -> dict[str, object]:
        return {"status": "written", "adapter": "/usr/bin/wl-copy", "text": text}


class StaticIntentBroker(IntentBroker):
    def __init__(self, intent):
        self.intent = intent

    def parse(self, utterance):
        return self.intent


class CountingStaticIntentBroker(IntentBroker):
    def __init__(self, intent):
        self.intent = intent
        self.call_count = 0

    def parse(self, utterance):
        self.call_count += 1
        return self.intent


class FixedUnderstandingTransitionProvider(UnderstandingTransitionProvider):
    provider_name = "test_understanding_transition"
    model_name = "deterministic-test"

    def transition(self, *, understanding, current_analysis, decision, failure):
        return UnderstandingAnalysisDecision(
            analysis=UtteranceAnalysis(
                utterance=understanding.utterance,
                type="task",
                confidence=0.97,
                domains=("browser",),
                explanation="test transition provider moved the request onto the browser domain",
                task_spans=(
                    TaskSpan(
                        id="span_1",
                        text=understanding.utterance,
                        start=0,
                        end=len(understanding.utterance),
                        domain="browser",
                        confidence=0.97,
                    ),
                ),
                provenance=None,
            ),
            provider_name=self.provider_name,
            model_name=self.model_name,
            request_payload={"action": decision.action, "failure_class": failure.failure_class},
            response_payload={"analysis_type": "task", "domains": ["browser"]},
        )


class FakeStackUnderstandingProvider(UnderstandingAnalysisProvider):
    provider_name = "fake_understanding"
    model_name = "fake-structured"

    def __init__(self, analysis: UtteranceAnalysis) -> None:
        self.analysis = analysis

    def analyze(self, *, utterance: str, broker) -> UnderstandingAnalysisDecision:
        return UnderstandingAnalysisDecision(
            analysis=self.analysis,
            provider_name=self.provider_name,
            model_name=self.model_name,
            request_payload={"utterance": utterance},
            response_payload={"analysis_type": self.analysis.type},
        )


class CachedIntentTaskWithoutSpanUnderstandingProvider(UnderstandingAnalysisProvider):
    provider_name = "cached_intent_task_without_span"
    model_name = "deterministic-test"

    def analyze(self, *, utterance: str, broker) -> UnderstandingAnalysisDecision:
        broker.parse(utterance)
        return UnderstandingAnalysisDecision(
            analysis=UtteranceAnalysis(
                utterance=utterance,
                type="task",
                confidence=0.94,
                domains=("browser",),
                explanation="test provider classified the utterance as a browser task without emitting a concrete span",
                task_spans=(),
                provenance=None,
            ),
            provider_name=self.provider_name,
            model_name=self.model_name,
            request_payload={"utterance": utterance},
            response_payload={"analysis_type": "task", "domains": ["browser"]},
        )


class FakeStackClarificationProvider(ClarificationProvider):
    provider_name = "fake_clarification"
    model_name = "fake-structured"

    def generate(self, *, utterance: str, analysis) -> ClarificationDecision:
        return ClarificationDecision(
            clarification_question_id="cqid_fake_stack",
            question="Which exact site should I open?",
            reason="fake clarification provider requested the minimal missing detail",
            provider_name=self.provider_name,
            model_name=self.model_name,
        )


class FakeStackGoalSynthesisProvider(GoalSynthesisProvider):
    provider_name = "fake_goal_synthesizer"
    provider_version = "v0.fake"
    model_name = "fake-structured"

    def __init__(self, payload: dict[str, object]) -> None:
        self.payload = payload

    def synthesize(self, utterance: str, analysis) -> dict[str, object]:
        self._last_parse_valid = True
        self._last_fallback_used = False
        self._last_error = None
        self._last_raw_output = str(self.payload)
        return dict(self.payload)


class FakeStackRouteSelectionProvider(CandidateSelectionProvider):
    provider_name = "fake_route_selector"
    model_name = "fake-structured"

    def __init__(self, selected_candidate_id: str | None, action: str = "select") -> None:
        self.selected_candidate_id = selected_candidate_id
        self.action = action

    def decide(self, *, understanding, candidate_set) -> CandidateSelectionDecision:
        return CandidateSelectionDecision(
            route_decision_id="rdec_fake_stack",
            candidate_set_id=candidate_set.candidate_set_id,
            understanding_id=candidate_set.understanding_id,
            action=self.action,
            selected_candidate_id=self.selected_candidate_id,
            reason="fake route selector chose a bounded candidate",
            provider_name=self.provider_name,
            model_name=self.model_name,
        )


class FakeStackSemanticAcceptanceProvider(SemanticAcceptanceProvider):
    provider_name = "fake_semantic_acceptance"
    model_name = "fake-structured"

    def summarize(self, *, input_payload):
        return SemanticEvidenceSummary(
            semantic_summary_id="ssum_fake_stack",
            understanding_id=str(input_payload.get("understanding_id") or ""),
            candidate_set_id=str(input_payload.get("candidate_set_id") or ""),
            route_decision_id=str(input_payload.get("route_decision_id") or ""),
            route_domain=str(input_payload.get("route_domain") or ""),
            summary_text="fake semantic provider considers the goal satisfied",
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
            semantic_acceptance_decision_id="sacc_fake_stack",
            semantic_summary_id=summary.semantic_summary_id,
            understanding_id=summary.understanding_id,
            candidate_set_id=summary.candidate_set_id,
            route_decision_id=summary.route_decision_id,
            decision="complete",
            acceptance_status="passed",
            reason="fake semantic provider marked the goal complete",
            provider_name=self.provider_name,
            model_name=self.model_name,
        )


class FakeBrokerStrategySelectionProvider(StrategySelectionProvider):
    provider_name = "fake_strategy_selector"
    model_name = "fake-structured"

    def __init__(self, selected_strategy_id: str) -> None:
        self.selected_strategy_id = selected_strategy_id

    def decide(self, *, utterance: str, eligible, constraints, environment, attempts, last_failure_class: str):
        return StrategySelectionResult(
            decision=make_strategy_decision(
                action="select",
                reason="fake strategy provider selected an allowed strategy",
                selected_strategy_id=self.selected_strategy_id,
                constraints=constraints,
                failure_class=last_failure_class,
                provider_name=self.provider_name,
                model_name=self.model_name,
            ),
            request_payload={"utterance": utterance, "eligible": [candidate.strategy_id for _, candidate in eligible]},
            response_payload={"action": "select", "selected_strategy_id": self.selected_strategy_id},
        )


def test_review_task_plan_allows_browser_media_route_without_l2_review() -> None:
    broker = CapabilityBroker(
        intent_broker=StaticIntentBroker(Intent.unknown("should not reparse")),
        apps=FakeApps(),
        portal=FakePortal(),
        audit=AuditLog(make_audit_path("plan-review")),
        reviews=ReviewStore(make_review_path("plan-review")),
    )
    plan = browser_media_plan("play baby", media_span(), "baby")

    review = broker.review_task_plan(plan)

    assert review.status == "allowed"
    assert review.review_id is None
    assert review.max_risk_level == "L1"
    assert [item.action for item in review.step_reviews] == ["browser.open_site_search"]


def test_browser_media_route_executes_with_verification_metadata() -> None:
    audit = AuditLog(make_audit_path("approve-plan-review"))
    reviews = ReviewStore(make_review_path("approve-plan-review"))
    broker = CapabilityBroker(
        intent_broker=StaticIntentBroker(Intent.unknown("approval must execute stored task plan")),
        apps=FakeApps(),
        portal=FakePortal(),
        audit=audit,
        reviews=reviews,
        verifier_harness=VerifierHarness({"browser_search_route_completed": {"query": "baby"}}),
    )
    plan = browser_media_plan("play baby", media_span(), "baby")
    result = broker.execute_task_plan(plan)
    entries = audit.tail(10)
    step_entries = [item for item in entries if item.get("step_id")]

    assert result.status == "succeeded"
    assert result.plan_id == plan.plan_id
    assert [item.status for item in result.step_results] == ["succeeded"]
    assert result.step_results[0].adapter == "browser.semantic"
    assert result.step_results[0].capability_id == "browser.open_site_search"
    assert result.step_results[0].adapter_status == "succeeded"
    assert result.step_results[0].duration_ms is not None
    assert result.step_results[0].diagnostics["site"] == "youtube.com"
    assert result.verification_status == "passed"
    assert result.verification_results[0]["verifier_id"] == "browser_search_route_completed"


def test_broker_compatibility_intent_reuses_planning_step_when_provider_intent_is_missing() -> None:
    broker = CapabilityBroker(intent_broker=FailingIntentBroker(), audit=AuditLog(), reviews=ReviewStore())
    plan = TaskPlan(
        schema_version="v0.5",
        plan_id="plan_browser_search",
        utterance="search web for hello",
        display=DisplayFields(goal="search the web"),
        selected_route_id="browser_search_web_route",
        routes=(TaskRoute(id="browser_search_web_route", score=1.0, domain_id="browser", required_capabilities=("browser.search_web",)),),
        steps=(
            TaskStep(
                id="browser_search_web",
                action="browser.search_web",
                capability_id="browser.search_web",
                target={"query": "hello"},
                expected_state=ExpectedState(kind="search_results_available", fields={"query": "hello"}),
                preconditions=(StepPrecondition(kind="capability_available", capability_id="browser.search_web"),),
                provenance=StepProvenance(source_span_id="span_1", planner="test"),
            ),
        ),
    )
    analysis = UtteranceAnalysis(
        utterance="search web for hello",
        type="task",
        confidence=0.99,
        domains=("browser",),
        explanation="already understood as a browser search task",
        task_spans=(TaskSpan(id="span_1", text="search web for hello", start=0, end=20, domain="browser", confidence=0.99),),
        provenance=None,
    )
    understanding = UnderstandingArtifact(
        understanding_id="und_test",
        utterance="search web for hello",
        analysis=analysis,
        primary_understanding_id="und_test",
        provider_intent=None,
    )
    planning = PlanningArtifacts(
        understanding=understanding,
        analysis=analysis,
        goal_synthesis=None,
        plan=plan,
        candidates=(plan,),
    )

    compatibility_intent = broker._compatibility_intent_from_planning(planning)

    assert compatibility_intent.action == "browser.search_web"
    assert compatibility_intent.target["query"] == "hello"


def test_broker_handle_does_not_fall_back_to_legacy_direct_execution_when_planning_stalls() -> None:
    intent_broker = CountingStaticIntentBroker(
        Intent(
            action="browser.search_web",
            target={"query": "OpenAI 文档"},
            reason="test broker resolved the utterance as a browser search",
        )
    )
    broker = CapabilityBroker(
        intent_broker=intent_broker,
        audit=AuditLog(),
        reviews=ReviewStore(),
        understanding_analysis_provider=CachedIntentTaskWithoutSpanUnderstandingProvider(),
        goal_synthesis_provider=FakeStackGoalSynthesisProvider(
            {
                "status": "ready",
                "goal_type": "browser_search_web",
                "candidate_domain_ids": [],
                "required_capability_ids": ["browser.search_web"],
                "missing_capability_ids": [],
                "clarification_questions": [],
                "constraints": ["Planner must use registered domains, routes, and capability families only."],
                "fallback_hints": [],
                "assumptions": ["test provider intentionally emitted no executable candidate boundary"],
                "assistant_intent": None,
                "subgoals": [],
                "message": "goal synthesis completed",
            }
        ),
    )

    result = broker.handle(CommandRequest("帮我查一下 OpenAI 文档"))

    assert result.status == "failed"
    assert result.execution_status == "not_started"
    assert result.acceptance_status == "skipped"
    assert result.overall_status == "failed"
    assert result.message == "planner did not produce a task plan"
    assert result.intent.action == "browser.search_web"
    assert intent_broker.call_count == 1


def test_browser_media_route_reports_verification_failure_when_harness_disagrees() -> None:
    broker = CapabilityBroker(
        intent_broker=StaticIntentBroker(Intent.unknown("should not reparse")),
        portal=FakePortal(),
        audit=AuditLog(make_audit_path("browser-verifier-fail")),
        reviews=ReviewStore(make_review_path("browser-verifier-fail")),
        verifier_harness=VerifierHarness({"browser_search_route_completed": {"query": "other"}}),
    )

    execution = broker.execute_task_plan(browser_media_plan("play baby", media_span(), "baby"))

    assert execution.status == "succeeded"
    assert execution.verification_status == "failed"
    assert execution.verification_results[0]["status"] == "failed"


def test_browser_media_route_does_not_treat_requested_query_as_observed_without_harness() -> None:
    broker = CapabilityBroker(
        intent_broker=StaticIntentBroker(Intent.unknown("should not reparse")),
        portal=FakePortal(),
        audit=AuditLog(make_audit_path("browser-context-verifier-incomplete")),
        reviews=ReviewStore(make_review_path("browser-context-verifier-incomplete")),
    )

    execution = broker.execute_task_plan(browser_media_plan("play baby", media_span(), "baby"))

    assert execution.status == "succeeded"
    assert execution.verification_status == "failed"
    assert execution.verification_results[0]["status"] == "failed"
    assert execution.acceptance_status == "indeterminate"


def test_browser_media_route_uses_observed_browser_context_for_verification_without_harness() -> None:
    broker = CapabilityBroker(
        intent_broker=RuleIntentBroker(),
        portal=ObservedPortal(),
        audit=AuditLog(make_audit_path("browser-context-observed-pass")),
        reviews=ReviewStore(make_review_path("browser-context-observed-pass")),
    )

    result = broker.handle(CommandRequest("search web for hello"))

    assert result.status == "executed"
    assert result.result["execution"]["status"] == "succeeded"
    assert result.result["execution"]["verification_status"] == "passed"
    assert result.result["execution"]["verification_results"][0]["status"] == "passed"
    assert result.result["execution"]["acceptance_status"] == "passed"


def test_browser_requests_expose_runtime_state_on_main_path() -> None:
    broker = CapabilityBroker(
        intent_broker=RuleIntentBroker(),
        portal=ObservedPortal(),
        audit=AuditLog(make_audit_path("browser-v06-runtime-state")),
        reviews=ReviewStore(make_review_path("browser-v06-runtime-state")),
    )

    result = broker.handle(CommandRequest("search web for hello", debug=True))

    assert result.status == "executed"
    assert result.result["goal_runtime"]["goal_id"].startswith("goal_")
    assert result.result["goal_runtime"]["status"] == "completed"
    assert result.result["environment_profile"]["search_policy"] == "browser_first"
    assert result.result["selected_strategy_id"] == "strategy_browser_search_web_route"
    assert result.result["strategy_candidates"]
    assert result.result["run_ledger"]["terminal_outcome"]["status"] == "completed"
    assert result.result["run_ledger"]["attempts"][0]["understanding_id"] == result.result["understanding"]["understanding_id"]
    assert result.result["run_ledger"]["attempts"][0]["candidate_set_id"] == result.result["candidate_set"]["candidate_set_id"]
    assert result.result["run_ledger"]["attempts"][0]["route_decision_id"] == result.result["route_decision"]["route_decision_id"]
    assert result.result["debug_trace"]["runtime_task_plan"]["goal_runtime"]["goal_id"] == result.result["goal_runtime"]["goal_id"]
    assert result.result["debug_trace"]["runtime_task_plan"]["environment_profile"]["search_policy"] == "browser_first"
    assert result.result["debug_trace"]["runtime_task_plan"]["provider_artifacts"]


def test_broker_main_path_supports_explicit_fake_strategy_selection_provider() -> None:
    broker = CapabilityBroker(
        intent_broker=RuleIntentBroker(),
        portal=ObservedPortal(),
        audit=AuditLog(make_audit_path("fake-strategy-provider")),
        reviews=ReviewStore(make_review_path("fake-strategy-provider")),
        strategy_selection_provider=FakeBrokerStrategySelectionProvider("strategy_browser_search_web_route"),
    )

    result = broker.handle(CommandRequest("search web for hello", debug=True))

    assert result.status == "executed"
    assert result.result["selected_strategy_id"] == "strategy_browser_search_web_route"
    assert result.result["run_ledger"]["strategy_history"][0]["provider_name"] == "fake_strategy_selector"


def test_app_requests_expose_runtime_state_on_main_path() -> None:
    broker = CapabilityBroker(
        intent_broker=RuleIntentBroker(),
        apps=FakeApps(),
        audit=AuditLog(make_audit_path("app-v06-runtime-state")),
        reviews=ReviewStore(make_review_path("app-v06-runtime-state")),
    )

    result = broker.handle(CommandRequest("open browser", debug=True))

    assert result.status == "executed"
    assert result.result["goal_runtime"]["goal_id"].startswith("goal_")
    assert result.result["goal_runtime"]["status"] == "completed"
    assert result.result["environment_profile"]["search_policy"] == "balanced"
    assert result.result["selected_strategy_id"] == "strategy_apps_open_route"
    assert result.result["strategy_candidates"]
    assert result.result["run_ledger"]["terminal_outcome"]["status"] == "completed"
    assert result.result["run_ledger"]["attempts"][0]["understanding_id"] == result.result["understanding"]["understanding_id"]
    assert result.result["run_ledger"]["attempts"][0]["candidate_set_id"] == result.result["candidate_set"]["candidate_set_id"]
    assert result.result["run_ledger"]["attempts"][0]["route_decision_id"] == result.result["route_decision"]["route_decision_id"]
    assert result.result["execution"]["acceptance_status"] == "passed"
    assert result.result["debug_trace"]["runtime_task_plan"]["goal_runtime"]["goal_id"] == result.result["goal_runtime"]["goal_id"]
    assert result.result["debug_trace"]["runtime_task_plan"]["environment_profile"]["search_policy"] == "balanced"
    assert result.result["debug_trace"]["runtime_task_plan"]["provider_artifacts"]


def test_broker_main_path_supports_explicit_fake_provider_stack_without_rule_fallback(monkeypatch) -> None:
    def fail_rule_understanding(*args, **kwargs):
        raise AssertionError("deterministic understanding fallback should not run")

    def fail_rule_goal_synthesis(*args, **kwargs):
        raise AssertionError("rule goal synthesis fallback should not run")

    def fail_rule_route_selection(*args, **kwargs):
        raise AssertionError("deterministic route selection fallback should not run")

    monkeypatch.setattr("vibeos.understanding.DeterministicUnderstandingAnalysisProvider.analyze", fail_rule_understanding)
    monkeypatch.setattr("vibeos.goal_synthesizer.RuleBasedGoalSynthesisProvider.synthesize", fail_rule_goal_synthesis)
    monkeypatch.setattr("vibeos.candidate_selection.DeterministicCandidateSelectionProvider.decide", fail_rule_route_selection)

    broker = CapabilityBroker(
        intent_broker=StaticIntentBroker(Intent(action="browser.search_web", target={"query": "hello"}, reason="fake intent broker")),
        portal=ObservedPortal(),
        audit=AuditLog(make_audit_path("fake-provider-stack")),
        reviews=ReviewStore(make_review_path("fake-provider-stack")),
        understanding_analysis_provider=FakeStackUnderstandingProvider(
            UtteranceAnalysis(
                utterance="search web for hello",
                type="task",
                confidence=0.99,
                domains=("browser",),
                explanation="fake understanding provider classified this as a browser search task",
                task_spans=(TaskSpan(id="span_1", text="search web for hello", start=0, end=20, domain="browser", confidence=0.99),),
            )
        ),
        goal_synthesis_provider=FakeStackGoalSynthesisProvider(
            {
                "status": "ready",
                "goal_type": "browser_search_web",
                "candidate_domain_ids": ["browser"],
                "required_capability_ids": ["browser.search_web"],
                "missing_capability_ids": [],
                "clarification_questions": [],
                "constraints": ["fake provider stayed inside host-owned capabilities"],
                "fallback_hints": [],
                "assumptions": ["fake provider stack"],
                "assistant_intent": None,
                "subgoals": [
                    {
                        "subgoal_id": "subgoal_1",
                        "text": "search web for hello",
                        "goal_type": "browser_search_web",
                        "candidate_domain_ids": ["browser"],
                        "required_capability_ids": ["browser.search_web"],
                    }
                ],
                "message": "fake provider synthesized a ready goal",
            }
        ),
        route_selection_provider=FakeStackRouteSelectionProvider("cand_browser_search_web_route"),
    )

    result = broker.handle(CommandRequest("search web for hello"))

    assert result.status == "executed"
    assert result.overall_status == "completed"
    assert result.result["understanding"]["analysis_provider_name"] == "fake_understanding"
    assert result.result["goal_synthesis"]["exchange"]["provider_name"] == "fake_goal_synthesizer"
    assert result.result["route_decision"]["provider_name"] == "fake_route_selector"


def test_broker_main_path_supports_explicit_fake_clarification_provider(monkeypatch) -> None:
    def fail_rule_clarification(*args, **kwargs):
        raise AssertionError("deterministic clarification fallback should not run")

    monkeypatch.setattr("vibeos.clarification.DeterministicClarificationProvider.generate", fail_rule_clarification)

    broker = CapabilityBroker(
        intent_broker=StaticIntentBroker(Intent.unknown("fake intent broker should not be needed for clarification")),
        audit=AuditLog(make_audit_path("fake-clarification-stack")),
        reviews=ReviewStore(make_review_path("fake-clarification-stack")),
        understanding_analysis_provider=FakeStackUnderstandingProvider(
            UtteranceAnalysis(
                utterance="open that site we discussed yesterday",
                type="clarification",
                confidence=0.97,
                domains=("browser",),
                explanation="fake understanding provider marked the target as ambiguous",
                task_spans=(),
                chat_response="Which exact site should I open?",
            )
        ),
        clarification_provider=FakeStackClarificationProvider(),
        goal_synthesis_provider=FakeStackGoalSynthesisProvider(
            {
                "status": "clarification_needed",
                "goal_type": "clarification",
                "candidate_domain_ids": ["browser"],
                "required_capability_ids": [],
                "missing_capability_ids": [],
                "clarification_questions": ["Which exact site should I open?"],
                "constraints": [],
                "fallback_hints": [],
                "assumptions": ["fake provider stack"],
                "assistant_intent": None,
                "subgoals": [],
                "message": "fake provider requested clarification",
            }
        ),
        route_selection_provider=FakeStackRouteSelectionProvider(None, action="clarify"),
    )

    result = broker.handle(CommandRequest("open that site we discussed yesterday"))

    assert result.status == "ambiguous"
    assert result.overall_status == "needs_user_input"
    assert result.result["understanding"]["analysis_provider_name"] == "fake_understanding"
    assert result.result["understanding"]["clarification_provider_name"] == "fake_clarification"
    assert result.result["understanding"]["clarification_question_id"] == "cqid_fake_stack"


def test_broker_maps_clarify_route_decision_with_candidates_to_needs_user_input() -> None:
    broker = CapabilityBroker(
        intent_broker=RuleIntentBroker(),
        route_selection_provider=FakeStackRouteSelectionProvider(None, action="clarify"),
        portal=ObservedPortal(),
        audit=AuditLog(make_audit_path("clarify-with-candidates")),
        reviews=ReviewStore(make_review_path("clarify-with-candidates")),
    )

    result = broker.handle(CommandRequest("open baidu.com"))

    assert result.status == "ambiguous"
    assert result.execution_status == "not_started"
    assert result.acceptance_status == "skipped"
    assert result.overall_status == "needs_user_input"
    assert result.result["route_decision"]["action"] == "clarify"
    assert result.result["candidates"]


def test_broker_execute_task_plan_supports_explicit_fake_semantic_acceptance_provider() -> None:
    broker = CapabilityBroker(
        intent_broker=StaticIntentBroker(Intent.unknown("semantic acceptance provider test uses a direct task plan")),
        portal=ObservedPortal(),
        audit=AuditLog(make_audit_path("fake-semantic-acceptance-provider")),
        reviews=ReviewStore(make_review_path("fake-semantic-acceptance-provider")),
        semantic_acceptance_provider=FakeStackSemanticAcceptanceProvider(),
    )

    plan = TaskPlan(
        schema_version="v0.5",
        plan_id="plan_fake_semantic_acceptance",
        utterance="search web for hello",
        display=DisplayFields(goal="search web for hello"),
        selected_route_id="browser_search_web_route",
        routes=(
            TaskRoute(
                id="browser_search_web_route",
                score=1.0,
                domain_id="browser",
                required_capabilities=("browser.search_web",),
                default_verifier_ids=("browser_search_route_completed",),
            ),
        ),
        steps=(
            TaskStep(
                id="browser_search_web",
                action="browser.search_web",
                capability_id="browser.search_web",
                target={"query": "hello"},
                expected_state=ExpectedState(kind="search_results_available", fields={"query": "hello"}),
                preconditions=(StepPrecondition(kind="capability_available", capability_id="browser.search_web"),),
                provenance=StepProvenance(source_span_id="span_1", planner="test"),
            ),
        ),
    )

    execution = broker.execute_task_plan(
        plan,
        understanding_id="und_fake",
        candidate_set_id="cset_fake",
        route_decision_id="rdec_fake",
    )

    assert execution.acceptance_status == "passed"
    assert execution.acceptance_result is not None
    assert execution.acceptance_result["semantic_summary_id"] == "ssum_fake_stack"
    assert execution.acceptance_result["semantic_acceptance_decision_id"] == "sacc_fake_stack"


def test_broker_reuses_goal_runtime_across_repeated_browser_turns() -> None:
    broker = CapabilityBroker(
        intent_broker=RuleIntentBroker(),
        portal=ObservedPortal(),
        audit=AuditLog(make_audit_path("browser-v06-repeated-turns")),
        reviews=ReviewStore(make_review_path("browser-v06-repeated-turns")),
    )

    first = broker.handle(CommandRequest("search web for hello"))
    second = broker.handle(CommandRequest("search web for hello"))

    assert first.result["goal_runtime"]["goal_id"] == second.result["goal_runtime"]["goal_id"]
    assert len(first.result["goal_runtime"]["turn_ids"]) == 1
    assert len(second.result["goal_runtime"]["turn_ids"]) == 2
    assert first.result["run"]["run_id"] != second.result["run"]["run_id"]


def test_broker_reuses_goal_runtime_across_repeated_app_turns() -> None:
    broker = CapabilityBroker(
        intent_broker=RuleIntentBroker(),
        apps=FakeApps(),
        audit=AuditLog(make_audit_path("app-v06-repeated-turns")),
        reviews=ReviewStore(make_review_path("app-v06-repeated-turns")),
    )

    first = broker.handle(CommandRequest("open browser"))
    second = broker.handle(CommandRequest("open browser"))

    assert first.result["goal_runtime"]["goal_id"] == second.result["goal_runtime"]["goal_id"]
    assert len(first.result["goal_runtime"]["turn_ids"]) == 1
    assert len(second.result["goal_runtime"]["turn_ids"]) == 2
    assert first.result["run"]["run_id"] != second.result["run"]["run_id"]


def test_broker_v06_bridge_respects_selected_route_boundary(monkeypatch) -> None:
    broker = CapabilityBroker(
        intent_broker=RuleIntentBroker(),
        apps=FakeApps(),
        portal=ObservedPortal(),
        audit=AuditLog(make_audit_path("v06-main-path-strategy-replacement")),
        reviews=ReviewStore(make_review_path("v06-main-path-strategy-replacement")),
    )

    apps_plan = TaskPlan(
        schema_version="v0.5",
        plan_id="plan_apps_notion",
        utterance="open Notion",
        display=DisplayFields(goal="open Notion as an installed app"),
        selected_route_id="apps_open_route",
        routes=(TaskRoute(id="apps_open_route", score=3.0, domain_id="apps", required_capabilities=("app.open",)),),
        steps=(
            TaskStep(
                id="open_notion_app",
                action="app.open",
                capability_id="app.open",
                target={"name": "Notion"},
                expected_state=ExpectedState(kind="app_opened_or_focused", fields={"app": "Notion"}),
                preconditions=(StepPrecondition(kind="capability_available", capability_id="app.open"),),
                provenance=StepProvenance(source_span_id="span_1", planner="test"),
            ),
        ),
        provenance={"planner": "test"},
    )
    browser_plan = TaskPlan(
        schema_version="v0.5",
        plan_id="plan_browser_notion",
        utterance="open Notion",
        display=DisplayFields(goal="search Notion in the browser"),
        selected_route_id="browser_search_web_route",
        routes=(TaskRoute(id="browser_search_web_route", score=1.0, domain_id="browser", required_capabilities=("browser.search_web",), default_verifier_ids=("browser_search_route_completed",)),),
        steps=(
            TaskStep(
                id="search_notion",
                action="browser.search_web",
                capability_id="browser.search_web",
                target={"query": "Notion"},
                expected_state=ExpectedState(kind="search_results_available", fields={"query": "Notion"}),
                preconditions=(StepPrecondition(kind="capability_available", capability_id="browser.search_web"),),
                provenance=StepProvenance(source_span_id="span_1", planner="test"),
            ),
        ),
        provenance={"planner": "test"},
    )
    goal_spec = GoalSpec(
        goal_id="goal_open_notion_main_path",
        goal_text="open Notion",
        goal_type="app_open",
        candidate_domain_ids=("apps", "browser"),
        required_capability_ids=("app.open", "browser.search_web"),
        synthesis_provenance=GoalSynthesisProvenance(provider_name="test", provider_version="v0.6"),
    )
    planning = PlanningArtifacts(
        understanding=make_understanding(
            UtteranceAnalysis(
                utterance="open Notion",
                type="task",
                confidence=1.0,
                domains=("apps", "browser"),
                explanation="try app first and then browser fallback",
                task_spans=(TaskSpan(id="span_1", text="open Notion", start=0, end=11, domain="apps", confidence=1.0),),
            )
        ),
        analysis=UtteranceAnalysis(
            utterance="open Notion",
            type="task",
            confidence=1.0,
            domains=("apps", "browser"),
            explanation="try app first and then browser fallback",
            task_spans=(TaskSpan(id="span_1", text="open Notion", start=0, end=11, domain="apps", confidence=1.0),),
        ),
        goal_synthesis=GoalSynthesisResult(
            status="ready",
            goal_spec=goal_spec,
            message="goal synthesis completed",
            exchange=ProviderExchange(provider_name="test", model_name="test", normalized_output={"status": "ready"}),
        ),
        plan=apps_plan,
        candidates=(apps_plan, browser_plan),
    )

    monkeypatch.setattr("vibeos.broker.plan_turn", lambda *args, **kwargs: planning)

    result = broker.handle(CommandRequest("open Notion", debug=True))

    assert result.status == "failed"
    assert result.overall_status == "blocked"
    assert result.result["goal_runtime"]["goal_id"] == "goal_open_notion_main_path"
    assert result.result["selected_strategy_id"] == "strategy_apps_open_route"
    assert len(result.result["attempts"]) == 3
    assert all(item["selected_route_id"] == "apps_open_route" for item in result.result["attempts"])
    assert result.result["attempts"][0]["failure"]["failure_class"] == "semantic_mismatch"
    assert result.result["run_ledger"]["terminal_outcome"]["status"] == "blocked"


def test_window_list_requests_expose_v06_runtime_state_on_main_path() -> None:
    broker = CapabilityBroker(
        intent_broker=RuleIntentBroker(),
        windows=FakeWindows(),
        audit=AuditLog(make_audit_path("window-list-v06-runtime-state")),
        reviews=ReviewStore(make_review_path("window-list-v06-runtime-state")),
    )

    result = broker.handle(CommandRequest("list windows", debug=True))

    assert result.status == "executed"
    assert result.result["goal_runtime"]["status"] == "completed"
    assert result.result["selected_strategy_id"] == "strategy_window_list_route"
    assert result.result["execution"]["step_results"][0]["adapter"] == "windows.registry"
    assert result.result["run_ledger"]["terminal_outcome"]["status"] == "completed"


def test_notification_requests_expose_v06_runtime_state_on_main_path() -> None:
    broker = CapabilityBroker(
        intent_broker=RuleIntentBroker(),
        notifications=FakeNotifications(),
        audit=AuditLog(make_audit_path("notification-v06-runtime-state")),
        reviews=ReviewStore(make_review_path("notification-v06-runtime-state")),
    )

    result = broker.handle(CommandRequest("notify hello", debug=True))

    assert result.status == "executed"
    assert result.result["goal_runtime"]["status"] == "completed"
    assert result.result["selected_strategy_id"] == "strategy_notification_send_route"
    assert result.result["execution"]["step_results"][0]["adapter"] == "notifications.send"
    assert result.result["run_ledger"]["terminal_outcome"]["status"] == "completed"


def test_system_status_requests_expose_v06_runtime_state_on_main_path() -> None:
    broker = CapabilityBroker(
        intent_broker=RuleIntentBroker(),
        portal=FakePortal(),
        audit=AuditLog(make_audit_path("system-status-v06-runtime-state")),
        reviews=ReviewStore(make_review_path("system-status-v06-runtime-state")),
    )

    result = broker.handle(CommandRequest("system status", debug=True))

    assert result.status == "executed"
    assert result.result["goal_runtime"]["status"] == "completed"
    assert result.result["selected_strategy_id"] == "strategy_system_status_route"
    assert result.result["execution"]["step_results"][0]["adapter"] == "system.status"
    assert result.result["run_ledger"]["terminal_outcome"]["status"] == "completed"


def test_execute_plan_graph_blocks_downstream_step_after_failure() -> None:
    plan = TaskPlan(
        schema_version="v0.3",
        plan_id="plan_blocked_graph",
        utterance="two step test",
        display=DisplayFields(goal="test blocked execution"),
        selected_route_id="test_route",
        routes=(TaskRoute(id="test_route", score=1.0, required_capabilities=("app.open", "portal.open_uri")),),
        steps=(
            TaskStep(
                id="open_browser",
                action="app.open",
                capability_id="app.open",
                target={"name": "browser"},
                expected_state=ExpectedState(kind="app_opened_or_focused", fields={"app": "browser"}),
                preconditions=(StepPrecondition(kind="capability_available", capability_id="app.open"),),
                provenance=StepProvenance(source_span_id="span_1", planner="test"),
            ),
            TaskStep(
                id="open_media_search_uri",
                action="portal.open_uri",
                capability_id="portal.open_uri",
                target={"uri": "https://example.com"},
                depends_on=("open_browser",),
                risk_level="L2",
                expected_state=ExpectedState(kind="uri_open_requested", fields={"uri": "https://example.com"}),
                preconditions=(StepPrecondition(kind="capability_available", capability_id="portal.open_uri"),),
                provenance=StepProvenance(source_span_id="span_1", planner="test"),
            ),
        ),
        provenance={"planner": "test"},
    )

    def execute_step(step: TaskStep) -> StepExecutionResult:
        if step.id == "open_browser":
            return StepExecutionResult(step_id=step.id, layer="adapter_execute", status="failed", error="browser failed")
        return StepExecutionResult(step_id=step.id, layer="adapter_execute", status="succeeded")

    execution = execute_plan_graph(plan, execute_step)

    assert execution.status == "failed"
    assert execution.step_results[0].status == "failed"
    assert execution.step_results[1].status == "blocked"
    assert execution.step_results[1].result["blocked_by"] == "open_browser"


def test_normal_ask_executes_allowed_task_plan() -> None:
    broker = CapabilityBroker(
        intent_broker=RuleIntentBroker(),
        apps=FakeApps(),
        portal=FakePortal(),
        audit=AuditLog(make_audit_path("normal-ask-plan-exec")),
        reviews=ReviewStore(make_review_path("normal-ask-plan-exec")),
    )

    result = broker.handle(CommandRequest("open browser"))

    assert result.status == "executed"
    assert result.selected_target == "firefox.desktop"
    assert result.result["analysis"]["type"] == "task"
    assert result.result["plan"]["schema_version"] == "v0.5"
    assert result.result["execution"]["status"] == "succeeded"
    assert result.result["execution"]["acceptance_status"] == "passed"
    assert result.overall_status == "completed"
    assert result.result["execution"]["step_results"][0]["status"] == "succeeded"
    assert result.result["execution"]["step_results"][0]["adapter"] == "apps.registry"
    assert result.result["execution"]["step_results"][0]["capability_id"] == "app.open"


def test_normal_ask_uses_unique_run_ids_for_same_utterance() -> None:
    broker = CapabilityBroker(
        intent_broker=RuleIntentBroker(),
        apps=FakeApps(),
        portal=FakePortal(),
        audit=AuditLog(make_audit_path("unique-run-id")),
        reviews=ReviewStore(make_review_path("unique-run-id")),
    )

    first = broker.handle(CommandRequest("open browser"))
    second = broker.handle(CommandRequest("open browser"))

    assert first.result["run"]["run_id"] != second.result["run"]["run_id"]


def test_task_planning_uses_configured_intent_broker_for_normal_requests() -> None:
    broker = CapabilityBroker(
        intent_broker=StaticIntentBroker(Intent(action="window.list", reason="semantic route selection")),
        audit=AuditLog(make_audit_path("semantic-broker-plan")),
        reviews=ReviewStore(make_review_path("semantic-broker-plan")),
    )

    result = broker.handle(CommandRequest("show the active surfaces", dry_run=True))

    assert result.status == "dry_run"
    assert tuple(result.result["analysis"]["domains"]) == ("window_management",)
    assert result.result["plan"]["selected_route_id"] == "window_list_route"
    assert result.result["plan"]["steps"][0]["action"] == "window.list"


def test_normal_ask_rejects_named_web_targets_when_no_official_resolution_path_exists() -> None:
    broker = CapabilityBroker(
        intent_broker=RuleIntentBroker(),
        portal=FakePortal(),
        audit=AuditLog(make_audit_path("named-web-browser-plan")),
        reviews=ReviewStore(make_review_path("named-web-browser-plan")),
        verifier_harness=VerifierHarness({"browser_search_route_completed": {"query": "\u767e\u5ea6\u5b98\u7f51"}}),
    )

    result = broker.handle(CommandRequest("\u6253\u5f00\u767e\u5ea6\u5b98\u7f51"))

    assert result.status == "rejected"
    assert result.result["assistant_intent"]["objective_kind"] == "open_named_website"
    assert [attempt["selected_route_id"] for attempt in result.result["attempts"]] == [
        "browser_named_direct_open_route",
        "browser_search_followup_route",
    ]
    assert result.result["attempts"][0]["failure"]["failure_class"] == "semantic_mismatch"
    assert result.result["attempts"][1]["failure"]["failure_class"] == "semantic_mismatch"


def test_task_plan_loop_replans_semantic_mismatch_into_browser_route(monkeypatch) -> None:
    broker = CapabilityBroker(
        intent_broker=RuleIntentBroker(),
        apps=FakeApps(),
        portal=FakePortal(),
        audit=AuditLog(make_audit_path("semantic-replan-browser")),
        reviews=ReviewStore(make_review_path("semantic-replan-browser")),
        verifier_harness=VerifierHarness({"browser_search_route_completed": {"query": "\u767e\u5ea6\u5b98\u7f51"}}),
    )

    apps_plan = TaskPlan(
        schema_version="v0.5",
        plan_id="plan_apps_baidu",
        utterance="\u6253\u5f00\u767e\u5ea6\u5b98\u7f51",
        display=DisplayFields(goal="open an application"),
        selected_route_id="apps_open_route",
        routes=(TaskRoute(id="apps_open_route", score=1.0, domain_id="apps", required_capabilities=("app.open",)),),
        steps=(
            TaskStep(
                id="open_app",
                action="app.open",
                capability_id="app.open",
                target={"name": "\u767e\u5ea6\u5b98\u7f51"},
                expected_state=ExpectedState(kind="app_opened_or_focused", fields={"app": "\u767e\u5ea6\u5b98\u7f51"}),
                preconditions=(StepPrecondition(kind="capability_available", capability_id="app.open"),),
                provenance=StepProvenance(source_span_id="span_1", planner="test"),
            ),
        ),
        provenance={"planner": "test"},
    )
    browser_plan = TaskPlan(
        schema_version="v0.5",
        plan_id="plan_browser_baidu",
        utterance="\u6253\u5f00\u767e\u5ea6\u5b98\u7f51",
        display=DisplayFields(goal="search in the browser"),
        selected_route_id="browser_search_web_route",
        routes=(TaskRoute(id="browser_search_web_route", score=1.0, domain_id="browser", required_capabilities=("browser.search_web",), default_verifier_ids=("browser_search_route_completed",)),),
        steps=(
            TaskStep(
                id="search_baidu",
                action="browser.search_web",
                capability_id="browser.search_web",
                target={"query": "\u767e\u5ea6\u5b98\u7f51"},
                expected_state=ExpectedState(kind="search_results_available", fields={"query": "\u767e\u5ea6\u5b98\u7f51"}),
                preconditions=(StepPrecondition(kind="capability_available", capability_id="browser.search_web"),),
                provenance=StepProvenance(source_span_id="span_1", planner="test"),
            ),
        ),
        provenance={"planner": "test"},
    )

    initial_understanding = make_understanding(
        UtteranceAnalysis(
            utterance="\u6253\u5f00\u767e\u5ea6\u5b98\u7f51",
            type="task",
            confidence=1.0,
            domains=("apps",),
            explanation="misclassified as app.open",
            task_spans=(TaskSpan(id="span_1", text="\u6253\u5f00\u767e\u5ea6\u5b98\u7f51", start=0, end=6, domain="apps", confidence=1.0),),
        )
    )

    plans = [
        PlanningArtifacts(
            understanding=initial_understanding,
            analysis=UtteranceAnalysis(
                utterance="\u6253\u5f00\u767e\u5ea6\u5b98\u7f51",
                type="task",
                confidence=1.0,
                domains=("apps",),
                explanation="misclassified as app.open",
                task_spans=(TaskSpan(id="span_1", text="\u6253\u5f00\u767e\u5ea6\u5b98\u7f51", start=0, end=6, domain="apps", confidence=1.0),),
            ),
            goal_synthesis=None,
            plan=apps_plan,
            candidates=(apps_plan,),
        ),
        PlanningArtifacts(
            understanding=initial_understanding,
            analysis=UtteranceAnalysis(
                utterance="\u6253\u5f00\u767e\u5ea6\u5b98\u7f51",
                type="task",
                confidence=1.0,
                domains=("browser",),
                explanation="replanned into browser route",
                task_spans=(TaskSpan(id="span_1", text="\u6253\u5f00\u767e\u5ea6\u5b98\u7f51", start=0, end=6, domain="browser", confidence=1.0),),
            ),
            goal_synthesis=None,
            plan=browser_plan,
            candidates=(browser_plan,),
        ),
    ]

    def fake_plan_turn(*args, **kwargs):
        return plans.pop(0)

    monkeypatch.setattr("vibeos.broker.plan_turn", fake_plan_turn)

    result = broker.handle(CommandRequest("\u6253\u5f00\u767e\u5ea6\u5b98\u7f51"))

    assert result.status == "executed"
    assert result.overall_status == "completed"
    assert result.result["plan"]["selected_route_id"] == "browser_search_web_route"
    assert len(result.result["attempts"]) == 2
    assert result.result["attempts"][0]["understanding_id"] == result.result["understanding"]["primary_understanding_id"]
    assert result.result["attempts"][1]["understanding_id"] == result.result["understanding"]["primary_understanding_id"]
    assert result.result["understanding"]["artifact_role"] == "refinement"
    assert result.result["understanding"]["source_understanding_id"] == result.result["attempts"][0]["understanding_id"]
    assert result.result["understanding_refinement"]["previous_understanding_id"] == result.result["attempts"][0]["understanding_id"]
    assert result.result["understanding_refinement"]["refined_understanding_id"] == result.result["understanding"]["understanding_id"]
    assert "domains" in result.result["understanding_refinement"]["changed_fields"]
    assert result.result["attempts"][0]["failure"]["failure_class"] == "semantic_mismatch"
    assert result.result["attempts"][0]["replan_decision"]["action"] == "replan_with_constraints"
    assert result.result["attempts"][0]["replan_decision"]["replan_decision_id"].startswith("rpdec_")
    assert result.result["attempts"][1]["selected_route_id"] == "browser_search_web_route"


def test_task_plan_loop_preserves_attempt_history_when_replan_exhausts_candidates(monkeypatch) -> None:
    broker = CapabilityBroker(
        intent_broker=RuleIntentBroker(),
        apps=FakeApps(),
        portal=FakePortal(),
        audit=AuditLog(make_audit_path("semantic-replan-exhausted")),
        reviews=ReviewStore(make_review_path("semantic-replan-exhausted")),
    )

    apps_plan = TaskPlan(
        schema_version="v0.5",
        plan_id="plan_apps_exhausted",
        utterance="\u6253\u5f00\u767e\u5ea6\u5b98\u7f51",
        display=DisplayFields(goal="open an application"),
        selected_route_id="apps_open_route",
        routes=(TaskRoute(id="apps_open_route", score=1.0, domain_id="apps", required_capabilities=("app.open",)),),
        steps=(
            TaskStep(
                id="open_app",
                action="app.open",
                capability_id="app.open",
                target={"name": "\u767e\u5ea6\u5b98\u7f51"},
                expected_state=ExpectedState(kind="app_opened_or_focused", fields={"app": "\u767e\u5ea6\u5b98\u7f51"}),
                preconditions=(StepPrecondition(kind="capability_available", capability_id="app.open"),),
                provenance=StepProvenance(source_span_id="span_1", planner="test"),
            ),
        ),
        provenance={"planner": "test"},
    )

    plans = [
        PlanningArtifacts(
            understanding=make_understanding(
                UtteranceAnalysis(
                    utterance="\u6253\u5f00\u767e\u5ea6\u5b98\u7f51",
                    type="task",
                    confidence=1.0,
                    domains=("apps",),
                    explanation="misclassified as app.open",
                    task_spans=(TaskSpan(id="span_1", text="\u6253\u5f00\u767e\u5ea6\u5b98\u7f51", start=0, end=6, domain="apps", confidence=1.0),),
                )
            ),
            analysis=UtteranceAnalysis(
                utterance="\u6253\u5f00\u767e\u5ea6\u5b98\u7f51",
                type="task",
                confidence=1.0,
                domains=("apps",),
                explanation="misclassified as app.open",
                task_spans=(TaskSpan(id="span_1", text="\u6253\u5f00\u767e\u5ea6\u5b98\u7f51", start=0, end=6, domain="apps", confidence=1.0),),
            ),
            goal_synthesis=None,
            plan=apps_plan,
            candidates=(apps_plan,),
        ),
        PlanningArtifacts(
            understanding=make_understanding(
                UtteranceAnalysis(
                    utterance="\u6253\u5f00\u767e\u5ea6\u5b98\u7f51",
                    type="rejected",
                    confidence=1.0,
                    domains=(),
                    explanation="no replacement route available",
                    task_spans=(),
                )
            ),
            analysis=UtteranceAnalysis(
                utterance="\u6253\u5f00\u767e\u5ea6\u5b98\u7f51",
                type="rejected",
                confidence=1.0,
                domains=(),
                explanation="no replacement route available",
                task_spans=(),
            ),
            goal_synthesis=None,
            plan=None,
            candidates=(),
        ),
    ]

    def fake_plan_turn(*args, **kwargs):
        return plans.pop(0)

    monkeypatch.setattr("vibeos.broker.plan_turn", fake_plan_turn)

    result = broker.handle(CommandRequest("\u6253\u5f00\u767e\u5ea6\u5b98\u7f51"))

    assert result.status == "rejected"
    assert result.result["run"]["final_outcome"] == "failed"
    assert len(result.result["attempts"]) == 1
    assert result.result["attempts"][0]["failure"]["failure_class"] == "semantic_mismatch"


def test_task_plan_loop_emits_understanding_supersession_when_replan_changes_type(monkeypatch) -> None:
    broker = CapabilityBroker(
        intent_broker=RuleIntentBroker(),
        apps=FakeApps(),
        portal=FakePortal(),
        audit=AuditLog(make_audit_path("semantic-replan-supersession")),
        reviews=ReviewStore(make_review_path("semantic-replan-supersession")),
    )

    apps_plan = TaskPlan(
        schema_version="v0.5",
        plan_id="plan_apps_supersession",
        utterance="open that site we discussed yesterday",
        display=DisplayFields(goal="open an application"),
        selected_route_id="apps_open_route",
        routes=(TaskRoute(id="apps_open_route", score=1.0, domain_id="apps", required_capabilities=("app.open",)),),
        steps=(
            TaskStep(
                id="open_app",
                action="app.open",
                capability_id="app.open",
                target={"name": "that site we discussed yesterday"},
                expected_state=ExpectedState(kind="app_opened_or_focused", fields={"app": "that site we discussed yesterday"}),
                preconditions=(StepPrecondition(kind="capability_available", capability_id="app.open"),),
                provenance=StepProvenance(source_span_id="span_1", planner="test"),
            ),
        ),
        provenance={"planner": "test"},
    )

    initial_understanding = make_understanding(
        UtteranceAnalysis(
            utterance="open that site we discussed yesterday",
            type="task",
            confidence=1.0,
            domains=("apps",),
            explanation="misclassified as an app request",
            task_spans=(TaskSpan(id="span_1", text="open that site we discussed yesterday", start=0, end=37, domain="apps", confidence=1.0),),
        )
    )

    plans = [
        PlanningArtifacts(
            understanding=initial_understanding,
            analysis=initial_understanding.analysis,
            goal_synthesis=None,
            plan=apps_plan,
            candidates=(apps_plan,),
        ),
        PlanningArtifacts(
            understanding=initial_understanding,
            analysis=UtteranceAnalysis(
                utterance="open that site we discussed yesterday",
                type="clarification",
                confidence=0.95,
                domains=("browser",),
                explanation="the referenced site is ambiguous without a concrete target",
                task_spans=(),
                chat_response="Which exact site should I open?",
            ),
            goal_synthesis=None,
            plan=None,
            candidates=(),
        ),
    ]

    def fake_plan_turn(*args, **kwargs):
        return plans.pop(0)

    monkeypatch.setattr("vibeos.broker.plan_turn", fake_plan_turn)

    result = broker.handle(CommandRequest("open that site we discussed yesterday"))

    assert result.status == "ambiguous"
    assert result.overall_status == "needs_user_input"
    assert len(result.result["attempts"]) == 1
    assert result.result["understanding"]["artifact_role"] == "supersession"
    assert result.result["understanding"]["primary_understanding_id"] == result.result["attempts"][0]["understanding_id"]
    assert result.result["understanding"]["source_understanding_id"] == result.result["attempts"][0]["understanding_id"]
    assert result.result["understanding_supersession"]["previous_understanding_id"] == result.result["attempts"][0]["understanding_id"]
    assert result.result["understanding_supersession"]["superseding_understanding_id"] == result.result["understanding"]["understanding_id"]
    assert "type" in result.result["understanding_supersession"]["changed_fields"]


def test_task_plan_loop_passes_refined_understanding_into_replanned_plan_turn(monkeypatch) -> None:
    monkeypatch.setenv("VIBEOS_STATE_DIR", str(Path(".vibeos") / "test-state" / f"semantic-replan-transition-provider-{uuid4().hex}"))
    broker = CapabilityBroker(
        intent_broker=RuleIntentBroker(),
        apps=FakeApps(),
        portal=ObservedPortal(),
        audit=AuditLog(make_audit_path("semantic-replan-transition-provider")),
        reviews=ReviewStore(make_review_path("semantic-replan-transition-provider")),
        understanding_transition_provider=FixedUnderstandingTransitionProvider(),
    )

    apps_plan = TaskPlan(
        schema_version="v0.5",
        plan_id="plan_apps_transition_provider",
        utterance="打开百度官网",
        display=DisplayFields(goal="open an application"),
        selected_route_id="apps_open_route",
        routes=(TaskRoute(id="apps_open_route", score=1.0, domain_id="apps", required_capabilities=("app.open",)),),
        steps=(
            TaskStep(
                id="open_app",
                action="app.open",
                capability_id="app.open",
                target={"name": "百度官网"},
                expected_state=ExpectedState(kind="app_opened_or_focused", fields={"app": "百度官网"}),
                preconditions=(StepPrecondition(kind="capability_available", capability_id="app.open"),),
                provenance=StepProvenance(source_span_id="span_1", planner="test"),
            ),
        ),
        provenance={"planner": "test"},
    )
    browser_plan = TaskPlan(
        schema_version="v0.5",
        plan_id="plan_browser_transition_provider",
        utterance="打开百度官网",
        display=DisplayFields(goal="search Baidu in the browser"),
        selected_route_id="browser_search_web_route",
        routes=(TaskRoute(id="browser_search_web_route", score=1.0, domain_id="browser", required_capabilities=("browser.search_web",), default_verifier_ids=("browser_search_route_completed",)),),
        steps=(
            TaskStep(
                id="search_baidu",
                action="browser.search_web",
                capability_id="browser.search_web",
                target={"query": "百度官网"},
                expected_state=ExpectedState(kind="search_results_available", fields={"query": "百度官网"}),
                preconditions=(StepPrecondition(kind="capability_available", capability_id="browser.search_web"),),
                provenance=StepProvenance(source_span_id="span_1", planner="test"),
            ),
        ),
        provenance={"planner": "test"},
    )
    initial_understanding = make_understanding(
        UtteranceAnalysis(
            utterance="打开百度官网",
            type="task",
            confidence=1.0,
            domains=("apps",),
            explanation="misclassified as app.open",
            task_spans=(TaskSpan(id="span_1", text="打开百度官网", start=0, end=6, domain="apps", confidence=1.0),),
        )
    )
    seen_understandings: list[UnderstandingArtifact | None] = []

    def fake_plan_turn(*args, **kwargs):
        seen_understandings.append(kwargs.get("understanding"))
        if len(seen_understandings) == 1:
            return PlanningArtifacts(
                understanding=initial_understanding,
                analysis=initial_understanding.analysis,
                goal_synthesis=None,
                plan=apps_plan,
                candidates=(apps_plan,),
            )
        replanned_understanding = kwargs.get("understanding")
        assert replanned_understanding is not None
        assert replanned_understanding.artifact_role == "refinement"
        assert replanned_understanding.analysis.domains == ("browser",)
        return PlanningArtifacts(
            understanding=replanned_understanding,
            analysis=replanned_understanding.analysis,
            goal_synthesis=None,
            plan=browser_plan,
            candidates=(browser_plan,),
        )

    monkeypatch.setattr("vibeos.broker.plan_turn", fake_plan_turn)

    result = broker.handle(CommandRequest("打开百度官网"))

    assert result.status == "executed"
    assert result.result["understanding"]["artifact_role"] == "refinement"
    assert result.result["understanding"]["analysis_provider_name"] == initial_understanding.analysis_provider_name
    assert len(seen_understandings) == 2
    trace_run_id = result.trace_run_id
    assert trace_run_id is not None
    model_io = TaskTraceStore().model_io(trace_run_id)
    assert any(item["provider"] == "test_understanding_transition" for item in model_io)


def test_normal_ask_returns_plan_review_for_mixed_clipboard_request() -> None:
    broker = CapabilityBroker(
        intent_broker=RuleIntentBroker(),
        apps=FakeApps(),
        portal=FakePortal(),
        audit=AuditLog(make_audit_path("normal-ask-mixed-review")),
        reviews=ReviewStore(make_review_path("normal-ask-mixed-review")),
    )

    result = broker.handle(CommandRequest("explain clipboard permissions and then copy hello to clipboard"))

    assert result.status == "review_required"
    assert result.review_id
    assert result.result["analysis"]["type"] == "mixed"
    assert result.result["plan_review"]["status"] == "review_required"
    assert result.result["goal_runtime"]["status"] == "needs_review"
    assert result.result["run_ledger"]["terminal_outcome"]["status"] == "needs_review"


def test_approved_clipboard_plan_continues_same_v06_goal_runtime() -> None:
    broker = CapabilityBroker(
        intent_broker=RuleIntentBroker(),
        clipboard=FakeClipboard(),
        audit=AuditLog(make_audit_path("clipboard-v06-approve")),
        reviews=ReviewStore(make_review_path("clipboard-v06-approve")),
    )

    pending = broker.handle(CommandRequest("clipboard hello"))
    approved = broker.handle(CommandRequest("", review_id=pending.review_id, approve=True))

    assert pending.status == "review_required"
    assert approved.status == "executed"
    assert pending.result["goal_runtime"]["goal_id"] == approved.result["goal_runtime"]["goal_id"]
    assert approved.result["goal_runtime"]["status"] == "completed"
    assert approved.result["selected_strategy_id"] == "strategy_clipboard_write_route"
    assert approved.result["run_ledger"]["terminal_outcome"]["status"] == "completed"
    assert approved.result["step_results"][0]["adapter"] == "/usr/bin/wl-copy"


def test_approved_clipboard_plan_reports_typed_adapter_timeout() -> None:
    broker = CapabilityBroker(
        intent_broker=RuleIntentBroker(),
        clipboard=TimeoutClipboard(),
        audit=AuditLog(make_audit_path("clipboard-timeout-plan")),
        reviews=ReviewStore(make_review_path("clipboard-timeout-plan")),
    )

    pending = broker.handle(CommandRequest("clipboard hello"))
    approved = broker.handle(CommandRequest("", review_id=pending.review_id, approve=True))
    step = approved.result["step_results"][0]

    assert pending.status == "review_required"
    assert approved.status == "failed"
    assert step["adapter"] == "/usr/bin/wl-copy"
    assert step["adapter_status"] == "timeout"
    assert step["error_code"] == "adapter_timeout"
    assert step["diagnostics"]["adapter_result_status"] == "timeout"


def test_window_close_request_enters_v06_needs_review_state() -> None:
    broker = CapabilityBroker(
        intent_broker=RuleIntentBroker(),
        windows=FakeWindows(),
        audit=AuditLog(make_audit_path("window-close-v06-review")),
        reviews=ReviewStore(make_review_path("window-close-v06-review")),
    )

    result = broker.handle(CommandRequest("close firefox"))

    assert result.status == "review_required"
    assert result.review_id
    assert result.result["plan_review"]["status"] == "review_required"
    assert result.result["goal_runtime"]["status"] == "needs_review"
    assert result.result["selected_strategy_id"] == "strategy_window_close_route"
    assert result.result["run_ledger"]["terminal_outcome"]["status"] == "needs_review"


def test_approved_window_close_plan_continues_same_v06_goal_runtime() -> None:
    broker = CapabilityBroker(
        intent_broker=RuleIntentBroker(),
        windows=FakeWindows(),
        audit=AuditLog(make_audit_path("window-close-v06-approve")),
        reviews=ReviewStore(make_review_path("window-close-v06-approve")),
    )

    pending = broker.handle(CommandRequest("close firefox"))
    approved = broker.handle(CommandRequest("", review_id=pending.review_id, approve=True))

    assert pending.status == "review_required"
    assert approved.status == "executed"
    assert pending.result["goal_runtime"]["goal_id"] == approved.result["goal_runtime"]["goal_id"]
    assert approved.result["goal_runtime"]["status"] == "completed"
    assert approved.result["selected_strategy_id"] == "strategy_window_close_route"
    assert approved.result["run_ledger"]["terminal_outcome"]["status"] == "completed"
    assert approved.result["step_results"][0]["adapter"] == "windows.registry"
    assert approved.result["step_results"][0]["capability_id"] == "window.close"
    assert approved.result["step_results"][0]["result"]["status"] == "closed"


def test_normal_ask_notification_plan_reports_typed_adapter_metadata() -> None:
    broker = CapabilityBroker(
        intent_broker=RuleIntentBroker(),
        notifications=FakeNotifications(),
        audit=AuditLog(make_audit_path("notification-plan")),
        reviews=ReviewStore(make_review_path("notification-plan")),
    )

    result = broker.handle(CommandRequest("notify hello"))
    step = result.result["execution"]["step_results"][0]

    assert result.status == "executed"
    assert step["adapter"] == "notifications.send"
    assert step["capability_id"] == "notification.send"
    assert step["adapter_status"] == "succeeded"
    assert step["diagnostics"]["title"] == "VibeOS"


def media_span() -> TaskSpan:
    return TaskSpan(id="span_1", text="play baby", start=0, end=9, domain="media", confidence=0.9)


def make_review_path(name: str) -> Path:
    return Path(".vibeos") / f"test-{name}-{uuid4().hex}.jsonl"


def make_audit_path(name: str) -> Path:
    return Path(".vibeos") / f"audit-{name}-{uuid4().hex}.jsonl"


def make_understanding(analysis: UtteranceAnalysis) -> UnderstandingArtifact:
    return UnderstandingArtifact(
        understanding_id=f"und_{uuid4().hex[:8]}",
        utterance=analysis.utterance,
        analysis=analysis,
    )
