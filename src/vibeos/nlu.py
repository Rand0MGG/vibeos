from __future__ import annotations

from .intent import IntentBroker, OpenAICompatibleIntentBroker, RuleIntentBroker, infer_browser_intent_from_open_request
from .task_models import ParseProvenance, SourceSpan, TaskSpan, UtteranceAnalysis


CHAT_TERMS = ("how should", "what do you think", "design", "\u4f60\u89c9\u5f97", "\u600e\u4e48\u770b")
MIXED_MARKERS = ("and then", "then", "\u7136\u540e")
MEDIA_PREFIXES = ("play ", "listen to ", "\u6211\u60f3\u542c ", "\u64ad\u653e ", "\u653e\u4e00\u9996 ")
MEDIA_SEARCH_PREFIXES = ("search media for ", "search music for ", "find media ", "find music ")
MEDIA_PAUSE_PREFIXES = ("pause", "pause music", "pause playback")
CLARIFICATION_TERMS = {"play", "\u64ad\u653e"}
BROWSER_URL_PREFIXES = ("open https://", "\u6253\u5f00 https://")
BROWSER_SEARCH_PREFIXES = ("search web for ", "\u641c\u7d22 ")


def analyze_utterance(utterance: str, intent_broker: IntentBroker | None = None) -> UtteranceAnalysis:
    broker = intent_broker or OpenAICompatibleIntentBroker()
    stripped = utterance.strip()
    lowered = stripped.lower()

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

    if is_dangerous_imperative(stripped, lowered):
        return UtteranceAnalysis(
            utterance=utterance,
            type="rejected",
            confidence=1.0,
            domains=(),
            explanation="The request is outside the supported safe capability scope.",
            task_spans=(),
            provenance=make_provenance(stripped, "rule_fast_path", 1.0),
            chat_response=None,
        )

    mixed = analyze_mixed_request(stripped)
    if mixed is not None:
        return mixed

    if any(term in lowered for term in CHAT_TERMS) or any(term in stripped for term in ("\u4f60\u89c9\u5f97", "\u600e\u4e48\u770b")):
        return UtteranceAnalysis(
            utterance=utterance,
            type="chat",
            confidence=0.95,
            domains=(),
            explanation="The utterance asks for discussion rather than an executable desktop task.",
            task_spans=(),
            provenance=make_provenance(stripped, "rule_fast_path", 0.95),
            chat_response=None,
        )

    if lowered in CLARIFICATION_TERMS or stripped in {"\u64ad\u653e", "\u6211\u60f3\u542c"}:
        return UtteranceAnalysis(
            utterance=utterance,
            type="clarification",
            confidence=1.0,
            domains=("media",),
            explanation="The utterance indicates media playback but does not provide a query.",
            task_spans=(),
            provenance=make_provenance(stripped, "rule_fast_path", 1.0),
            chat_response="What would you like to play?",
        )

    browser_analysis = analyze_browser_request(stripped)
    if browser_analysis is not None:
        return browser_analysis

    media_analysis = analyze_media_request(stripped)
    if media_analysis is not None:
        return media_analysis

    capability_intent = RuleIntentBroker().parse(stripped)
    if capability_intent.action != "unknown":
        span = TaskSpan(
            id="span_1",
            text=stripped,
            start=0,
            end=len(stripped),
            domain=domain_for_action(capability_intent.action),
            confidence=1.0,
        )
        return UtteranceAnalysis(
            utterance=utterance,
            type="task",
            confidence=1.0,
            domains=(span.domain,),
            explanation=f"Structured capability analysis resolved {capability_intent.action}.",
            task_spans=(span,),
            provenance=make_provenance(stripped, "rule_capability_analysis", 1.0),
            chat_response=None,
        )

    intent = broker.parse(stripped)
    if intent.action == "unknown":
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

    span = TaskSpan(
        id="span_1",
        text=stripped,
        start=0,
        end=len(stripped),
        domain=domain_for_action(intent.action),
        confidence=0.8,
    )
    return UtteranceAnalysis(
        utterance=utterance,
        type="task",
        confidence=0.8,
        domains=(span.domain,),
        explanation=f"Provider capability analysis resolved {intent.action}.",
        task_spans=(span,),
        provenance=make_provenance(stripped, "provider_capability_analysis", 0.8),
        chat_response=None,
    )


