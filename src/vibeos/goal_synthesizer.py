from __future__ import annotations

from dataclasses import asdict
from hashlib import sha256

from .capabilities import CAPABILITIES
from .goal_models import GoalSpec, GoalSubgoal, GoalSynthesisProvenance, GoalSynthesisResult, ProviderExchange
from .intent import IntentBroker, RuleIntentBroker
from .nlu import domain_for_action
from .task_models import TaskSpan, UtteranceAnalysis


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
