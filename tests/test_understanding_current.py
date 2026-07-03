from vibeos.goal_synthesizer import build_goal_synthesis_boundary_hint
from vibeos.models import Intent
from vibeos.understanding import create_primary_understanding
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
