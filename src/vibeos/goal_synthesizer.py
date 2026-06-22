from __future__ import annotations

from dataclasses import asdict
from hashlib import sha256
import json
import urllib.error
from typing import Any

from .assistant_semantics import (
    AssistantCompletionSemantics,
    AssistantIntent,
    AssistantIntentTarget,
    assistant_intent_from_payload,
    assistant_intent_to_payload,
)
from .capabilities import CAPABILITIES
from .goal_models import GoalSpec, GoalSubgoal, GoalSynthesisProvenance, GoalSynthesisResult, ProviderExchange
from .intent import (
    WEB_NAMED_TARGET_HINTS,
    IntentBroker,
    RuleIntentBroker,
    extract_app_history_search_target,
    extract_open_target,
    infer_browser_intent_from_open_request,
    normalize_bare_domain_uri,
)
from .provider_client import env_flag_enabled, load_openai_compatible_provider_config, request_json_object
from .task_trace import record_model_io
from .nlu import domain_for_action
from .task_models import TaskSpan, UtteranceAnalysis


GOAL_SYNTHESIS_SYSTEM_PROMPT = """You are VibeOS's bounded goal synthesizer.
Synthesize one structured goal object from the provided utterance, analysis, and host hints.
You must stay within host-owned candidate domains and capability boundaries.
Do not invent a new capability, route, tool, or authority outside the provided host hints.
Return JSON only."""


class GoalSynthesisProvider:
    provider_name = "provider"
    provider_version = "v0"
    model_name = "structured"
    _last_parse_valid = True
    _last_fallback_used = False
    _last_error: str | None = None
    _last_raw_output = ""

    def synthesize(self, utterance: str, analysis: UtteranceAnalysis) -> dict[str, object]:
        raise NotImplementedError

    def response_metadata(self) -> dict[str, object]:
        return {
            "parse_valid": self._last_parse_valid,
            "fallback_used": self._last_fallback_used,
            "error": self._last_error,
            "raw_output": self._last_raw_output,
        }


