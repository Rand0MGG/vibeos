import pytest

from vibeos.clarification import ClarificationDecision, ClarificationProvider
from vibeos.candidate_selection import CandidateSelectionDecision, CandidateSelectionProvider
from vibeos.intent import IntentBroker, RuleIntentBroker
from vibeos.models import Intent
from vibeos.domain_registry import default_domain_registry
from vibeos.domain_router import route_domains
from vibeos.nlu import analyze_utterance
from vibeos.planner import plan_payload, plan_turn, plan_utterance
from vibeos.goal_synthesizer import GoalSynthesisProvider, OpenAICompatibleGoalSynthesisProvider, build_goal_synthesis_boundary_hint
from vibeos.provider_client import OpenAICompatibleProviderConfig, ProviderJsonObjectResponse
from vibeos.task_models import TaskSpan, UtteranceAnalysis
from vibeos.understanding import (
    CapturingIntentBroker,
    OpenAICompatibleUnderstandingAnalysisProvider,
    UnderstandingAnalysisDecision,
    UnderstandingAnalysisProvider,
    create_primary_understanding,
    default_understanding_host_hint,
    reconcile_understanding_transition,
    validated_understanding_from_payload,
)
from vibeos.verifiers import default_verifier_registry


class StaticIntentBroker(IntentBroker):
    def __init__(self, intent: Intent) -> None:
        self.intent = intent

    def parse(self, utterance: str) -> Intent:
        return self.intent


class CountingIntentBroker(IntentBroker):
    def __init__(self, intent: Intent) -> None:
        self.intent = intent
        self.call_count = 0

    def parse(self, utterance: str) -> Intent:
        self.call_count += 1
        return self.intent


class StaticSelectionProvider(CandidateSelectionProvider):
    def __init__(self, *, selected_candidate_id: str | None, action: str = "select") -> None:
        self.selected_candidate_id = selected_candidate_id
        self.action = action
        self.provider_name = "test_selector"
        self.model_name = "deterministic-test"

    def decide(self, *, understanding, candidate_set) -> CandidateSelectionDecision:
        return CandidateSelectionDecision(
            route_decision_id="rdec_test",
            candidate_set_id=candidate_set.candidate_set_id,
            understanding_id=understanding.understanding_id,
            action=self.action,
            selected_candidate_id=self.selected_candidate_id,
            reason="test override",
            provider_name=self.provider_name,
            model_name=self.model_name,
        )


class StaticClarificationProvider(ClarificationProvider):
    provider_name = "test_clarifier"
    model_name = "deterministic-test"

    def generate(self, *, utterance: str, analysis) -> ClarificationDecision:
        return ClarificationDecision(
            clarification_question_id="cqid_test",
            question="Which exact site should I open?",
            reason="test override",
            provider_name=self.provider_name,
            model_name=self.model_name,
        )


class EmptyDomainGoalSynthesisProvider(GoalSynthesisProvider):
    provider_name = "test_goal_synthesizer"
    model_name = "deterministic-test"

    def synthesize(self, utterance: str, analysis: UtteranceAnalysis) -> dict[str, object]:
        self._last_parse_valid = True
        self._last_fallback_used = False
        self._last_error = None
        self._last_raw_output = "{}"
        return {
            "status": "ready",
            "goal_type": "browser_open_url",
            "candidate_domain_ids": [],
            "required_capability_ids": ["browser.open_url"],
            "missing_capability_ids": [],
            "clarification_questions": [],
            "constraints": [],
            "fallback_hints": [],
            "assumptions": [],
            "assistant_intent": None,
            "subgoals": [
                {
                    "subgoal_id": "subgoal_1",
                    "text": utterance,
                    "goal_type": "browser_open_url",
                    "candidate_domain_ids": [],
                    "required_capability_ids": ["browser.open_url"],
                }
            ],
            "message": "test override",
        }


class MismatchedAppOpenGoalSynthesisProvider(GoalSynthesisProvider):
    provider_name = "test_goal_synthesizer"
    model_name = "deterministic-test"

    def synthesize(self, utterance: str, analysis: UtteranceAnalysis) -> dict[str, object]:
        self._last_parse_valid = True
        self._last_fallback_used = False
        self._last_error = None
        self._last_raw_output = "{}"
        return {
            "status": "ready",
            "goal_type": "app_open",
            "candidate_domain_ids": ["browser"],
            "required_capability_ids": ["app.open"],
            "missing_capability_ids": [],
            "clarification_questions": [],
            "constraints": [],
            "fallback_hints": [],
            "assumptions": [],
            "assistant_intent": {
                "objective_kind": "open_application",
                "target": {
                    "entity_type": "application",
                    "display_name": "browser",
                    "app_name": "browser",
                },
                "completion": {
                    "kind": "application_state",
                    "success_signal": "the requested application is opened or focused",
                },
                "interaction_hints": ["native-open"],
                "preferred_domains": ["apps"],
            },
            "subgoals": [],
            "message": "test override",
        }


