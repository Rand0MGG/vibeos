from __future__ import annotations

from .intent import IntentBroker, OpenAICompatibleIntentBroker
from .models import Intent
from .task_models import ParseProvenance, SourceSpan, TaskSpan, UtteranceAnalysis


def analyze_utterance(utterance: str, intent_broker: IntentBroker | None = None) -> UtteranceAnalysis:
    broker = intent_broker or OpenAICompatibleIntentBroker()
    stripped = utterance.strip()
    if not stripped:
        return UtteranceAnalysis(
            utterance=utterance,
            type="clarification",
            confidence=1.0,
            domains=(),
            explanation="The request is empty.",
            task_spans=(),
            provenance=None,
            chat_response="Please provide a task.",
        )

    intent = broker.parse(stripped)
    if intent.action != "unknown":
        return analysis_from_intent(stripped, intent, confidence=0.88, provenance_parser="provider_capability_analysis")

    return UtteranceAnalysis(
        utterance=utterance,
        type="rejected",
        confidence=0.0,
        domains=(),
        explanation=intent.reason or "Unsupported request.",
        task_spans=(),
        provenance=None,
        chat_response=None,
    )


def analysis_from_intent(
    utterance: str,
    intent: Intent,
    *,
    confidence: float,
    provenance_parser: str,
) -> UtteranceAnalysis:
    domain = domain_for_action(intent.action)
    span = TaskSpan(
        id="span_1",
        text=utterance,
        start=0,
        end=len(utterance),
        domain=domain,
        confidence=confidence,
    )
    return UtteranceAnalysis(
        utterance=utterance,
        type="task",
        confidence=confidence,
        domains=(domain,),
        explanation=f"Structured capability analysis resolved {intent.action}.",
        task_spans=(span,),
        provenance=make_provenance(utterance, provenance_parser, confidence),
        chat_response=None,
    )


def domain_for_action(action: str) -> str:
    if action == "app.search_history":
        return "app_interaction"
    if action.startswith("browser.") or action == "portal.open_uri":
        return "browser"
    if action.startswith("media."):
        return "media"
    if action.startswith("window."):
        return "window_management"
    if action.startswith("app."):
        return "apps"
    if action == "clipboard.write":
        return "clipboard"
    if action == "notification.send":
        return "notification"
    return "system_observation"


def make_provenance(text: str, parser: str, confidence: float) -> ParseProvenance:
    return ParseProvenance(
        parser=parser,
        parser_version="v0.5",
        source_span=SourceSpan(start=0, end=len(text), text=text),
        confidence=confidence,
        model=None,
        schema_version="v0.5",
        repair_applied=False,
    )