class RuleBasedGoalSynthesisProvider(GoalSynthesisProvider):
    provider_name = "rule_goal_synthesizer"
    provider_version = "v0.5"
    model_name = "deterministic-local"

    def __init__(self, intent_broker: IntentBroker | None = None) -> None:
        self.intent_broker = intent_broker or RuleIntentBroker()

    def synthesize(self, utterance: str, analysis: UtteranceAnalysis) -> dict[str, object]:
        self._last_parse_valid = True
        self._last_fallback_used = False
        self._last_error = None
        synthesized_assistant_intent = synthesize_assistant_intent(utterance, analysis, self.intent_broker)
        if synthesized_assistant_intent is not None and synthesized_assistant_intent.objective_kind == "in_app_search":
            output = {
                "status": "ready",
                "goal_type": "app_search_history",
                "candidate_domain_ids": ["app_interaction"],
                "required_capability_ids": ["app.search_history"],
                "missing_capability_ids": [],
                "clarification_questions": [],
                "constraints": ["Planner must preserve the goal while changing interaction surface."],
                "fallback_hints": ["shortcut fallback may replace structured UI search when controls are unavailable"],
                "assumptions": [],
                "assistant_intent": assistant_intent_to_payload(synthesized_assistant_intent),
                "subgoals": [
                    {
                        "subgoal_id": "subgoal_1",
                        "text": utterance.strip(),
                        "goal_type": "app_search_history",
                        "candidate_domain_ids": ["app_interaction"],
                        "required_capability_ids": ["app.search_history"],
                    }
                ],
                "message": "goal synthesis completed",
            }
            self._last_raw_output = str(output)
            return output

        if analysis.type == "clarification":
            output = {
                "status": "clarification_needed",
                "goal_type": "clarification",
                "candidate_domain_ids": list(analysis.domains),
                "required_capability_ids": [],
                "missing_capability_ids": [],
                "clarification_questions": [analysis.chat_response or analysis.explanation or "Please clarify the request."],
                "constraints": [],
                "fallback_hints": [],
                "assumptions": [],
                "assistant_intent": assistant_intent_to_payload(synthesized_assistant_intent),
                "subgoals": [],
                "message": analysis.explanation or "clarification required",
            }
            self._last_raw_output = str(output)
            return output

        if analysis.type == "rejected":
            missing_capabilities = infer_missing_capabilities(utterance)
            status = "missing_capability" if missing_capabilities else "unsupported"
            output = {
                "status": status,
                "goal_type": "unsupported",
                "candidate_domain_ids": [],
                "required_capability_ids": [],
                "missing_capability_ids": list(missing_capabilities),
                "clarification_questions": [],
                "constraints": ["Only registered capabilities may be composed into plans."],
                "fallback_hints": [],
                "assumptions": [],
                "assistant_intent": assistant_intent_to_payload(synthesized_assistant_intent),
                "subgoals": [],
                "message": analysis.explanation or "request is outside the registered capability surface",
            }
            self._last_raw_output = str(output)
            return output

        spans = analysis.task_spans or (
            TaskSpan(
                id="span_1",
                text=utterance.strip(),
                start=0,
                end=len(utterance.strip()),
                domain=analysis.domains[0] if analysis.domains else "system_observation",
                confidence=analysis.confidence,
            ),
        )
        subgoals = []
        candidate_domain_ids: list[str] = []
        required_capability_ids: list[str] = []
        fallback_hints: list[str] = []
        assumptions: list[str] = []
        for index, span in enumerate(spans, start=1):
            action = infer_action_for_span(span, self.intent_broker)
            domain_id = span.domain or (domain_for_action(action) if action else "")
            if domain_id and domain_id not in candidate_domain_ids:
                candidate_domain_ids.append(domain_id)
            if action and action in CAPABILITIES and action not in required_capability_ids:
                required_capability_ids.append(action)
            if domain_id == "media" and "browser" not in fallback_hints:
                fallback_hints.append("browser fallback may be used when dedicated media execution is unavailable")
            subgoals.append(
                {
                    "subgoal_id": f"subgoal_{index}",
                    "text": span.text,
                    "goal_type": goal_type_for_action(action, domain_id),
                    "candidate_domain_ids": [domain_id] if domain_id else [],
                    "required_capability_ids": [action] if action in CAPABILITIES else [],
                }
            )

        if not candidate_domain_ids:
            missing_capabilities = infer_missing_capabilities(utterance)
            output = {
                "status": "missing_capability" if missing_capabilities else "unsupported",
                "goal_type": "unsupported",
                "candidate_domain_ids": [],
                "required_capability_ids": [],
                "missing_capability_ids": list(missing_capabilities),
                "clarification_questions": [],
                "constraints": ["Planner may only use registered domain packs and capabilities."],
                "fallback_hints": [],
                "assumptions": [],
                "subgoals": subgoals,
                "message": "no registered domain can satisfy the request",
            }
            self._last_raw_output = str(output)
            return output

        if analysis.type == "mixed":
            assumptions.append("Conversational context was separated from the executable subgoal.")
        if analysis.type == "task" and not required_capability_ids:
            assumptions.append("No direct single capability was committed during synthesis; route builders will refine the executable step.")

        output = {
            "status": "ready",
            "goal_type": goal_type_for_analysis(candidate_domain_ids, required_capability_ids),
            "candidate_domain_ids": candidate_domain_ids,
            "required_capability_ids": required_capability_ids,
            "missing_capability_ids": [],
            "clarification_questions": [],
            "constraints": ["Planner must use registered domains, routes, and capability families only."],
            "fallback_hints": fallback_hints,
            "assumptions": assumptions,
            "assistant_intent": assistant_intent_to_payload(synthesized_assistant_intent),
            "subgoals": subgoals,
            "message": "goal synthesis completed",
        }
        self._last_raw_output = str(output)
        return output