class StaticUnderstandingProvider(UnderstandingAnalysisProvider):
    provider_name = "test_understanding"
    model_name = "deterministic-test"

    def analyze(self, *, utterance: str, broker) -> UnderstandingAnalysisDecision:
        return UnderstandingAnalysisDecision(
            analysis=UtteranceAnalysis(
                utterance=utterance,
                type="clarification",
                confidence=0.93,
                domains=("browser",),
                explanation="test override requested clarification",
                task_spans=(),
                provenance=None,
                chat_response="Which site should I open exactly?",
            ),
            provider_name=self.provider_name,
            model_name=self.model_name,
        )


class BrowserTaskUnderstandingProvider(UnderstandingAnalysisProvider):
    provider_name = "test_understanding"
    model_name = "deterministic-test"

    def analyze(self, *, utterance: str, broker) -> UnderstandingAnalysisDecision:
        return UnderstandingAnalysisDecision(
            analysis=UtteranceAnalysis(
                utterance=utterance,
                type="task",
                confidence=0.93,
                domains=("browser",),
                explanation="test override classified the utterance as a browser task",
                task_spans=(
                    TaskSpan(
                        id="span_1",
                        text=utterance,
                        start=0,
                        end=len(utterance),
                        domain="browser",
                        confidence=0.93,
                    ),
                ),
                provenance=None,
                chat_response=None,
            ),
            provider_name=self.provider_name,
            model_name=self.model_name,
        )


class FailingUnderstandingFallback(UnderstandingAnalysisProvider):
    def analyze(self, *, utterance: str, broker) -> UnderstandingAnalysisDecision:
        raise AssertionError("deterministic understanding fallback should not run before a successful provider response")


def test_analyze_utterance_classifies_chat() -> None:
    analysis = analyze_utterance("how should VibeOS v0.3 be designed?")
    assert analysis.type == "chat"


def test_analyze_utterance_classifies_open_browser_as_task() -> None:
    analysis = analyze_utterance("open browser")
    assert analysis.type == "task"


def test_analyze_utterance_prefers_provider_intent_for_named_site_resolution() -> None:
    analysis = analyze_utterance(
        "帮我打开百度官网",
        intent_broker=StaticIntentBroker(
            Intent(
                action="browser.open_url",
                target={"url": "https://www.baidu.com"},
                reason="provider resolved the named site to a concrete URL",
            )
        ),
    )

    assert analysis.type == "task"
    assert analysis.domains == ("browser",)
    assert analysis.provenance is not None
    assert analysis.provenance.parser == "provider_capability_analysis"


def test_analyze_utterance_classifies_play_baby_as_task() -> None:
    analysis = analyze_utterance("play baby")
    assert analysis.type == "task"


def test_analyze_utterance_classifies_app_history_search_as_app_interaction_task() -> None:
    analysis = analyze_utterance("search chat history in WeChat for Alice", intent_broker=RuleIntentBroker())

    assert analysis.type == "task"
    assert analysis.domains == ("app_interaction",)
    assert analysis.task_spans[0].domain == "app_interaction"


def test_analyze_utterance_classifies_delete_downloads_as_rejected() -> None:
    analysis = analyze_utterance("delete downloads")
    assert analysis.type == "rejected"


def test_analyze_utterance_classifies_clarification() -> None:
    analysis = analyze_utterance("play")
    assert analysis.type == "clarification"
    assert analysis.chat_response == "What would you like to play?"


def test_analyze_utterance_classifies_mixed_and_extracts_task_span() -> None:
    analysis = analyze_utterance("explain clipboard permissions and then copy hello to clipboard")
    assert analysis.type == "mixed"
    assert analysis.chat_response == "explain clipboard permissions"
    assert analysis.task_spans[0].text == "copy hello to clipboard"


