from __future__ import annotations

import json
import urllib.error

from .capabilities import executable_actions
from .models import Intent
from .provider_client import load_openai_compatible_provider_config, request_json_object
from .task_trace import record_model_io, record_trace_event
from .validation import IntentValidationError, parse_intent_json

ALLOWED_ACTIONS_TEXT = ", ".join([*executable_actions(), "unknown"])
SYSTEM_PROMPT = """You are VibeOS's model intent broker.
Translate the user's natural-language Linux desktop request into exactly one JSON object.
Allowed actions: """ + ALLOWED_ACTIONS_TEXT + """.
Do not include shell commands, scripts, raw D-Bus paths, raw API calls, or implementation details.
Map the request to the best allowed action when possible.
If the request cannot be represented without inventing a new capability or authority outside the allowed actions, return action "unknown" with a short reason.
Schema:
{
  "action": "app.open",
  "target": {"name": "browser", "kind": "application"},
  "reason": "short explanation",
  "requires_confirmation": false
}
Return JSON only."""


class IntentBroker:
    def parse(self, utterance: str) -> Intent:
        raise NotImplementedError


class OpenAICompatibleIntentBroker(IntentBroker):
    def __init__(self) -> None:
        self.config = load_openai_compatible_provider_config(default_openai_model=None)
        self.provider = self.config.provider_name
        self.model = self.config.model_name
        self.api_key = self.config.api_key
        self.base_url = self.config.base_url
        self._successful_parse_cache: dict[str, Intent] = {}

    def parse(self, utterance: str) -> Intent:
        cached = self._successful_parse_cache.get(utterance)
        if cached is not None:
            record_trace_event(
                phase="analysis",
                event_type="intent_broker_cache_hit",
                status="ok",
                actor="intent_broker",
                data={"provider": self.provider, "utterance": utterance},
            )
            return cached
        if not self.config.configured:
            record_model_io(
                phase="analysis",
                provider=self.provider,
                model=self.model,
                request_payload={"utterance": utterance},
                response_payload=None,
                normalized_output=None,
                parse_valid=False,
                error="missing_api_key_or_model",
                actor="intent_broker",
            )
            return Intent.unknown("model provider is unavailable")

        try:
            response = request_json_object(
                config=self.config,
                system_prompt=SYSTEM_PROMPT,
                user_content=utterance,
                max_tokens=512,
            )
            content = json.dumps(response.parsed_object, ensure_ascii=False)
            parsed = parse_intent_json(content)
            record_model_io(
                phase="analysis",
                provider=self.provider,
                model=self.model,
                request_payload=response.request_payload,
                response_payload=response.response_payload,
                normalized_output={
                    "action": parsed.action,
                    "target": parsed.target,
                    "reason": parsed.reason,
                    "requires_confirmation": parsed.requires_confirmation,
                },
                actor="intent_broker",
            )
            self._successful_parse_cache[utterance] = parsed
            return parsed
        except urllib.error.URLError as exc:
            record_model_io(
                phase="analysis",
                provider=self.provider,
                model=self.model,
                request_payload={"utterance": utterance},
                response_payload=None,
                normalized_output=None,
                parse_valid=False,
                error=str(exc),
                actor="intent_broker",
            )
            return Intent.unknown("model provider is unavailable")
        except TimeoutError as exc:
            record_model_io(
                phase="analysis",
                provider=self.provider,
                model=self.model,
                request_payload={"utterance": utterance},
                response_payload=None,
                normalized_output=None,
                parse_valid=False,
                error=str(exc),
                actor="intent_broker",
            )
            return Intent.unknown("model provider timed out")
        except (KeyError, IntentValidationError, json.JSONDecodeError) as exc:
            record_model_io(
                phase="analysis",
                provider=self.provider,
                model=self.model,
                request_payload={"utterance": utterance},
                response_payload=locals().get("response"),
                normalized_output=None,
                parse_valid=False,
                error=str(exc),
                actor="intent_broker",
            )
            return Intent.unknown("model provider returned an invalid intent payload")