class OpenAICompatibleGoalSynthesisProvider(GoalSynthesisProvider):
    provider_version = "v0.8"

    def __init__(self, intent_broker: IntentBroker | None = None, fallback: GoalSynthesisProvider | None = None) -> None:
        self.intent_broker = intent_broker or RuleIntentBroker()
        self.fallback = fallback or RuleBasedGoalSynthesisProvider(self.intent_broker)
        self.config = load_openai_compatible_provider_config()
        self.provider_name = self.config.provider_name
        self.model_name = self.config.model_name or "unknown-model"

    def synthesize(self, utterance: str, analysis: UtteranceAnalysis) -> dict[str, object]:
        if not self.config.configured or not model_guidance_enabled("VIBEOS_ENABLE_MODEL_GOAL_SYNTHESIS"):
            return self._fallback(utterance, analysis, error="missing_api_key_or_model_or_guidance_disabled")

        host_hint = build_goal_synthesis_boundary_hint(utterance=utterance, analysis=analysis, intent_broker=self.intent_broker)
        request_payload = build_goal_synthesis_request_payload(utterance=utterance, analysis=analysis, host_hint=host_hint)
        try:
            response = request_json_object(
                config=self.config,
                system_prompt=GOAL_SYNTHESIS_SYSTEM_PROMPT,
                user_content=json.dumps(request_payload, ensure_ascii=False),
                max_tokens=768,
            )
            parsed = response.parsed_object
            validated = validate_goal_synthesis_payload(parsed, host_hint=host_hint)
            self._last_parse_valid = True
            self._last_fallback_used = False
            self._last_error = None
            self._last_raw_output = json.dumps(response.response_payload, ensure_ascii=False)
            return validated
        except (urllib.error.URLError, TimeoutError, KeyError, ValueError, json.JSONDecodeError) as exc:
            return self._fallback(utterance, analysis, error=str(exc))

    def _fallback(self, utterance: str, analysis: UtteranceAnalysis, *, error: str) -> dict[str, object]:
        payload = self.fallback.synthesize(utterance, analysis)
        self._last_parse_valid = False
        self._last_fallback_used = True
        self._last_error = error
        self._last_raw_output = str(payload)
        return payload


class GoalSynthesizer:
    def __init__(self, provider: GoalSynthesisProvider | None = None) -> None:
        self.provider = provider or OpenAICompatibleGoalSynthesisProvider()

    def synthesize(self, utterance: str, analysis: UtteranceAnalysis, *, understanding_id: str | None = None) -> GoalSynthesisResult:
        normalized = self.provider.synthesize(utterance, analysis)
        metadata = self.provider.response_metadata()
        exchange = ProviderExchange(
            provider_name=self.provider.provider_name,
            model_name=self.provider.model_name,
            normalized_output=normalized,
            raw_output=str(metadata.get("raw_output", str(normalized))),
            parse_valid=bool(metadata.get("parse_valid", True)),
            fallback_used=bool(metadata.get("fallback_used", False)),
            error=str(metadata.get("error")) if metadata.get("error") is not None else None,
        )
        record_model_io(
            phase="goal_synthesis",
            provider=exchange.provider_name,
            model=exchange.model_name,
            request_payload={"utterance": utterance, "analysis": asdict(analysis)},
            response_payload={"raw_output": exchange.raw_output},
            normalized_output=exchange.normalized_output,
            parse_valid=exchange.parse_valid,
            fallback_used=exchange.fallback_used,
            error=exchange.error,
            actor="goal_synthesizer",
            call_kind="structured_followup",
            consumed_artifacts={"understanding_id": understanding_id},
        )
        try:
            status = str(normalized.get("status", "unsupported"))
            goal_type = str(normalized.get("goal_type", "unsupported"))
            subgoals = tuple(
                GoalSubgoal(
                    subgoal_id=str(item.get("subgoal_id", "")),
                    text=str(item.get("text", "")),
                    goal_type=str(item.get("goal_type", "")),
                    candidate_domain_ids=tuple(str(value) for value in item.get("candidate_domain_ids", ())),
                    required_capability_ids=tuple(str(value) for value in item.get("required_capability_ids", ())),
                )
                for item in normalized.get("subgoals", ())
            )
            goal_spec = GoalSpec(
                goal_id=make_goal_id(utterance, status, goal_type),
                goal_text=utterance.strip(),
                goal_type=goal_type,
                source_understanding_id=understanding_id,
                subgoals=subgoals,
                candidate_domain_ids=tuple(str(item) for item in normalized.get("candidate_domain_ids", ())),
                required_capability_ids=tuple(str(item) for item in normalized.get("required_capability_ids", ())),
                missing_capability_ids=tuple(str(item) for item in normalized.get("missing_capability_ids", ())),
                clarification_questions=tuple(str(item) for item in normalized.get("clarification_questions", ())),
                constraints=tuple(str(item) for item in normalized.get("constraints", ())),
                fallback_hints=tuple(str(item) for item in normalized.get("fallback_hints", ())),
                assumptions=tuple(str(item) for item in normalized.get("assumptions", ())),
                assistant_intent=assistant_intent_from_payload(normalized.get("assistant_intent") if isinstance(normalized.get("assistant_intent"), dict) else None),
                synthesis_provenance=GoalSynthesisProvenance(
                    provider_name=self.provider.provider_name,
                    provider_version=self.provider.provider_version,
                    model_name=self.provider.model_name,
                    fallback_used=exchange.fallback_used,
                    parse_valid=exchange.parse_valid,
                    error=exchange.error,
                ),
            )
            return GoalSynthesisResult(
                status=status,  # type: ignore[arg-type]
                goal_spec=goal_spec,
                message=str(normalized.get("message", "")),
                exchange=exchange,
            )
        except Exception as exc:
            fallback = {
                "status": "unsupported",
                "message": f"goal synthesis payload validation failed: {exc}",
            }
            record_model_io(
                phase="goal_synthesis",
                provider=self.provider.provider_name,
                model=self.provider.model_name,
                request_payload={"utterance": utterance, "analysis": asdict(analysis)},
                response_payload={"raw_output": str(normalized)},
                normalized_output=fallback,
                parse_valid=False,
                fallback_used=True,
                error=str(exc),
                actor="goal_synthesizer",
                call_kind="structured_followup",
                consumed_artifacts={"understanding_id": understanding_id},
            )
            return GoalSynthesisResult(
                status="unsupported",
                goal_spec=None,
                message=str(fallback["message"]),
                exchange=ProviderExchange(
                    provider_name=self.provider.provider_name,
                    model_name=self.provider.model_name,
                    normalized_output=fallback,
                    raw_output=str(normalized),
                    parse_valid=False,
                    fallback_used=True,
                    error=str(exc),
                ),
            )