def test_plan_payload_for_mixed_request_returns_clipboard_task_plan() -> None:
    payload = plan_payload("explain clipboard permissions and then copy hello to clipboard")
    assert payload["status"] == "validated"
    assert payload["analysis"]["type"] == "mixed"
    assert payload["plan"]["steps"][0]["action"] == "clipboard.write"
    assert payload["plan"]["steps"][0]["target"]["text"] == "hello"


def test_analyze_utterance_mixed_request_prefers_structured_intent_for_followup_domain() -> None:
    analysis = analyze_utterance(
        "explain the target and then help me查一下 OpenAI 文档",
        intent_broker=StaticIntentBroker(
            Intent(
                action="browser.search_web",
                target={"query": "OpenAI 文档"},
                reason="provider resolved the follow-up clause into a browser search",
            )
        ),
    )

    assert analysis.type == "mixed"
    assert analysis.chat_response == "explain the target"
    assert analysis.domains == ("browser",)
    assert analysis.task_spans[0].domain == "browser"


def test_plan_payload_for_clipboard_variants_returns_task_plan() -> None:
    variants = (
        "clipboard VibeOS evidence",
        "copy VibeOS evidence",
        "copy to clipboard VibeOS evidence",
        "write VibeOS evidence to clipboard",
    )

    for utterance in variants:
        payload = plan_payload(utterance)
        assert payload["status"] == "validated"
        assert payload["plan"]["steps"][0]["action"] == "clipboard.write"
        assert payload["plan"]["steps"][0]["target"]["text"] == "VibeOS evidence"


def test_plan_turn_uses_analysis_domains_when_goal_synthesis_omits_candidate_domains() -> None:
    planning = plan_turn(
        "open https://example.com",
        goal_synthesis_provider=EmptyDomainGoalSynthesisProvider(),
    )

    assert planning.plan is not None
    assert planning.plan.selected_route_id == "browser_open_url_route"
    assert planning.plan.provenance["planner"] == "v0.5_domain_planner"
    assert all(not candidate.selected_route_id.startswith("legacy_") for candidate in planning.candidates)


def test_plan_turn_prefers_intent_aligned_domain_when_goal_synthesis_domains_drift() -> None:
    planning = plan_turn(
        "open browser",
        intent_broker=StaticIntentBroker(
            Intent(
                action="app.open",
                target={"name": "browser"},
                reason="test broker resolved the app-open request",
            )
        ),
        goal_synthesis_provider=MismatchedAppOpenGoalSynthesisProvider(),
    )

    assert planning.goal_synthesis is not None
    assert planning.goal_synthesis.goal_spec is not None
    assert planning.goal_synthesis.goal_spec.candidate_domain_ids == ("apps", "browser")
    assert planning.plan is not None
    assert planning.plan.selected_route_id == "apps_open_route"
    assert planning.plan.steps[0].action == "app.open"


def test_plan_payload_for_media_request_selects_browser_route_when_media_capabilities_absent() -> None:
    payload = plan_payload("play baby")
    assert payload["status"] == "validated"
    assert payload["analysis"]["type"] == "task"
    assert payload["plan"]["selected_route_id"] == "browser_music_search_route"
    assert len(payload["plan"]["steps"]) == 1
    assert payload["plan"]["steps"][0]["action"] == "browser.open_site_search"
    assert payload["plan"]["steps"][0]["target"]["site"] == "youtube.com"
    assert tuple(payload["plan"]["steps"][0]["depends_on"]) == ()
    assert len(payload["candidates"]) == 2
    assert payload["candidates"][0]["score"] >= payload["candidates"][1]["score"]
    assert tuple(payload["domain_routing"]["active_domain_ids"]) == ("media", "browser")
    assert set(payload["capability_exposure"]["exposed_route_ids"]) == {
        "browser_open_url_route",
        "browser_search_web_route",
        "browser_site_search_route",
        "browser_music_search_route",
        "media_search_route",
        "media_play_route",
        "media_pause_route",
    }


def test_plan_utterance_prefers_music_route_when_media_capabilities_exist() -> None:
    analysis, plan, candidates = plan_utterance(
        "play baby",
        capability_context={"app.open", "media.search", "media.play", "browser.open_site_search"},
    )

    assert analysis.type == "task"
    assert plan is not None
    assert plan.selected_route_id == "media_play_route"
    assert candidates[0].selected_route_id in {"media_play_route", "browser_music_search_route"}


def test_plan_utterance_returns_no_plan_when_no_route_is_satisfiable() -> None:
    analysis, plan, candidates = plan_utterance("play baby", capability_context=set())

    assert analysis.type == "task"
    assert plan is None
    assert len(candidates) == 2


