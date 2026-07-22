from vibeos.goal_synthesizer import build_goal_synthesis_boundary_hint
from vibeos.models import Intent
from vibeos.understanding import OpenAICompatibleUnderstandingAnalysisProvider, CapturingIntentBroker, create_primary_understanding
from vibeos.intent import OpenAICompatibleIntentBroker
from tests.support_intent_broker import FixtureIntentBroker


class StaticIntentBroker(FixtureIntentBroker):
    def __init__(self, intent: Intent) -> None:
        self.intent = intent

    def parse(self, utterance: str) -> Intent:
        return self.intent


def test_create_primary_understanding_uses_explicit_broker_when_provider_unavailable() -> None:
    understanding, broker = create_primary_understanding(
        "open browser",
        intent_broker=StaticIntentBroker(Intent(action="app.open", target={"name": "browser"}, reason="test intent")),
    )

    assert understanding.analysis.type == "task"
    assert understanding.analysis.domains == ("apps",)
    assert broker.provider_parse_count == 1
    assert understanding.analysis_provider_name != "host_explicit_contract"


def test_live_provider_cannot_drift_an_explicit_notification_contract(monkeypatch) -> None:
    monkeypatch.setenv("VIBEOS_ENABLE_MODEL_UNDERSTANDING", "1")
    monkeypatch.setattr(
        "vibeos.understanding.request_json_object",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("explicit contract must not call the model")),
    )
    broker = CapturingIntentBroker(OpenAICompatibleIntentBroker())

    decision = OpenAICompatibleUnderstandingAnalysisProvider().analyze(
        utterance="notify VibeOS evidence",
        broker=broker,
    )

    assert decision.provider_name == "host_explicit_contract"
    assert decision.analysis.domains == ("notification",)
    assert broker.cached_intent("notify VibeOS evidence") == Intent(
        action="notification.send",
        target={"title": "VibeOS", "body": "VibeOS evidence"},
        reason="user asked to send a notification",
    )


def test_goal_boundary_hint_uses_cached_explicit_intent() -> None:
    understanding, broker = create_primary_understanding(
        "search web for hello",
        intent_broker=StaticIntentBroker(Intent(action="browser.search_web", target={"query": "hello"}, reason="test intent")),
    )

    hint = build_goal_synthesis_boundary_hint(
        utterance="search web for hello",
        analysis=understanding.analysis,
        intent_broker=broker,
    )

    assert hint["required_capability_ids"] == ["browser.search_web"]
    assert hint["candidate_domain_ids"] == ["browser"]