def infer_action_for_span(span: TaskSpan, intent_broker: IntentBroker) -> str:
    lowered = span.text.strip().lower()
    if lowered.startswith("open http://") or lowered.startswith("open https://") or span.text.startswith("打开 http://") or span.text.startswith("打开 https://"):
        return "browser.open_url"
    if lowered.startswith("search web for ") or span.text.startswith("搜索 "):
        return "browser.search_web"
    if lowered.startswith("search ") and " for " in lowered:
        return "browser.open_site_search"
    if span.domain == "media":
        if lowered.startswith(("search media for ", "search music for ", "find media ", "find music ")):
            return "media.search"
        if "pause" in lowered:
            return "media.pause"
        return "media.play"
    intent = intent_broker.parse(span.text)
    return intent.action


def infer_missing_capabilities(utterance: str) -> tuple[str, ...]:
    lowered = utterance.lower()
    if "email" in lowered or "mail" in lowered:
        return ("email.send",)
    if "calendar" in lowered:
        return ("calendar.create_event",)
    if "file" in lowered or "folder" in lowered or "pdf" in lowered:
        return ("file.read",)
    if "terminal" in lowered or "shell" in lowered:
        return ("shell.execute",)
    return ()


def goal_type_for_analysis(candidate_domain_ids: list[str], required_capability_ids: list[str]) -> str:
    if required_capability_ids:
        return required_capability_ids[0].replace(".", "_")
    if candidate_domain_ids:
        return candidate_domain_ids[0]
    return "unsupported"


def goal_type_for_action(action: str, domain_id: str) -> str:
    if action and action != "unknown":
        return action.replace(".", "_")
    if domain_id:
        return domain_id
    return "unsupported"


def make_goal_id(utterance: str, status: str, goal_type: str) -> str:
    digest = sha256(f"{status}:{goal_type}:{utterance.strip()}".encode("utf-8")).hexdigest()[:12]
    return f"goal_{digest}"