def test_plan_payload_returns_rejected_when_no_route_is_satisfiable() -> None:
    payload = plan_payload("play baby", capability_context=set())

    assert payload["status"] == "blocked"
    assert payload["plan"] is None
    assert payload["overall_status"] == "blocked"
    assert payload["message"] == "no candidate satisfies the current capability boundary"
    assert len(payload["candidates"]) == 2


def test_plan_utterance_returns_no_plan_for_clarification() -> None:
    analysis, plan, candidates = plan_utterance("play")
    assert analysis.type == "clarification"
    assert plan is None
    assert candidates == []


def test_plan_payload_exposes_browser_domain_trace_for_open_url() -> None:
    payload = plan_payload("open https://example.com")

    assert payload["status"] == "validated"
    assert payload["plan"]["selected_route_id"] == "browser_open_url_route"
    assert payload["plan"]["steps"][0]["action"] == "browser.open_url"
    assert tuple(payload["domain_routing"]["active_domain_ids"]) == ("browser",)
    assert "media" in payload["capability_exposure"]["hidden_domain_ids"]
    assert payload["trace"]["selected_route"]["id"] == "browser_open_url_route"


def test_plan_payload_routes_named_web_targets_to_browser_search() -> None:
    payload = plan_payload("\u6253\u5f00\u767e\u5ea6\u5b98\u7f51", intent_broker=RuleIntentBroker())

    assert payload["status"] == "validated"
    assert tuple(payload["analysis"]["domains"]) == ("browser",)
    assert payload["plan"]["selected_route_id"] == "browser_named_direct_open_route"
    assert payload["plan"]["steps"][0]["action"] == "browser.open_named_target"
    assert payload["plan"]["steps"][0]["target"] == {
        "name": "\u767e\u5ea6\u5b98\u7f51",
        "resolution_mode": "direct",
    }
    assert {item["route_id"] for item in payload["candidates"]} == {
        "browser_named_direct_open_route",
        "browser_search_followup_route",
    }


def test_plan_payload_routes_app_history_search_to_app_interaction_domain() -> None:
    payload = plan_payload("search chat history in WeChat for Alice", intent_broker=RuleIntentBroker())

    assert payload["status"] == "validated"
    assert tuple(payload["analysis"]["domains"]) == ("app_interaction",)
    assert payload["plan"]["selected_route_id"] == "app_structured_search_route"
    assert payload["plan"]["steps"][0]["action"] == "app.search_history"
    assert payload["plan"]["steps"][0]["target"] == {"app": "WeChat", "query": "Alice", "interaction_surface": "structured"}
    assert {item["route_id"] for item in payload["candidates"]} == {
        "app_structured_search_route",
        "app_shortcut_search_route",
    }


def test_plan_payload_routes_bare_domains_to_browser_open_url() -> None:
    payload = plan_payload("open baidu.com", intent_broker=RuleIntentBroker())

    assert payload["status"] == "validated"
    assert payload["plan"]["selected_route_id"] == "browser_open_url_route"
    assert payload["plan"]["steps"][0]["action"] == "browser.open_url"
    assert payload["plan"]["steps"][0]["target"]["uri"] == "https://baidu.com"


def test_plan_payload_uses_provider_open_url_intent_for_named_site_requests() -> None:
    payload = plan_payload(
        "\u5e2e\u6211\u6253\u5f00\u767e\u5ea6\u5b98\u7f51",
        intent_broker=StaticIntentBroker(
            Intent(
                action="browser.open_url",
                target={"url": "https://www.baidu.com"},
                reason="provider resolved the named site to a concrete URL",
            )
        ),
    )

    assert payload["status"] == "validated"
    assert payload["plan"]["schema_version"] == "v0.5"
    assert payload["plan"]["selected_route_id"] == "browser_open_url_route"
    assert payload["plan"]["steps"][0]["action"] == "browser.open_url"
    assert payload["plan"]["steps"][0]["target"]["uri"] == "https://www.baidu.com"


def test_plan_payload_uses_provider_search_web_intent_for_non_literal_search_phrasing() -> None:
    payload = plan_payload(
        "帮我查一下 OpenAI 文档",
        intent_broker=StaticIntentBroker(
            Intent(
                action="browser.search_web",
                target={"query": "OpenAI 文档"},
                reason="provider resolved the utterance into a browser search request",
            )
        ),
    )

    assert payload["status"] == "validated"
    assert payload["plan"]["selected_route_id"] == "browser_search_web_route"
    assert payload["plan"]["steps"][0]["action"] == "browser.search_web"
    assert payload["plan"]["steps"][0]["target"]["query"] == "OpenAI 文档"


