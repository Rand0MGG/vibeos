import pytest

from vibeos.goal_synthesizer import (
    GoalSynthesisProvider,
    GoalSynthesizer,
    OpenAICompatibleGoalSynthesisProvider,
    RuleBasedGoalSynthesisProvider,
    build_goal_synthesis_boundary_hint,
    synthesize_assistant_intent,
    validate_goal_synthesis_payload,
)
from vibeos.intent import IntentBroker, RuleIntentBroker
from vibeos.models import Intent
from vibeos.nlu import analyze_utterance
from vibeos.provider_client import OpenAICompatibleProviderConfig, ProviderJsonObjectResponse
from vibeos.task_models import TaskSpan, UtteranceAnalysis
from vibeos.understanding import CapturingIntentBroker


class StaticIntentBroker(IntentBroker):
    def __init__(self, intent: Intent) -> None:
        self.intent = intent

    def parse(self, utterance: str) -> Intent:
        return self.intent


def test_goal_synthesizer_returns_typed_ready_goal_for_supported_request() -> None:
    utterance = "open browser"
    analysis = analyze_utterance(utterance)

    result = GoalSynthesizer().synthesize(utterance, analysis)

    assert result.status == "ready"
    assert result.goal_spec is not None
    assert result.goal_spec.goal_type == "app_open"
    assert result.goal_spec.candidate_domain_ids == ("apps",)
    assert result.goal_spec.required_capability_ids == ("app.open",)


def test_goal_synthesizer_returns_clarification_for_incomplete_media_request() -> None:
    utterance = "play"
    analysis = analyze_utterance(utterance)

    result = GoalSynthesizer().synthesize(utterance, analysis)

    assert result.status == "clarification_needed"
    assert result.goal_spec is not None
    assert result.goal_spec.clarification_questions


def test_goal_synthesizer_returns_missing_capability_for_unsupported_request() -> None:
    utterance = "email Alice about the report"
    analysis = analyze_utterance(utterance)

    result = GoalSynthesizer().synthesize(utterance, analysis)

    assert result.status == "missing_capability"
    assert result.goal_spec is not None
    assert result.goal_spec.missing_capability_ids == ("email.send",)


def test_assistant_intent_prefers_structured_browser_search_intent_for_named_website_requests() -> None:
    utterance = "帮我打开百度官网"
    analysis = UtteranceAnalysis(
        utterance=utterance,
        type="task",
        confidence=0.95,
        domains=("browser",),
        explanation="provider resolved a named-website browser task",
        task_spans=(
            TaskSpan(
                id="span_1",
                text=utterance,
                start=0,
                end=len(utterance),
                domain="browser",
                confidence=0.95,
            ),
        ),
        provenance=None,
    )

    assistant_intent = synthesize_assistant_intent(
        utterance,
        analysis,
        StaticIntentBroker(
            Intent(
                action="browser.search_web",
                target={"query": "百度官网"},
                reason="provider selected browser search for a named website request",
            )
        ),
    )

    assert assistant_intent is not None
    assert assistant_intent.objective_kind == "open_named_website"
    assert assistant_intent.target.display_name == "百度官网"
    assert assistant_intent.target.query_text == "百度官网"


class FixedGoalSynthesisProvider(GoalSynthesisProvider):
    provider_name = "fake_goal_synthesizer"
    provider_version = "v0.test"
    model_name = "fake-structured"

    def synthesize(self, utterance: str, analysis) -> dict[str, object]:
        self._last_parse_valid = True
        self._last_fallback_used = False
        self._last_error = None
        self._last_raw_output = '{"status":"ready"}'
        return {
            "status": "ready",
            "goal_type": "browser_search_web",
            "candidate_domain_ids": ["browser"],
            "required_capability_ids": ["browser.search_web"],
            "missing_capability_ids": [],
            "clarification_questions": [],
            "constraints": ["host-owned route boundaries remain in effect"],
            "fallback_hints": [],
            "assumptions": ["fake provider override"],
            "assistant_intent": None,
            "subgoals": [
                {
                    "subgoal_id": "subgoal_1",
                    "text": utterance,
                    "goal_type": "browser_search_web",
                    "candidate_domain_ids": ["browser"],
                    "required_capability_ids": ["browser.search_web"],
                }
            ],
            "message": "fake provider synthesized goal",
        }


class LegacyShapeGoalSynthesisProvider(GoalSynthesisProvider):
    provider_name = "fake_legacy_goal_synthesizer"
    provider_version = "v0.test"
    model_name = "fake-structured"

    def synthesize(self, utterance: str, analysis) -> dict[str, object]:
        self._last_parse_valid = True
        self._last_fallback_used = False
        self._last_error = None
        self._last_raw_output = '{"status":"ready"}'
        return {
            "status": "ready",
            "type": "browser_open_url",
            "domain_id": "browser",
            "capability_id": "browser.open_url",
            "message": "fake legacy provider synthesized goal",
        }


class MismatchedAppOpenGoalSynthesisProvider(GoalSynthesisProvider):
    provider_name = "fake_mismatched_goal_synthesizer"
    provider_version = "v0.test"
    model_name = "fake-structured"

    def synthesize(self, utterance: str, analysis) -> dict[str, object]:
        self._last_parse_valid = True
        self._last_fallback_used = False
        self._last_error = None
        self._last_raw_output = '{"status":"ready"}'
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
            "message": "fake provider synthesized a mismatched app-open goal",
        }