def is_dangerous_imperative(stripped: str, lowered: str) -> bool:
    dangerous_prefixes = (
        "delete ",
        "remove ",
        "rm ",
        "\u5220\u9664",
        "\u5220\u6389",
        "install ",
        "\u5b89\u88c5",
        "sudo ",
        "shell ",
    )
    return any(lowered.startswith(prefix) or stripped.startswith(prefix) for prefix in dangerous_prefixes)


def analyze_mixed_request(utterance: str) -> UtteranceAnalysis | None:
    lowered = utterance.lower()
    for marker in MIXED_MARKERS:
        marker_lower = marker.lower()
        if marker_lower not in lowered:
            continue
        split_index = lowered.find(marker_lower)
        left = utterance[:split_index].strip(" ,\uff0c")
        right = utterance[split_index + len(marker) :].strip(" ,\uff0c")
        if not right:
            return None
        task_span = TaskSpan(
            id="span_1",
            text=right,
            start=split_index + len(marker),
            end=split_index + len(marker) + len(right),
            domain=infer_domain_for_text(right),
            confidence=0.9,
        )
        return UtteranceAnalysis(
            utterance=utterance,
            type="mixed",
            confidence=0.9,
            domains=(task_span.domain,),
            explanation="The utterance contains both discussion and an executable task.",
            task_spans=(task_span,),
            provenance=make_provenance(utterance, "rule_fast_path", 0.9),
            chat_response=left or "The first clause is conversational context.",
        )
    return None


def analyze_browser_request(utterance: str) -> UtteranceAnalysis | None:
    lowered = utterance.lower()
    if lowered.startswith(("search media for ", "search music for ", "find media ", "find music ")):
        return None
    if lowered.startswith(BROWSER_URL_PREFIXES[0]) or utterance.startswith(BROWSER_URL_PREFIXES[1]):
        span = TaskSpan(id="span_1", text=utterance, start=0, end=len(utterance), domain="browser", confidence=0.95)
        return UtteranceAnalysis(
            utterance=utterance,
            type="task",
            confidence=0.95,
            domains=("browser",),
            explanation="The utterance requests opening a browser URL.",
            task_spans=(span,),
            provenance=make_provenance(utterance, "rule_fast_path", 0.95),
            chat_response=None,
        )
    browser_open_intent = infer_browser_intent_from_open_request(utterance)
    if browser_open_intent is not None:
        explanation = (
            "The utterance requests opening a browser-resolved URL."
            if browser_open_intent.action == "browser.open_url"
            else "The utterance requests opening a website by name in the browser."
        )
        span = TaskSpan(id="span_1", text=utterance, start=0, end=len(utterance), domain="browser", confidence=0.9)
        return UtteranceAnalysis(
            utterance=utterance,
            type="task",
            confidence=0.9,
            domains=("browser",),
            explanation=explanation,
            task_spans=(span,),
            provenance=make_provenance(utterance, "rule_fast_path", 0.9),
            chat_response=None,
        )
    if lowered.startswith(BROWSER_SEARCH_PREFIXES[0]) or utterance.startswith(BROWSER_SEARCH_PREFIXES[1]):
        query = utterance[len(BROWSER_SEARCH_PREFIXES[0]) :].strip() if lowered.startswith(BROWSER_SEARCH_PREFIXES[0]) else utterance[len(BROWSER_SEARCH_PREFIXES[1]) :].strip()
        if not query:
            return UtteranceAnalysis(
                utterance=utterance,
                type="clarification",
                confidence=1.0,
                domains=("browser",),
                explanation="The utterance indicates a browser search but does not provide a query.",
                task_spans=(),
                provenance=make_provenance(utterance, "rule_fast_path", 1.0),
                chat_response="What would you like to search for?",
            )
        span = TaskSpan(id="span_1", text=utterance, start=0, end=len(utterance), domain="browser", confidence=0.95)
        return UtteranceAnalysis(
            utterance=utterance,
            type="task",
            confidence=0.95,
            domains=("browser",),
            explanation="The utterance requests a browser search.",
            task_spans=(span,),
            provenance=make_provenance(utterance, "rule_fast_path", 0.95),
            chat_response=None,
        )
    if lowered.startswith("search ") and " for " in lowered:
        span = TaskSpan(id="span_1", text=utterance, start=0, end=len(utterance), domain="browser", confidence=0.95)
        return UtteranceAnalysis(
            utterance=utterance,
            type="task",
            confidence=0.95,
            domains=("browser",),
            explanation="The utterance requests a site-scoped browser search.",
            task_spans=(span,),
            provenance=make_provenance(utterance, "rule_fast_path", 0.95),
            chat_response=None,
        )
    return None