def test_plan_payload_uses_provider_site_search_intent_for_non_literal_site_search_phrasing() -> None:
    payload = plan_payload(
        "帮我去知乎搜 OpenAI",
        intent_broker=StaticIntentBroker(
            Intent(
                action="browser.open_site_search",
                target={"site": "zhihu.com", "query": "OpenAI"},
                reason="provider resolved the utterance into a site-scoped browser search request",
            )
        ),
    )

    assert payload["status"] == "validated"
    assert payload["plan"]["selected_route_id"] == "browser_site_search_route"
    assert payload["plan"]["steps"][0]["action"] == "browser.open_site_search"
    assert payload["plan"]["steps"][0]["target"]["site"] == "zhihu.com"
    assert payload["plan"]["steps"][0]["target"]["query"] == "OpenAI"


def test_plan_payload_emits_primary_understanding_candidate_set_and_route_decision() -> None:
    payload = plan_payload("\u6253\u5f00\u767e\u5ea6\u5b98\u7f51", intent_broker=RuleIntentBroker())

    understanding = payload["understanding"]
    candidate_set = payload["candidate_set"]
    route_decision = payload["route_decision"]

    assert understanding["understanding_id"].startswith("und_")
    assert candidate_set["understanding_id"] == understanding["understanding_id"]
    assert candidate_set["candidate_set_id"].startswith("cset_")
    assert route_decision["candidate_set_id"] == candidate_set["candidate_set_id"]
    assert route_decision["understanding_id"] == understanding["understanding_id"]
    assert route_decision["action"] == "select"
    assert route_decision["provider_name"] == "local"
    assert route_decision["fallback_used"] is True
    assert understanding["analysis_provider_name"] == "local"
    assert understanding["analysis_fallback_used"] is True


def test_plan_turn_reuses_primary_understanding_without_reparsing_same_utterance() -> None:
    broker = CountingIntentBroker(
        Intent(
            action="browser.open_url",
            target={"url": "https://www.baidu.com"},
            reason="provider resolved the named site to a concrete URL",
        )
    )

    planning = plan_turn("\u5e2e\u6211\u6253\u5f00\u767e\u5ea6\u5b98\u7f51", intent_broker=broker)

    assert broker.call_count == 1
    assert planning.understanding.provider_parse_count == 1
    assert planning.plan is not None
    assert planning.plan.steps[0].target["uri"] == "https://www.baidu.com"


def test_plan_turn_rejects_unknown_candidate_id_from_selection_layer() -> None:
    with pytest.raises(ValueError, match="unknown candidate_id"):
        plan_turn(
            "open baidu.com",
            intent_broker=RuleIntentBroker(),
            selection_provider=StaticSelectionProvider(selected_candidate_id="cand_missing"),
        )


def test_replan_rejects_unknown_candidate_id() -> None:
    test_plan_turn_rejects_unknown_candidate_id_from_selection_layer()


def test_plan_turn_supports_bounded_route_selection_provider_override() -> None:
    planning = plan_turn(
        "open baidu.com",
        intent_broker=RuleIntentBroker(),
        selection_provider=StaticSelectionProvider(selected_candidate_id="cand_browser_open_url_route"),
    )

    assert planning.plan is not None
    assert planning.route_decision is not None
    assert planning.route_decision.provider_name == "test_selector"
    assert planning.plan.selected_route_id == "browser_open_url_route"


def test_route_domains_ignores_unknown_candidate_domain_ids_before_observation() -> None:
    analysis = UtteranceAnalysis(
        utterance="search chat history in WeChat for Alice",
        type="task",
        confidence=0.88,
        domains=("app_interaction",),
        explanation="Structured capability analysis resolved app.search_history.",
        task_spans=(TaskSpan(id="span_1", text="search chat history in WeChat for Alice", start=0, end=39, domain="app_interaction", confidence=0.88),),
        provenance=None,
        chat_response=None,
    )
    registry = default_domain_registry(default_verifier_registry().ids())

    routing = route_domains(analysis, registry, candidate_domain_ids=("app_interaction", "unknown_domain"))

    assert routing is not None
    assert routing.candidate_domain_ids == ("app_interaction",)
    assert routing.active_domain_ids == ("app_interaction",)


