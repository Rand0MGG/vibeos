from __future__ import annotations

import re

from vibeos.intent import IntentBroker
from vibeos.models import Intent

WINDOW_LIST_TERMS = ("列出窗口", "窗口列表", "list windows", "show windows", "window list")
APP_LIST_TERMS = ("列出应用", "应用列表", "list apps", "show apps", "application list")
STATUS_TERMS = ("系统状态", "状态", "status", "system status")
FOCUS_PREFIXES = ("切到", "切换到", "聚焦", "focus ", "switch to ")
WINDOW_ACTIONS = (
    ("最大化", "window.maximize", "fixture asked to maximize a window"),
    ("最小化", "window.minimize", "fixture asked to minimize a window"),
    ("关闭", "window.close", "fixture asked to close a window"),
    ("maximize ", "window.maximize", "fixture asked to maximize a window"),
    ("minimize ", "window.minimize", "fixture asked to minimize a window"),
    ("close ", "window.close", "fixture asked to close a window"),
)
OPEN_PREFIXES = ("打开", "启动", "open ", "launch ")
NOTIFICATION_PREFIXES = ("发一个通知", "发送通知", "notify ")
CLIPBOARD_PREFIXES = ("写入剪贴板", "复制到剪贴板", "copy to clipboard ", "clipboard ", "copy ", "write ")
SITE_SEARCH_PATTERN = re.compile(r"^search\s+([A-Za-z0-9.-]+\.[A-Za-z]{2,})\s+for\s+(.+)$", re.IGNORECASE)
SEARCH_CN_PREFIX = "搜索 "
MEDIA_SEARCH_PREFIXES = ("search media for ", "search music for ", "find media ", "find music ")
MEDIA_PAUSE_PREFIXES = ("pause", "pause music", "pause playback")
MEDIA_PLAY_PREFIXES = ("play ", "listen to ", "我想听 ", "播放 ", "放一首 ")
WEB_NAMED_TARGET_HINTS = ("官网", "网页", "网站", "website", "web page", "webpage", "homepage", "official site")
BARE_DOMAIN_PATTERN = re.compile(r"^(?:www\.)?[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+(?::\d+)?(?:/.*)?$")
APP_HISTORY_SEARCH_PATTERN = re.compile(
    r"^search\s+(?:(?P<scope>chat history)\s+)?in\s+(?P<app>.+?)\s+for\s+(?P<query>.+)$",
    re.IGNORECASE,
)


class FixtureIntentBroker(IntentBroker):
    """Deterministic intent broker used only by unit tests."""

    def parse(self, utterance: str) -> Intent:
        stripped = utterance.strip()
        text = stripped.lower()
        if not text:
            return Intent.unknown("empty command")

        if any(term in text for term in WINDOW_LIST_TERMS):
            return Intent(action="window.list", reason="fixture asked to list windows")
        if any(term in text for term in APP_LIST_TERMS):
            return Intent(action="app.list", reason="fixture asked to list apps")
        if any(term in text for term in STATUS_TERMS):
            return Intent(action="system.status", reason="fixture asked for VibeOS status")

        for prefix in FOCUS_PREFIXES:
            if text.startswith(prefix):
                name = stripped[len(prefix) :].strip()
                return Intent(action="window.focus", target={"name": name}, reason="fixture asked to focus a window")

        for prefix, action, reason in WINDOW_ACTIONS:
            if text.startswith(prefix):
                name = stripped[len(prefix) :].strip() or "current"
                return Intent(action=action, target={"name": name}, reason=reason)

        if text.startswith("打开 http://") or text.startswith("打开 https://") or text.startswith("open http://") or text.startswith("open https://"):
            uri = stripped.split(maxsplit=1)[-1]
            return Intent(action="portal.open_uri", target={"uri": uri}, reason="fixture asked to open a URI")

        browser_intent = _infer_browser_intent_from_open_request(stripped)
        if browser_intent is not None:
            return browser_intent

        app_history_target = _extract_app_history_search_target(stripped)
        if app_history_target is not None:
            return Intent(
                action="app.search_history",
                target=app_history_target,
                reason="fixture asked to search within an application",
            )

        site_search = SITE_SEARCH_PATTERN.match(stripped)
        if site_search:
            site = site_search.group(1).strip()
            query = site_search.group(2).strip()
            if site and query:
                return Intent(
                    action="browser.open_site_search",
                    target={"site": site, "query": query},
                    reason="fixture asked to search within a specific website",
                )

        if text.startswith("search web for "):
            query = stripped[len("search web for ") :].strip()
            if query:
                return Intent(action="browser.search_web", target={"query": query}, reason="fixture asked to search the web")
            return Intent.unknown("browser search request is missing a query")
        if stripped.startswith(SEARCH_CN_PREFIX):
            query = stripped[len(SEARCH_CN_PREFIX) :].strip()
            if query:
                return Intent(action="browser.search_web", target={"query": query}, reason="fixture asked to search the web")
            return Intent.unknown("browser search request is missing a query")

        for prefix in MEDIA_SEARCH_PREFIXES:
            if text.startswith(prefix):
                query = stripped[len(prefix) :].strip()
                if query:
                    return Intent(action="media.search", target={"query": query}, reason="fixture asked to search media")
                return Intent.unknown("media search request is missing a query")
        for prefix in MEDIA_PAUSE_PREFIXES:
            if text == prefix or text.startswith(prefix + " "):
                return Intent(action="media.pause", target={}, reason="fixture asked to pause media")
        for prefix in MEDIA_PLAY_PREFIXES:
            if text.startswith(prefix.lower()) or stripped.startswith(prefix):
                query = stripped[len(prefix) :].strip()
                if query:
                    return Intent(
                        action="media.play",
                        target={"query": query, "selection": "best_match"},
                        reason="fixture asked to play media",
                    )
                return Intent.unknown("media playback request is missing a query")

        for prefix in NOTIFICATION_PREFIXES:
            if text.startswith(prefix):
                body = stripped[len(prefix) :].strip() if prefix == "notify " else stripped
                return Intent(action="notification.send", target={"title": "VibeOS", "body": body}, reason="fixture asked to send a notification")

        for prefix in CLIPBOARD_PREFIXES:
            if text.startswith(prefix):
                return Intent(action="clipboard.write", target={"text": _extract_clipboard_content(stripped, prefix)}, reason="fixture asked to write clipboard")

        for prefix in OPEN_PREFIXES:
            if text.startswith(prefix.lower()) or stripped.startswith(prefix):
                name = stripped[len(prefix) :].strip()
                return Intent(action="app.open", target={"name": name}, reason="fixture asked to open an application")

        return Intent.unknown("request did not match VibeOS capabilities")