class FailingGoalSynthesisFallback(GoalSynthesisProvider):
    def synthesize(self, utterance: str, analysis) -> dict[str, object]:
        raise AssertionError("rule goal synthesis fallback should not run before a successful provider response")


def test_goal_synthesizer_supports_provider_override() -> None:
    utterance = "search web for hello"
    analysis = analyze_utterance(utterance)

    result = GoalSynthesizer(provider=FixedGoalSynthesisProvider()).synthesize(utterance, analysis)

    assert result.status == "ready"
    assert result.goal_spec is not None
    assert result.goal_spec.goal_type == "browser_search_web"
    assert result.goal_spec.assumptions == ("fake provider override",)
    assert result.exchange.provider_name == "fake_goal_synthesizer"


def test_goal_synthesis_validation_normalizes_legacy_provider_shape_into_host_bounded_goal() -> None:
    utterance = "open baidu.com"
    analysis = analyze_utterance(utterance)
    host_hint = RuleBasedGoalSynthesisProvider().synthesize(utterance, analysis)
    validated = validate_goal_synthesis_payload(
        LegacyShapeGoalSynthesisProvider().synthesize(utterance, analysis),
        host_hint=host_hint,
    )

    assert validated["goal_type"] == "browser_open_url"
    assert validated["candidate_domain_ids"] == ["browser"]
    assert validated["required_capability_ids"] == ["browser.open_url"]


def test_goal_synthesis_boundary_hint_includes_intent_implied_domain_when_analysis_drifts() -> None:
    analysis = UtteranceAnalysis(
        utterance="open browser",
        type="task",
        confidence=0.95,
        domains=("browser",),
        explanation="provider analysis drifted toward browser navigation",
        task_spans=(
            TaskSpan(
                id="span_1",
                text="open browser",
                start=0,
                end=len("open browser"),
                domain="browser",
                confidence=0.95,
            ),
        ),
        provenance=None,
    )

    broker = CapturingIntentBroker(StaticIntentBroker(Intent(action="app.open", target={"name": "browser"}, reason="cached app-open intent")))
    broker.remember("open browser", Intent(action="app.open", target={"name": "browser"}, reason="cached app-open intent"))
    hint = build_goal_synthesis_boundary_hint(utterance="open browser", analysis=analysis, intent_broker=broker)

    assert hint["candidate_domain_ids"] == ["apps", "browser"]
    assert hint["required_capability_ids"] == ["app.open"]


def test_goal_synthesizer_reconciles_provider_domains_with_assistant_intent_preferences() -> None:
    analysis = analyze_utterance("open browser")

    result = GoalSynthesizer(provider=MismatchedAppOpenGoalSynthesisProvider()).synthesize("open browser", analysis)

    assert result.goal_spec is not None
    assert result.goal_spec.candidate_domain_ids == ("apps", "browser")
    assert result.goal_spec.required_capability_ids == ("app.open",)
    assert result.exchange.normalized_output["candidate_domain_ids"] == ["apps", "browser"]


def test_rule_goal_synthesizer_main_path_no_longer_depends_on_shadow_rule_reparse(monkeypatch) -> None:
    def fail_shadow_rule_parse(self, utterance: str) -> Intent:
        raise AssertionError("shadow RuleIntentBroker reparse should not run inside goal synthesis")

    monkeypatch.setattr(RuleIntentBroker, "parse", fail_shadow_rule_parse)
    analysis = UtteranceAnalysis(
        utterance="open browser",
        type="task",
        confidence=0.95,
        domains=("apps",),
        explanation="open browser as an application",
        task_spans=(
            TaskSpan(
                id="span_1",
                text="open browser",
                start=0,
                end=len("open browser"),
                domain="apps",
                confidence=0.95,
            ),
        ),
        provenance=None,
    )

    result = GoalSynthesizer(
        provider=RuleBasedGoalSynthesisProvider(
            intent_broker=StaticIntentBroker(
                Intent(action="app.open", target={"name": "browser"}, reason="test broker resolved open browser"),
            )
        )
    ).synthesize("open browser", analysis)

    assert result.status == "ready"
    assert result.goal_spec is not None
    assert result.goal_spec.required_capability_ids == ("app.open",)


def test_provider_goal_synthesis_path_no_longer_runs_rule_fallback_before_success(monkeypatch) -> None:
    monkeypatch.setenv("VIBEOS_ENABLE_MODEL_GOAL_SYNTHESIS", "1")
    utterance = "search web for hello"
    analysis = analyze_utterance(utterance)
    provider = OpenAICompatibleGoalSynthesisProvider(
        intent_broker=StaticIntentBroker(
            Intent(action="browser.search_web", target={"query": "hello"}, reason="cached provider intent"),
        ),
        fallback=FailingGoalSynthesisFallback(),
    )
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
                "status": "ready",
                "goal_type": "browser_search_web",
                "candidate_domain_ids": ["browser"],
                "required_capability_ids": ["browser.search_web"],
                "missing_capability_ids": [],
                "clarification_questions": [],
                "constraints": ["host-owned route boundaries remain in effect"],
                "fallback_hints": [],
                "assumptions": [],
                "assistant_intent": None,
                "subgoals": [],
                "message": "provider synthesized goal",
            },
        )

    monkeypatch.setattr("vibeos.goal_synthesizer.request_json_object", fake_request_json_object)

    result = GoalSynthesizer(provider=provider).synthesize(utterance, analysis)

    assert result.status == "ready"
    assert result.goal_spec is not None
    assert result.goal_spec.required_capability_ids == ("browser.search_web",)
    assert result.exchange.provider_name == "test-provider"
    assert result.exchange.fallback_used is False