def test_plan_turn_preserves_clarify_route_decision_even_when_candidates_exist() -> None:
    clarify_payload = plan_turn(
        "open baidu.com",
        intent_broker=RuleIntentBroker(),
        capability_context={"browser.open_url"},
        selection_provider=StaticSelectionProvider(selected_candidate_id=None, action="clarify"),
    )

    assert clarify_payload.plan is None
    assert clarify_payload.route_decision is not None
    assert clarify_payload.route_decision.action == "clarify"


def test_plan_payload_maps_clarify_route_decision_with_candidates_to_clarification_status(monkeypatch) -> None:
    redirected = plan_turn(
        "open baidu.com",
        intent_broker=RuleIntentBroker(),
        capability_context={"browser.open_url"},
        selection_provider=StaticSelectionProvider(selected_candidate_id=None, action="clarify"),
    )
    monkeypatch.setattr("vibeos.planner.plan_turn", lambda *args, **kwargs: redirected)

    payload = plan_payload("open baidu.com", intent_broker=RuleIntentBroker())

    assert payload["status"] == "clarification"
    assert payload["plan"] is None
    assert payload["route_decision"]["action"] == "clarify"
    assert payload["overall_status"] == "needs_user_input"


def test_plan_payload_maps_blocked_route_decision_with_candidates_to_blocked_status(monkeypatch) -> None:
    planning = plan_turn(
        "open baidu.com",
        intent_broker=RuleIntentBroker(),
        capability_context={"browser.open_url"},
        selection_provider=StaticSelectionProvider(selected_candidate_id=None, action="blocked"),
    )
    monkeypatch.setattr("vibeos.planner.plan_turn", lambda *args, **kwargs: planning)

    payload = plan_payload("open baidu.com", intent_broker=RuleIntentBroker())

    assert payload["status"] == "blocked"
    assert payload["plan"] is None
    assert payload["route_decision"]["action"] == "blocked"
    assert payload["overall_status"] == "blocked"


def test_plan_payload_returns_clarification_for_ambiguous_site_reference() -> None:
    payload = plan_payload("open that site we discussed yesterday")

    assert payload["status"] == "clarification"
    assert payload["plan"] is None
    assert payload["route_decision"]["action"] == "clarify"
    assert payload["understanding"]["uncertainty_reasons"]
    assert payload["analysis"]["chat_response"] == "Which site do you mean?"
    assert payload["understanding"]["clarification_provider_name"] == "local"
    assert payload["understanding"]["clarification_fallback_used"] is True
    assert payload["understanding"]["clarification_question_id"].startswith("cqid_")


def test_primary_understanding_supports_clarification_provider_override() -> None:
    understanding, _ = create_primary_understanding(
        "open that site we discussed yesterday",
        clarification_provider=StaticClarificationProvider(),
    )

    assert understanding.analysis.chat_response == "Which exact site should I open?"
    assert understanding.clarification_provider_name == "test_clarifier"
    assert understanding.clarification_question_id == "cqid_test"


def test_primary_understanding_supports_understanding_provider_override() -> None:
    understanding, _ = create_primary_understanding(
        "open that site we discussed yesterday",
        analysis_provider=StaticUnderstandingProvider(),
    )

    assert understanding.analysis.type == "clarification"
    assert understanding.analysis.chat_response == "Which site should I open exactly?"
    assert understanding.analysis_provider_name == "test_understanding"


def test_primary_understanding_captures_provider_intent_for_task_analysis() -> None:
    understanding, broker = create_primary_understanding(
        "open browser",
        intent_broker=StaticIntentBroker(
            Intent(
                action="app.open",
                target={"name": "browser"},
                reason="test broker resolved the app-open request",
            )
        ),
        analysis_provider=BrowserTaskUnderstandingProvider(),
    )

    assert understanding.provider_intent is not None
    assert understanding.provider_intent.action == "app.open"
    assert broker.provider_parse_count == 1
    hint = build_goal_synthesis_boundary_hint(
        utterance="open browser",
        analysis=understanding.analysis,
        intent_broker=broker,
    )
    assert hint["required_capability_ids"] == ["app.open"]
    assert hint["candidate_domain_ids"] == ["apps", "browser"]


