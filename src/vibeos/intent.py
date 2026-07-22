from __future__ import annotations

import json
import re
import urllib.error

from .capabilities import executable_actions
from .models import Intent
from .provider_client import load_openai_compatible_provider_config, request_json_object
from .task_trace import record_model_io, record_trace_event
from .validation import IntentValidationError, parse_intent_json

ALLOWED_ACTIONS_TEXT = ", ".join([*executable_actions(), "unknown"])
OPEN_CN_PREFIX = "打开"
OPEN_CN_PREFIX_WITH_SPACE = "打开 "
SEARCH_CN_PREFIX = "搜索 "
WEB_NAMED_TARGET_HINTS = ("官网", "网页", "网站", "website", "web page", "webpage", "homepage", "official site")
WINDOW_LIST_TERMS = ("列出窗口", "窗口列表", "list windows", "show windows", "window list")
APP_LIST_TERMS = ("列出应用", "应用列表", "list apps", "show apps", "application list")
STATUS_TERMS = ("系统状态", "状态", "status", "system status")
FOCUS_PREFIXES = ("切到", "切换到", "聚焦", "focus ", "switch to ")
WINDOW_ACTIONS = (
    ("最大化", "window.maximize", "user asked to maximize a window"),
    ("最小化", "window.minimize", "user asked to minimize a window"),
    ("关闭", "window.close", "user asked to close a window"),
    ("maximize ", "window.maximize", "user asked to maximize a window"),
    ("minimize ", "window.minimize", "user asked to minimize a window"),
    ("close ", "window.close", "user asked to close a window"),
)
OPEN_PREFIXES = (OPEN_CN_PREFIX, "启动", "open ", "launch ")
NOTIFICATION_PREFIXES = ("发一个通知", "发送通知", "notify ")
CLIPBOARD_PREFIXES = ("写入剪贴板", "复制到剪贴板", "copy to clipboard ", "clipboard ", "copy ", "write ")
SITE_SEARCH_PATTERN = re.compile(r"^search\s+([A-Za-z0-9.-]+\.[A-Za-z]{2,})\s+for\s+(.+)$", re.IGNORECASE)
BARE_DOMAIN_PATTERN = re.compile(r"^(?:www\.)?[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+(?::\d+)?(?:/.*)?$")
APP_HISTORY_SEARCH_PATTERN = re.compile(
    r"^search\s+(?:(?P<scope>chat history)\s+)?in\s+(?P<app>.+?)\s+for\s+(?P<query>.+)$",
    re.IGNORECASE,
)
MEDIA_SEARCH_PREFIXES = ("search media for ", "search music for ", "find media ", "find music ")
MEDIA_PAUSE_PREFIXES = ("pause", "pause music", "pause playback")
MEDIA_PLAY_PREFIXES = ("play ", "listen to ", "我想听 ", "播放 ", "放一首 ")
MULTI_ACTION_MARKERS = (";", "；", "\n", " and then ", " then ", "如果", "然后", "接着", "最后")
SYSTEM_PROMPT = (
    """You are VibeOS's model intent broker.
Translate the user's natural-language Linux desktop request into exactly one JSON object.
Allowed actions: """
    + ALLOWED_ACTIONS_TEXT
    + """.
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
)


class IntentBroker:
    def parse(self, utterance: str) -> Intent:
        raise NotImplementedError


class RuleIntentBroker(IntentBroker):
    """Conservative local parser for explicit offline operation and tests."""

    def parse(self, utterance: str) -> Intent:
        stripped = utterance.strip()
        text = stripped.lower()
        if not text:
            return Intent.unknown("empty command")
        if any(term in text for term in WINDOW_LIST_TERMS):
            return Intent(action="window.list", reason="user asked to list windows")
        if any(term in text for term in APP_LIST_TERMS):
            return Intent(action="app.list", reason="user asked to list apps")
        if any(term in text for term in STATUS_TERMS):
            return Intent(action="system.status", reason="user asked for VibeOS status")
        for prefix in FOCUS_PREFIXES:
            if text.startswith(prefix):
                return Intent(action="window.focus", target={"name": stripped[len(prefix) :].strip()}, reason="user asked to focus a window")
        for prefix, action, reason in WINDOW_ACTIONS:
            if text.startswith(prefix):
                return Intent(action=action, target={"name": stripped[len(prefix) :].strip() or "current"}, reason=reason)
        if (
            text.startswith(f"{OPEN_CN_PREFIX.lower()} http://")
            or text.startswith(f"{OPEN_CN_PREFIX.lower()} https://")
            or text.startswith("open http://")
            or text.startswith("open https://")
        ):
            return Intent(
                action="browser.open_url",
                target={"uri": stripped.split(maxsplit=1)[-1]},
                reason="user asked to open an explicit browser URL",
            )
        browser_intent = infer_browser_intent_from_open_request(stripped)
        if browser_intent is not None:
            return browser_intent
        app_history_target = extract_app_history_search_target(stripped)
        if app_history_target is not None:
            return Intent(action="app.search_history", target=app_history_target, reason="user asked to search within an application")
        site_search = SITE_SEARCH_PATTERN.match(stripped)
        if site_search:
            site, query = site_search.group(1).strip(), site_search.group(2).strip()
            if site and query:
                return Intent(action="browser.open_site_search", target={"site": site, "query": query}, reason="user asked to search within a specific website")
        if text.startswith("search web for "):
            query = stripped[len("search web for ") :].strip()
            return (
                Intent(action="browser.search_web", target={"query": query}, reason="user asked to search the web")
                if query
                else Intent.unknown("browser search request is missing a query")
            )
        if stripped.startswith(SEARCH_CN_PREFIX):
            query = stripped[len(SEARCH_CN_PREFIX) :].strip()
            return (
                Intent(action="browser.search_web", target={"query": query}, reason="user asked to search the web")
                if query
                else Intent.unknown("browser search request is missing a query")
            )
        for prefix in MEDIA_SEARCH_PREFIXES:
            if text.startswith(prefix):
                query = stripped[len(prefix) :].strip()
                return (
                    Intent(action="media.search", target={"query": query}, reason="user asked to search media")
                    if query
                    else Intent.unknown("media search request is missing a query")
                )
        for prefix in MEDIA_PAUSE_PREFIXES:
            if text == prefix or text.startswith(prefix + " "):
                return Intent(action="media.pause", reason="user asked to pause media")
        for prefix in MEDIA_PLAY_PREFIXES:
            if text.startswith(prefix.lower()) or stripped.startswith(prefix):
                query = stripped[len(prefix) :].strip()
                return (
                    Intent(action="media.play", target={"query": query, "selection": "best_match"}, reason="user asked to play media")
                    if query
                    else Intent.unknown("media playback request is missing a query")
                )
        for prefix in NOTIFICATION_PREFIXES:
            if text.startswith(prefix):
                body = stripped[len(prefix) :].strip() if prefix == "notify " else stripped
                return Intent(action="notification.send", target={"title": "VibeOS", "body": body}, reason="user asked to send a notification")
        for prefix in CLIPBOARD_PREFIXES:
            if text.startswith(prefix):
                return Intent(action="clipboard.write", target={"text": extract_clipboard_content(stripped, prefix)}, reason="user asked to write clipboard")
        for prefix in OPEN_PREFIXES:
            if text.startswith(prefix.lower()) or stripped.startswith(prefix):
                return Intent(action="app.open", target={"name": stripped[len(prefix) :].strip()}, reason="user asked to open an application")
        return Intent.unknown("request did not match VibeOS capabilities")


def explicit_contract_intent(utterance: str) -> Intent | None:
    """Return a host-owned intent only for one unambiguous public command.

    This deliberately stays narrower than ``RuleIntentBroker``.  It protects
    stable CLI/D-Bus contracts from model drift without treating a compound
    goal as one executable action.
    """

    stripped = utterance.strip()
    lowered = stripped.lower()
    if not stripped or any(marker in lowered for marker in MULTI_ACTION_MARKERS):
        return None
    intent = RuleIntentBroker().parse(stripped)
    if intent.action == "unknown":
        return None
    if intent.action == "system.status" and lowered not in STATUS_TERMS:
        return None
    if intent.action == "window.list" and lowered not in WINDOW_LIST_TERMS:
        return None
    if intent.action == "app.list" and lowered not in APP_LIST_TERMS:
        return None
    return intent


def infer_browser_intent_from_open_request(utterance: str) -> Intent | None:
    target = extract_open_target(utterance)
    if not target:
        return None
    normalized_uri = normalize_bare_domain_uri(target)
    if normalized_uri:
        return Intent(action="browser.open_url", target={"uri": normalized_uri}, reason="user asked to open a browser domain")
    lowered_target = target.lower()
    if any(hint in target for hint in WEB_NAMED_TARGET_HINTS[:3]) or any(hint in lowered_target for hint in WEB_NAMED_TARGET_HINTS[3:]):
        return Intent(action="browser.search_web", target={"query": target}, reason="user asked to open a website by name")
    return None


def extract_open_target(utterance: str) -> str:
    stripped = utterance.strip()
    if stripped.lower().startswith("open "):
        return stripped[len("open ") :].strip()
    if stripped.startswith(OPEN_CN_PREFIX_WITH_SPACE):
        return stripped[len(OPEN_CN_PREFIX_WITH_SPACE) :].strip()
    if stripped.startswith(OPEN_CN_PREFIX):
        return stripped[len(OPEN_CN_PREFIX) :].strip()
    return ""


def normalize_bare_domain_uri(target: str) -> str | None:
    candidate = target.strip()
    return f"https://{candidate}" if candidate and "://" not in candidate and BARE_DOMAIN_PATTERN.fullmatch(candidate) else None


def extract_app_history_search_target(utterance: str) -> dict[str, str] | None:
    match = APP_HISTORY_SEARCH_PATTERN.match(utterance.strip())
    if not match:
        return None
    app_name, query = match.group("app").strip(), match.group("query").strip()
    if not app_name or not query:
        return None
    target = {"app": app_name, "query": query}
    if scope := match.group("scope"):
        target["scope"] = scope.strip()
    return target


def extract_clipboard_content(raw: str, prefix: str) -> str:
    lowered = raw.lower()
    if prefix in {"clipboard ", "copy ", "copy to clipboard "}:
        content = raw[len(prefix) :].strip()
        return content[: -len(" to clipboard")].strip() if prefix == "copy " and content.lower().endswith(" to clipboard") else content
    if prefix == "write " and " to clipboard" in lowered:
        return raw[len(prefix) : lowered.rfind(" to clipboard")].strip()
    if prefix in {"写入剪贴板", "复制到剪贴板"}:
        content = raw[len(prefix) :].strip()
        for marker in ("内容是", "内容:", "：", ":"):
            if marker in content:
                return content.split(marker, 1)[1].strip()
        return content
    return raw.strip()


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