def _infer_browser_intent_from_open_request(utterance: str) -> Intent | None:
    target = _extract_open_target(utterance)
    if not target:
        return None
    normalized_uri = _normalize_bare_domain_uri(target)
    if normalized_uri:
        return Intent(
            action="browser.open_url",
            target={"uri": normalized_uri},
            reason="fixture asked to open a browser domain",
        )
    lowered_target = target.lower()
    if any(hint in target for hint in WEB_NAMED_TARGET_HINTS[:3]) or any(hint in lowered_target for hint in WEB_NAMED_TARGET_HINTS[3:]):
        return Intent(
            action="browser.search_web",
            target={"query": target},
            reason="fixture asked to open a website by name",
        )
    return None


def _extract_open_target(utterance: str) -> str:
    stripped = utterance.strip()
    lowered = stripped.lower()
    if lowered.startswith("open "):
        return stripped[len("open ") :].strip()
    if stripped.startswith("打开 "):
        return stripped[len("打开 ") :].strip()
    if stripped.startswith("打开"):
        return stripped[len("打开") :].strip()
    return ""


def _normalize_bare_domain_uri(target: str) -> str | None:
    candidate = target.strip()
    if not candidate or "://" in candidate:
        return None
    if not BARE_DOMAIN_PATTERN.fullmatch(candidate):
        return None
    return f"https://{candidate}"


def _extract_app_history_search_target(utterance: str) -> dict[str, str] | None:
    match = APP_HISTORY_SEARCH_PATTERN.match(utterance.strip())
    if not match:
        return None
    app_name = match.group("app").strip()
    query = match.group("query").strip()
    if not app_name or not query:
        return None
    target = {"app": app_name, "query": query}
    scope = match.group("scope")
    if isinstance(scope, str) and scope.strip():
        target["scope"] = scope.strip()
    return target


def _extract_clipboard_content(raw: str, prefix: str) -> str:
    lowered = raw.lower()
    if prefix in {"clipboard ", "copy ", "copy to clipboard "}:
        content = raw[len(prefix) :].strip()
        if prefix == "copy " and content.lower().endswith(" to clipboard"):
            return content[: -len(" to clipboard")].strip()
        return content
    if prefix == "write " and " to clipboard" in lowered:
        marker_index = lowered.rfind(" to clipboard")
        return raw[len(prefix) : marker_index].strip()
    if prefix in {"写入剪贴板", "复制到剪贴板"}:
        content = raw[len(prefix) :].strip()
        for marker in ("内容是", "内容:", "：", ":"):
            if marker in content:
                return content.split(marker, 1)[1].strip()
        return content
    return raw.strip()