def analyze_media_request(utterance: str) -> UtteranceAnalysis | None:
    lowered = utterance.lower()
    for prefix in MEDIA_SEARCH_PREFIXES:
        if lowered.startswith(prefix):
            query = utterance[len(prefix) :].strip()
            if not query:
                return UtteranceAnalysis(
                    utterance=utterance,
                    type="clarification",
                    confidence=1.0,
                    domains=("media",),
                    explanation="The utterance indicates a media search but does not provide a query.",
                    task_spans=(),
                    provenance=make_provenance(utterance, "rule_fast_path", 1.0),
                    chat_response="What would you like to search for?",
                )
            span = TaskSpan(id="span_1", text=utterance, start=0, end=len(utterance), domain="media", confidence=0.9)
            return UtteranceAnalysis(
                utterance=utterance,
                type="task",
                confidence=0.9,
                domains=("media",),
                explanation="The utterance expresses a media search goal.",
                task_spans=(span,),
                provenance=make_provenance(utterance, "rule_fast_path", 0.9),
                chat_response=None,
            )
    for prefix in MEDIA_PAUSE_PREFIXES:
        if lowered == prefix or lowered.startswith(prefix + " "):
            span = TaskSpan(id="span_1", text=utterance, start=0, end=len(utterance), domain="media", confidence=0.9)
            return UtteranceAnalysis(
                utterance=utterance,
                type="task",
                confidence=0.9,
                domains=("media",),
                explanation="The utterance expresses a media pause goal.",
                task_spans=(span,),
                provenance=make_provenance(utterance, "rule_fast_path", 0.9),
                chat_response=None,
            )
    for prefix in MEDIA_PREFIXES:
        if lowered.startswith(prefix.lower()) or utterance.startswith(prefix):
            query = utterance[len(prefix) :].strip()
            if not query:
                return UtteranceAnalysis(
                    utterance=utterance,
                    type="clarification",
                    confidence=1.0,
                    domains=("media",),
                    explanation="The utterance indicates media playback but does not provide a query.",
                    task_spans=(),
                    provenance=make_provenance(utterance, "rule_fast_path", 1.0),
                    chat_response="What would you like to play?",
                )
            span = TaskSpan(id="span_1", text=utterance, start=0, end=len(utterance), domain="media", confidence=0.9)
            return UtteranceAnalysis(
                utterance=utterance,
                type="task",
                confidence=0.9,
                domains=("media",),
                explanation="The utterance expresses a media playback goal.",
                task_spans=(span,),
                provenance=make_provenance(utterance, "rule_fast_path", 0.9),
                chat_response=None,
            )
    return None


def infer_domain_for_text(text: str) -> str:
    lowered = text.lower()
    if lowered.startswith(("clipboard ", "copy ", "copy to clipboard ", "write ")) or text.startswith(("\u590d\u5236", "\u5199\u5165\u526a\u8d34\u677f")):
        return "clipboard"
    if lowered.startswith(("open http://", "open https://", "open browser", "search web for ")) or text.startswith(("\u6253\u5f00 http://", "\u6253\u5f00 https://", "\u6253\u5f00\u6d4f\u89c8\u5668", "\u641c\u7d22 ")):
        return "browser"
    if lowered.startswith(("close ", "focus ", "switch to ")) or text.startswith(("\u5173\u95ed", "\u805a\u7126", "\u5207\u5230")):
        return "window_management"
    return "system_observation"


def domain_for_action(action: str) -> str:
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
