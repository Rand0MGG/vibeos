from __future__ import annotations

from .intent import IntentBroker
from .models import Intent


class CapturingIntentBroker(IntentBroker):
    """Cache one broker parse so later planning layers reuse the same intent."""

    def __init__(self, wrapped: IntentBroker) -> None:
        self.wrapped = wrapped
        self._cache: dict[str, Intent] = {}
        self.provider_parse_count = 0
        self.provider_cache_hit_count = 0

    def parse(self, utterance: str) -> Intent:
        key = utterance.strip()
        cached = self._cache.get(key)
        if cached is not None:
            self.provider_cache_hit_count += 1
            return cached
        self.provider_parse_count += 1
        parsed = self.wrapped.parse(utterance)
        self._cache[key] = parsed
        return parsed

    def cached_intent(self, utterance: str) -> Intent | None:
        return self._cache.get(utterance.strip())

    def remember(self, utterance: str, intent: Intent) -> None:
        self._cache[utterance.strip()] = intent