def test_primary_understanding_main_path_no_longer_depends_on_legacy_nlu_pipeline(monkeypatch) -> None:
    def fail_legacy_nlu(*args, **kwargs):
        raise AssertionError("legacy analyze_utterance should not be called by primary understanding")

    monkeypatch.setattr("vibeos.nlu.analyze_utterance", fail_legacy_nlu)

    understanding, _ = create_primary_understanding("open browser")

    assert understanding.analysis.type == "task"
    assert understanding.analysis_provider_name == "local"
    assert understanding.analysis_fallback_used is True


def test_provider_understanding_path_no_longer_runs_deterministic_fallback_before_success(monkeypatch) -> None:
    monkeypatch.setenv("VIBEOS_ENABLE_MODEL_UNDERSTANDING", "1")
    provider = OpenAICompatibleUnderstandingAnalysisProvider(fallback=FailingUnderstandingFallback())
    provider.config = OpenAICompatibleProviderConfig(
        provider_name="test-provider",
        model_name="test-model",
        api_key="test-key",
        base_url="https://example.invalid",
    )
    provider.provider_name = "test-provider"
    provider.model_name = "test-model"

    def fake_request_json_object(**kwargs):
        return ProviderJsonObjectResponse(
            request_payload={"messages": []},
            response_payload={"ok": True},
            parsed_object={
                "type": "task",
                "confidence": 0.93,
                "domains": ["browser"],
                "explanation": "provider classified the utterance as a browser task",
                "chat_response": None,
            },
        )

    monkeypatch.setattr("vibeos.understanding.request_json_object", fake_request_json_object)

    broker = CapturingIntentBroker(RuleIntentBroker())
    decision = provider.analyze(utterance="open baidu.com", broker=broker)

    assert decision.provider_name == "test-provider"
    assert decision.parse_valid is True
    assert decision.fallback_used is False
    assert decision.analysis.type == "task"
    assert decision.analysis.domains == ("browser",)
    assert broker.provider_parse_count == 0


def test_primary_understanding_host_hint_no_longer_suggests_domains_from_raw_text() -> None:
    assert default_understanding_host_hint("open browser")["suggested_domains"] == []


def test_validated_understanding_requires_model_supplied_domain_for_primary_task_analysis() -> None:
    with pytest.raises(ValueError, match="requires at least one allowed domain"):
        validated_understanding_from_payload(
            utterance="open browser",
            payload={
                "type": "task",
                "confidence": 0.91,
                "domains": [],
                "explanation": "provider classified the utterance as a task without committing a domain",
                "chat_response": None,
            },
            host_hint=default_understanding_host_hint("open browser"),
        )


def test_validated_understanding_transition_may_reuse_prior_domain_without_reinferring_from_raw_text() -> None:
    prior_analysis = UtteranceAnalysis(
        utterance="open browser",
        type="task",
        confidence=0.93,
        domains=("apps",),
        explanation="prior analysis already committed to the apps domain",
        task_spans=(
            TaskSpan(
                id="span_1",
                text="open browser",
                start=0,
                end=len("open browser"),
                domain="apps",
                confidence=0.93,
            ),
        ),
        provenance=None,
    )

    validated = validated_understanding_from_payload(
        utterance="open browser",
        payload={
            "type": "task",
            "confidence": 0.94,
            "domains": [],
            "explanation": "transition kept the prior executable interpretation",
            "chat_response": None,
        },
        host_hint={"suggested_domains": [], "default_confidence": 0.5, "default_clarification_question": "What detail should I use to continue?"},
        prior_analysis=prior_analysis,
    )

    assert validated.domains == ("apps",)
    assert validated.task_spans[0].domain == "apps"


def test_plan_turn_main_path_no_longer_depends_on_shadow_rule_reparse(monkeypatch) -> None:
    def fail_shadow_rule_parse(self, utterance: str) -> Intent:
        raise AssertionError("shadow RuleIntentBroker reparse should not run inside planner compatibility path")

    monkeypatch.setattr("vibeos.intent.RuleIntentBroker.parse", fail_shadow_rule_parse)

    planning = plan_turn(
        "open browser",
        intent_broker=StaticIntentBroker(
            Intent(
                action="app.open",
                target={"name": "browser"},
                reason="test broker resolved the app-open request",
            )
        ),
    )

    assert planning.plan is not None
    assert planning.plan.selected_route_id == "apps_open_route"
    assert planning.plan.steps[0].action == "app.open"


