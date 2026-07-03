import pytest

from vibeos.clarification import OpenAICompatibleClarificationProvider
from vibeos.domain_registry import default_domain_registry
from vibeos.goal_synthesizer import synthesize_assistant_intent
from vibeos.intent import IntentBroker
from vibeos.models import Intent
from vibeos.planner import (
    build_browser_open_url_plan,
    build_browser_media_fallback_plan,
    build_browser_search_web_plan,
    build_browser_site_search_plan,
    build_media_pause_plan,
    build_media_play_plan,
    build_media_search_plan,
)
from vibeos.task_models import FailureClassification, ReplanDecision, TaskSpan, UtteranceAnalysis
from vibeos.understanding import OpenAICompatibleUnderstandingTransitionProvider, UnderstandingArtifact
from vibeos.verifiers import default_verifier_registry


class StaticIntentBroker(IntentBroker):
    def __init__(self, intent: Intent) -> None:
        self.intent = intent

    def parse(self, utterance: str) -> Intent:
        return self.intent


@pytest.mark.parametrize(
    ("builder", "route_id", "utterance"),
    (
        (build_browser_open_url_plan, "browser_open_url_route", "open https://example.com"),
        (build_browser_search_web_plan, "browser_search_web_route", "search web for hello"),
        (build_browser_site_search_plan, "browser_site_search_route", "search example.com for hello"),
        (build_media_play_plan, "media_play_route", "play hello"),
        (build_media_search_plan, "media_search_route", "search media for hello"),
        (build_media_pause_plan, "media_pause_route", "pause"),
        (build_browser_media_fallback_plan, "browser_music_search_route", "play hello"),
    ),
)
def test_browser_plan_builders_do_not_reparse_raw_text(builder, route_id, utterance) -> None:
    route_definition = default_domain_registry(default_verifier_registry().ids()).get_route(route_id)
    assert route_definition is not None
    span = TaskSpan(id="span_1", text=utterance, start=0, end=len(utterance), domain="browser", confidence=0.9)

    plan = builder(utterance, span, route_definition, StaticIntentBroker(Intent.unknown("structured intent unavailable")))

    assert plan is None


def test_search_web_intent_stays_search_web_without_named_website_guess() -> None:
    analysis = UtteranceAnalysis(
        utterance="帮我打开百度官网",
        type="task",
        confidence=0.9,
        domains=("browser",),
        explanation="structured capability analysis resolved browser.search_web.",
        task_spans=(TaskSpan(id="span_1", text="帮我打开百度官网", start=0, end=8, domain="browser", confidence=0.9),),
    )

    assistant_intent = synthesize_assistant_intent(
        "帮我打开百度官网",
        analysis,
        intent_broker=StaticIntentBroker(Intent(action="browser.search_web", target={"query": "百度官网"}, reason="test")),
    )

    assert assistant_intent is not None
    assert assistant_intent.objective_kind == "search_web"
    assert assistant_intent.target.display_name == "百度官网"


def test_clarification_fallback_uses_generic_host_hint(monkeypatch) -> None:
    monkeypatch.setenv("VIBEOS_ENABLE_MODEL_CLARIFICATION", "0")
    analysis = UtteranceAnalysis(
        utterance="search something",
        type="clarification",
        confidence=0.4,
        domains=("browser",),
        explanation="missing detail",
    )

    decision = OpenAICompatibleClarificationProvider().generate(utterance=analysis.utterance, analysis=analysis)

    assert decision.question == "What detail should I use to continue?"
    assert decision.fallback_used is True


def test_understanding_transition_fallback_does_not_inject_browser_defaults(monkeypatch) -> None:
    monkeypatch.setenv("VIBEOS_ENABLE_MODEL_UNDERSTANDING_TRANSITION", "0")
    current_analysis = UtteranceAnalysis(
        utterance="do it",
        type="task",
        confidence=0.3,
        domains=(),
        explanation="unclear target",
    )
    understanding = UnderstandingArtifact(understanding_id="u1", utterance="do it", analysis=current_analysis)

    decision = OpenAICompatibleUnderstandingTransitionProvider().transition(
        understanding=understanding,
        current_analysis=current_analysis,
        decision=ReplanDecision(action="ask_user"),
        failure=FailureClassification(failure_class="semantic_mismatch", message="need a specific target"),
    )

    assert decision.analysis.type == "clarification"
    assert decision.analysis.domains == ()
    assert decision.analysis.chat_response == "need a specific target"
    assert decision.fallback_used is True
