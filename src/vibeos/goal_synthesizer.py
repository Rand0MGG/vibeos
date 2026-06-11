from __future__ import annotations

from dataclasses import asdict
from hashlib import sha256
import re

from .assistant_semantics import (
    AssistantCompletionSemantics,
    AssistantIntent,
    AssistantIntentTarget,
    assistant_intent_from_payload,
    assistant_intent_to_payload,
)
from .capabilities import CAPABILITIES
from .goal_models import GoalSpec, GoalSubgoal, GoalSynthesisProvenance, GoalSynthesisResult, ProviderExchange
from .intent import IntentBroker, RuleIntentBroker, extract_open_target, infer_browser_intent_from_open_request, normalize_bare_domain_uri
from .nlu import domain_for_action
from .task_models import TaskSpan, UtteranceAnalysis


APP_HISTORY_SEARCH_RE = re.compile(
    r"^search\s+(?:(?P<scope>chat history)\s+)?in\s+(?P<app>.+?)\s+for\s+(?P<query>.+)$",
    re.IGNORECASE,
)


class GoalSynthesisProvider:
    provider_name = "provider"
    provider_version = "v0"
    model_name = "structured"

    def synthesize(self, utterance: str, analysis: UtteranceAnalysis) -> dict[str, object]:
        raise NotImplementedError


class RuleBasedGoalSynthesisProvider(GoalSynthesisProvider):
    provider_name = "rule_goal_synthesizer"
    provider_version = "v0.5"
    model_name = "deterministic-local"

    def __init__(self, intent_broker: IntentBroker | None = None) -> None:
        self.intent_broker = intent_broker or RuleIntentBroker()

    def synthesize(self, utterance: str, analysis: UtteranceAnalysis) -> dict[str, object]:
        synthesized_assistant_intent = synthesize_assistant_intent(utterance, analysis)
        if synthesized_assistant_intent is not None and synthesized_assistant_intent.objective_kind == "in_app_search":
            return {
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

        if analysis.type == "clarification":
            return {
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

        if analysis.type == "rejected":
            missing_capabilities = infer_missing_capabilities(utterance)
            status = "missing_capability" if missing_capabilities else "unsupported"
            return {
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
            return {
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

        if analysis.type == "mixed":
            assumptions.append("Conversational context was separated from the executable subgoal.")
        if analysis.type == "task" and not required_capability_ids:
            assumptions.append("No direct single capability was committed during synthesis; route builders will refine the executable step.")

        return {
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


class GoalSynthesizer:
    def __init__(self, provider: GoalSynthesisProvider | None = None) -> None:
        self.provider = provider or RuleBasedGoalSynthesisProvider()

    def synthesize(self, utterance: str, analysis: UtteranceAnalysis) -> GoalSynthesisResult:
        normalized = self.provider.synthesize(utterance, analysis)
        exchange = ProviderExchange(
            provider_name=self.provider.provider_name,
            model_name=self.provider.model_name,
            normalized_output=normalized,
            raw_output=str(normalized),
            parse_valid=True,
            fallback_used=False,
            error=None,
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
                    fallback_used=False,
                    parse_valid=True,
                    error=None,
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
    intent = RuleIntentBroker().parse(span.text)
    if intent.action != "unknown":
        return intent.action
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


def synthesize_assistant_intent(utterance: str, analysis: UtteranceAnalysis) -> AssistantIntent | None:
    stripped = utterance.strip()
    browser_intent = infer_browser_intent_from_open_request(stripped)
    if browser_intent is not None:
        if browser_intent.action == "browser.open_url":
            uri = str(browser_intent.target.get("uri") or "")
            return AssistantIntent(
                objective_kind="open_url",
                target=AssistantIntentTarget(
                    entity_type="website",
                    display_name=extract_open_target(stripped) or uri,
                    canonical_identifier=uri,
                ),
                completion=AssistantCompletionSemantics(
                    kind="page_identity",
                    success_signal="final browser page identity matches the requested URL",
                ),
                interaction_hints=("direct-open",),
                preferred_domains=("browser",),
            )
        target_name = extract_open_target(stripped) or stripped
        return AssistantIntent(
            objective_kind="open_named_website",
            target=AssistantIntentTarget(
                entity_type="website",
                display_name=target_name,
                canonical_identifier=normalize_bare_domain_uri(target_name),
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

    app_match = APP_HISTORY_SEARCH_RE.match(stripped)
    if app_match:
        app_name = app_match.group("app").strip()
        query = app_match.group("query").strip()
        return AssistantIntent(
            objective_kind="in_app_search",
            target=AssistantIntentTarget(
                entity_type=app_match.group("scope") or "app_content",
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

    rule_intent = RuleIntentBroker().parse(stripped)
    if rule_intent.action == "app.open":
        app_name = str(rule_intent.target.get("name") or "")
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