def test_plan_turn_recovers_app_open_boundary_when_model_goal_synthesis_drifts(monkeypatch) -> None:
    monkeypatch.setenv("VIBEOS_ENABLE_MODEL_GOAL_SYNTHESIS", "1")
    shared_broker = CapturingIntentBroker(
        StaticIntentBroker(
            Intent(
                action="app.open",
                target={"name": "browser"},
                reason="test broker resolved the app-open request",
            )
        )
    )
    understanding, _ = create_primary_understanding(
        "open browser",
        intent_broker=shared_broker,
        analysis_provider=BrowserTaskUnderstandingProvider(),
    )
    provider = OpenAICompatibleGoalSynthesisProvider(shared_broker)
    provider.config = OpenAICompatibleProviderConfig(
        provider_name="test-provider",
        model_name="test-model",
        api_key="test-key",
        base_url="https://example.invalid/v1",
    )

    def fake_request_json_object(**kwargs):
        return ProviderJsonObjectResponse(
            request_payload={"messages": []},
            response_payload={"ok": True},
            parsed_object={
                "status": "ready",
                "goal_type": "browser_open_url",
                "domain": "browser",
                "capability": "browser.open_url",
                "parameters": {"url": ""},
            },
        )

    monkeypatch.setattr("vibeos.goal_synthesizer.request_json_object", fake_request_json_object)

    planning = plan_turn(
        "open browser",
        understanding=understanding,
        intent_broker=shared_broker,
        goal_synthesis_provider=provider,
    )

    assert planning.understanding.provider_intent is not None
    assert planning.understanding.provider_intent.action == "app.open"
    assert planning.goal_synthesis is not None
    assert planning.goal_synthesis.exchange.fallback_used is True
    assert planning.goal_synthesis.goal_spec is not None
    assert planning.goal_synthesis.goal_spec.required_capability_ids == ("app.open",)
    assert planning.goal_synthesis.goal_spec.candidate_domain_ids == ("apps", "browser")
    assert planning.plan is not None
    assert planning.plan.selected_route_id == "apps_open_route"
    assert planning.plan.steps[0].action == "app.open"


def test_reconcile_understanding_transition_emits_refinement_for_same_type_semantic_shift() -> None:
    understanding, _ = create_primary_understanding("open browser")
    refined_analysis = UtteranceAnalysis(
        utterance="open browser",
        type="task",
        confidence=0.95,
        domains=("browser",),
        explanation="refined toward browser navigation",
        task_spans=understanding.analysis.task_spans,
        provenance=understanding.analysis.provenance,
    )

    updated, refinement, supersession = reconcile_understanding_transition(
        understanding,
        refined_analysis,
        reason="bounded replanning narrowed the task domain",
    )

    assert supersession is None
    assert refinement is not None
    assert updated.artifact_role == "refinement"
    assert updated.primary_understanding_id == understanding.understanding_id
    assert updated.source_understanding_id == understanding.understanding_id
    assert refinement.primary_understanding_id == understanding.understanding_id
    assert refinement.previous_understanding_id == understanding.understanding_id
    assert refinement.refined_understanding_id == updated.understanding_id
    assert "domains" in refinement.changed_fields


def test_reconcile_understanding_transition_emits_supersession_for_type_change() -> None:
    understanding, _ = create_primary_understanding("open browser")
    superseding_analysis = UtteranceAnalysis(
        utterance="open browser",
        type="clarification",
        confidence=0.91,
        domains=("browser",),
        explanation="later evidence showed the target is ambiguous",
        task_spans=(),
        provenance=understanding.analysis.provenance,
        chat_response="Which browser or site should I open?",
    )

    updated, refinement, supersession = reconcile_understanding_transition(
        understanding,
        superseding_analysis,
        reason="later evidence invalidated the earlier executable interpretation",
    )

    assert refinement is None
    assert supersession is not None
    assert updated.artifact_role == "supersession"
    assert updated.primary_understanding_id == understanding.understanding_id
    assert updated.source_understanding_id == understanding.understanding_id
    assert supersession.primary_understanding_id == understanding.understanding_id
    assert supersession.previous_understanding_id == understanding.understanding_id
    assert supersession.superseding_understanding_id == updated.understanding_id
    assert "type" in supersession.changed_fields


def test_plan_payload_supports_chinese_browser_and_media_examples() -> None:
    open_payload = plan_payload("打开 https://example.com")
    search_payload = plan_payload("搜索 hello")
    media_payload = plan_payload("我想听 baby")

    assert open_payload["plan"]["steps"][0]["action"] == "browser.open_url"
    assert search_payload["plan"]["steps"][0]["action"] == "browser.search_web"
    assert media_payload["plan"]["selected_route_id"] == "browser_music_search_route"