def synthesize_assistant_intent(
    utterance: str,
    analysis: UtteranceAnalysis,
    intent_broker: IntentBroker | None = None,
) -> AssistantIntent | None:
    stripped = utterance.strip()
    broker = intent_broker or RuleIntentBroker()
    cached_intent = cached_provider_intent(broker, stripped)
    if cached_intent is not None:
        structured = _assistant_intent_from_structured_intent(stripped, cached_intent)
        if structured is not None:
            return structured

    intent = broker.parse(stripped)
    structured = _assistant_intent_from_structured_intent(stripped, intent)
    if structured is not None:
        return structured

    browser_intent = infer_browser_intent_from_open_request(stripped)
    if browser_intent is not None:
        structured = _assistant_intent_from_structured_intent(stripped, browser_intent)
        if structured is not None:
            return structured

    app_history_target = extract_app_history_search_target(stripped)
    if app_history_target is not None:
        app_name = app_history_target["app"]
        query = app_history_target["query"]
        return AssistantIntent(
            objective_kind="in_app_search",
            target=AssistantIntentTarget(
                entity_type=app_history_target.get("scope") or "app_content",
                display_name=query,
                app_name=app_name,
                query_text=query,
            ),
            completion=AssistantCompletionSemantics(
                kind="target_presence",
                success_signal="the requested target appears in observed in-app search results",
            ),
            interaction_hints=("structured-search", "shortcut-fallback"),
            preferred_domains=("app_interaction",),
        )
    if analysis.domains == ("browser",) and "search" in stripped.lower():
        return AssistantIntent(
            objective_kind="search_web",
            target=AssistantIntentTarget(entity_type="search_query", display_name=stripped, query_text=stripped),
            completion=AssistantCompletionSemantics(
                kind="search_results",
                success_signal="the requested search query is observed in browser state",
                allows_intermediate_success=True,
            ),
            interaction_hints=("lookup",),
            preferred_domains=("browser",),
        )
    return None


def _assistant_intent_from_structured_intent(utterance: str, intent) -> AssistantIntent | None:
    action = str(getattr(intent, "action", "") or "").strip()
    if not action or action == "unknown":
        return None
    if action == "browser.open_url":
        uri = str(intent.target.get("uri") or intent.target.get("url") or "").strip()
        if not uri:
            return None
        return AssistantIntent(
            objective_kind="open_url",
            target=AssistantIntentTarget(
                entity_type="website",
                display_name=extract_open_target(utterance) or uri,
                canonical_identifier=uri,
            ),
            completion=AssistantCompletionSemantics(
                kind="page_identity",
                success_signal="final browser page identity matches the requested URL",
            ),
            interaction_hints=("direct-open",),
            preferred_domains=("browser",),
        )
    if action == "browser.search_web":
        query = str(intent.target.get("query") or "").strip() or utterance.strip()
        target_name = extract_open_target(utterance)
        if target_name or _looks_like_named_website_query(query):
            display_name = target_name or query
            return AssistantIntent(
                objective_kind="open_named_website",
                target=AssistantIntentTarget(
                    entity_type="website",
                    display_name=display_name,
                    canonical_identifier=normalize_bare_domain_uri(display_name),
                    query_text=query,
                ),
                completion=AssistantCompletionSemantics(
                    kind="page_identity",
                    success_signal="the final browser page identity matches the intended official site",
                    requires_follow_up_navigation=True,
                    allows_intermediate_success=False,
                ),
                interaction_hints=("direct-open", "lookup", "follow-up-navigation"),
                preferred_domains=("browser",),
            )
        return AssistantIntent(
            objective_kind="search_web",
            target=AssistantIntentTarget(entity_type="search_query", display_name=query, query_text=query),
            completion=AssistantCompletionSemantics(
                kind="search_results",
                success_signal="the requested search query is observed in browser state",
                allows_intermediate_success=True,
            ),
            interaction_hints=("lookup",),
            preferred_domains=("browser",),
        )
    if action == "browser.open_site_search":
        site = str(intent.target.get("site") or "").strip()
        query = str(intent.target.get("query") or "").strip()
        if not site or not query:
            return None
        return AssistantIntent(
            objective_kind="search_web",
            target=AssistantIntentTarget(
                entity_type="search_query",
                display_name=query,
                query_text=query,
                metadata={"site": site},
            ),
            completion=AssistantCompletionSemantics(
                kind="search_results",
                success_signal="the requested site-scoped search results are observed in browser state",
                allows_intermediate_success=True,
            ),
            interaction_hints=("lookup",),
            preferred_domains=("browser",),
        )
    if action == "app.search_history":
        app_name = str(intent.target.get("app") or intent.target.get("name") or "").strip()
        query = str(intent.target.get("query") or "").strip()
        if not app_name or not query:
            return None
        return AssistantIntent(
            objective_kind="in_app_search",
            target=AssistantIntentTarget(
                entity_type="app_content",
                display_name=query,
                app_name=app_name,
                query_text=query,
            ),
            completion=AssistantCompletionSemantics(
                kind="target_presence",
                success_signal="the requested target appears in observed in-app search results",
            ),
            interaction_hints=("structured-search", "shortcut-fallback"),
            preferred_domains=("app_interaction",),
        )
    if action == "app.open":
        app_name = str(intent.target.get("name") or intent.target.get("app") or "").strip()
        if not app_name:
            return None
        return AssistantIntent(
            objective_kind="open_application",
            target=AssistantIntentTarget(entity_type="application", display_name=app_name, app_name=app_name),
            completion=AssistantCompletionSemantics(
                kind="application_state",
                success_signal="the requested application is opened or focused",
            ),
            interaction_hints=("native-open",),
            preferred_domains=("apps",),
        )
    return None


def _looks_like_named_website_query(query: str) -> bool:
    lowered = query.lower()
    return any(hint in query for hint in WEB_NAMED_TARGET_HINTS[:3]) or any(
        hint in lowered for hint in WEB_NAMED_TARGET_HINTS[3:]
    )


def goal_synthesis_payload(result: GoalSynthesisResult) -> dict[str, object]:
    payload = {
        "status": result.status,
        "message": result.message,
        "exchange": asdict(result.exchange),
    }
    if result.goal_spec is not None:
        payload["goal_spec"] = asdict(result.goal_spec)
    else:
        payload["goal_spec"] = None
    return payload


def build_goal_synthesis_request_payload(*, utterance: str, analysis: UtteranceAnalysis, host_hint: dict[str, object]) -> dict[str, object]:
    return {
        "utterance": utterance,
        "analysis": asdict(analysis),
        "host_hint": host_hint,
        "allowed_statuses": ["ready", "clarification_needed", "missing_capability", "unsupported"],
        "allowed_candidate_domain_ids": list(host_hint.get("candidate_domain_ids", [])) if isinstance(host_hint.get("candidate_domain_ids"), list) else [],
        "allowed_required_capability_ids": list(host_hint.get("required_capability_ids", [])) if isinstance(host_hint.get("required_capability_ids"), list) else [],
        "allowed_missing_capability_ids": list(host_hint.get("missing_capability_ids", [])) if isinstance(host_hint.get("missing_capability_ids"), list) else [],
    }


def validate_goal_synthesis_payload(payload: dict[str, object], *, host_hint: dict[str, object]) -> dict[str, object]:
    normalized = dict(payload)
    if not normalized.get("goal_type") and isinstance(normalized.get("type"), str):
        normalized["goal_type"] = normalized["type"]
    if not isinstance(normalized.get("candidate_domain_ids"), list):
        normalized_domain = normalized.get("domain_id") or normalized.get("domain")
        if isinstance(normalized_domain, str) and normalized_domain.strip():
            normalized["candidate_domain_ids"] = [normalized_domain.strip()]
    if not isinstance(normalized.get("required_capability_ids"), list):
        normalized_capability = normalized.get("capability_id") or normalized.get("capability")
        if isinstance(normalized_capability, str) and normalized_capability.strip():
            normalized["required_capability_ids"] = [normalized_capability.strip()]
    status = str(normalized.get("status") or "").strip()
    if status not in {"ready", "clarification_needed", "missing_capability", "unsupported"}:
        raise ValueError("goal synthesis status is invalid")
    candidate_domain_ids = list(normalized.get("candidate_domain_ids", [])) if isinstance(normalized.get("candidate_domain_ids"), list) else []
    required_capability_ids = list(normalized.get("required_capability_ids", [])) if isinstance(normalized.get("required_capability_ids"), list) else []
    missing_capability_ids = list(normalized.get("missing_capability_ids", [])) if isinstance(normalized.get("missing_capability_ids"), list) else []

    allowed_domains = set(str(item) for item in host_hint.get("candidate_domain_ids", [])) if isinstance(host_hint.get("candidate_domain_ids"), list) else set()
    allowed_required_capabilities = set(str(item) for item in host_hint.get("required_capability_ids", [])) if isinstance(host_hint.get("required_capability_ids"), list) else set()
    allowed_missing_capabilities = set(str(item) for item in host_hint.get("missing_capability_ids", [])) if isinstance(host_hint.get("missing_capability_ids"), list) else set()

    if any(str(item) not in allowed_domains for item in candidate_domain_ids):
        raise ValueError("candidate_domain_ids exceeded host-owned domain boundary")
    if any(str(item) not in allowed_required_capabilities for item in required_capability_ids):
        raise ValueError("required_capability_ids exceeded host-owned capability boundary")
    if any(str(item) not in allowed_missing_capabilities for item in missing_capability_ids):
        raise ValueError("missing_capability_ids exceeded host-owned capability boundary")
    normalized["candidate_domain_ids"] = candidate_domain_ids
    normalized["required_capability_ids"] = required_capability_ids
    normalized["missing_capability_ids"] = missing_capability_ids
    return normalized


def model_guidance_enabled(env_name: str) -> bool:
    return env_flag_enabled(env_name)


def build_goal_synthesis_boundary_hint(
    *,
    utterance: str,
    analysis: UtteranceAnalysis,
    intent_broker: IntentBroker | None = None,
) -> dict[str, object]:
    candidate_domain_ids = [str(item) for item in analysis.domains if str(item)]
    required_capability_ids = _goal_synthesis_required_capability_boundaries(analysis, intent_broker=intent_broker, utterance=utterance)
    missing_capability_ids = list(infer_missing_capabilities(utterance)) if analysis.type == "rejected" else []
    status = "ready"
    message = analysis.explanation or "goal synthesis boundary ready"
    clarification_questions: list[str] = []
    if analysis.type == "clarification":
        status = "clarification_needed"
        clarification_questions = [analysis.chat_response or analysis.explanation or "Please clarify the request."]
    elif analysis.type == "rejected":
        status = "missing_capability" if missing_capability_ids else "unsupported"
    return {
        "status": status,
        "goal_type": _goal_type_boundary_hint(analysis=analysis, required_capability_ids=required_capability_ids),
        "candidate_domain_ids": candidate_domain_ids,
        "required_capability_ids": required_capability_ids,
        "missing_capability_ids": missing_capability_ids,
        "clarification_questions": clarification_questions,
        "constraints": ["Planner must use registered domains, routes, and capability families only."],
        "fallback_hints": [],
        "assumptions": [],
        "assistant_intent": None,
        "subgoals": [],
        "message": message,
    }


def _goal_synthesis_required_capability_boundaries(
    analysis: UtteranceAnalysis,
    *,
    intent_broker: IntentBroker | None,
    utterance: str,
) -> list[str]:
    cached_intent = cached_provider_intent(intent_broker, utterance)
    if cached_intent is not None and cached_intent.action and cached_intent.action != "unknown":
        return [cached_intent.action]
    capability_ids: list[str] = []
    for domain_id in analysis.domains:
        capability_ids.extend(DOMAIN_CAPABILITY_BOUNDARIES.get(domain_id, ()))
    return list(dict.fromkeys(capability_ids))


def _goal_type_boundary_hint(*, analysis: UtteranceAnalysis, required_capability_ids: list[str]) -> str:
    if required_capability_ids:
        return required_capability_ids[0].replace(".", "_")
    if analysis.domains:
        return analysis.domains[0]
    return "unsupported"


def cached_provider_intent(intent_broker: IntentBroker | None, utterance: str):
    cached_fn = getattr(intent_broker, "cached_intent", None)
    if callable(cached_fn):
        return cached_fn(utterance)
    return None


DOMAIN_CAPABILITY_BOUNDARIES: dict[str, tuple[str, ...]] = {
    "apps": ("app.open", "app.list"),
    "app_interaction": ("app.search_history",),
    "browser": ("browser.open_url", "browser.search_web", "browser.open_site_search"),
    "clipboard": ("clipboard.write",),
    "media": ("media.play", "media.search", "media.pause"),
    "notification": ("notification.send",),
    "system_observation": ("system.status",),
    "window_management": ("window.list", "window.focus", "window.minimize", "window.maximize", "window.close"),
}
