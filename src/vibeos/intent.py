from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request

from .capabilities import executable_actions
from .config import load_dotenv, provider_timeout_seconds
from .models import Intent
from .validation import IntentValidationError, parse_intent_json

ALLOWED_ACTIONS_TEXT = ", ".join([*executable_actions(), "unknown"])
OPEN_CN_PREFIX = "\u6253\u5f00"
OPEN_CN_PREFIX_WITH_SPACE = "\u6253\u5f00 "
WEB_NAMED_TARGET_HINTS = (
    "\u5b98\u7f51",
    "\u7f51\u9875",
    "\u7f51\u7ad9",
    "website",
    "web page",
    "webpage",
    "homepage",
    "official site",
)
BARE_DOMAIN_PATTERN = re.compile(r"^(?:www\.)?[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+(?::\d+)?(?:/.*)?$")

SYSTEM_PROMPT = """You are VibeOS's model intent broker.
Translate the user's natural-language Linux desktop request into exactly one JSON object.
Allowed actions: """ + ALLOWED_ACTIONS_TEXT + """.
Do not include shell commands, scripts, raw D-Bus paths, raw API calls, or implementation details.
If the request asks to delete files, install software, send messages to other people, read private screen content, type into apps, run shell commands, or perform any unsupported action, return action "unknown".
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


class RuleIntentBroker(IntentBroker):
    """Conservative local parser for offline development and tests."""

    DANGEROUS_PREFIXES = (
        "delete ",
        "remove ",
        "rm ",
        "install ",
        "sudo ",
        "shell ",
        "command line ",
        "\u5220\u9664",
        "\u5220\u6389",
        "\u5b89\u88c5",
    )

    DANGEROUS_TERMS = (
        "删除",
        "删掉",
        "delete",
        "remove",
        "rm ",
        "安装",
        "install",
        "发微信",
        "sudo",
        "格式化",
        "shell",
        "命令行执行",
        "command line",
    )

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
    OPEN_PREFIXES = ("打开", "启动", "open ", "launch ")

    def parse(self, utterance: str) -> Intent:
        text = utterance.strip().lower()
        if not text:
            return Intent.unknown("empty command")

        if any(text.startswith(prefix) for prefix in self.DANGEROUS_PREFIXES):
            return Intent.unknown("request is outside VibeOS safe capability scope")

        if any(term in text for term in self.WINDOW_LIST_TERMS):
            return Intent(action="window.list", reason="user asked to list windows")
        if any(term in text for term in self.APP_LIST_TERMS):
            return Intent(action="app.list", reason="user asked to list apps")
        if any(term in text for term in self.STATUS_TERMS):
            return Intent(action="system.status", reason="user asked for VibeOS status")

        for prefix in self.FOCUS_PREFIXES:
            if text.startswith(prefix):
                name = utterance.strip()[len(prefix) :].strip()
                return Intent(action="window.focus", target={"name": name}, reason="user asked to focus a window")

        for prefix, action, reason in self.WINDOW_ACTIONS:
            if text.startswith(prefix):
                name = utterance.strip()[len(prefix) :].strip() or "current"
                return Intent(action=action, target={"name": name}, reason=reason)

        if text.startswith("打开 http://") or text.startswith("打开 https://") or text.startswith("open http://") or text.startswith("open https://"):
            uri = utterance.strip().split(maxsplit=1)[-1]
            return Intent(action="portal.open_uri", target={"uri": uri}, reason="user asked to open a URI")
        browser_intent = infer_browser_intent_from_open_request(utterance.strip())
        if browser_intent is not None:
            return browser_intent

        if text.startswith("发一个通知") or text.startswith("发送通知") or text.startswith("notify "):
            body = utterance.strip()
            if text.startswith("notify "):
                body = utterance.strip()[len("notify ") :].strip()
            return Intent(action="notification.send", target={"title": "VibeOS", "body": body}, reason="user asked to send a notification")

        clipboard_prefixes = ("写入剪贴板", "复制到剪贴板", "copy to clipboard ", "clipboard ", "copy ", "write ")
        for prefix in clipboard_prefixes:
            if text.startswith(prefix):
                content = extract_clipboard_content(utterance.strip(), prefix)
                return Intent(action="clipboard.write", target={"text": content}, reason="user asked to write clipboard")

        for prefix in self.OPEN_PREFIXES:
            if text.startswith(prefix):
                name = utterance.strip()[len(prefix) :].strip()
                return Intent(action="app.open", target={"name": name}, reason="user asked to open an application")

        return Intent.unknown("request did not match VibeOS capabilities")


def infer_browser_intent_from_open_request(utterance: str) -> Intent | None:
    target = extract_open_target(utterance)
    if not target:
        return None
    normalized_uri = normalize_bare_domain_uri(target)
    if normalized_uri:
        return Intent(
            action="browser.open_url",
            target={"uri": normalized_uri},
            reason="user asked to open a browser domain",
        )
    lowered_target = target.lower()
    if any(hint in target for hint in WEB_NAMED_TARGET_HINTS[:3]) or any(hint in lowered_target for hint in WEB_NAMED_TARGET_HINTS[3:]):
        return Intent(
            action="browser.search_web",
            target={"query": target},
            reason="user asked to open a website by name",
        )
    return None


def extract_open_target(utterance: str) -> str:
    stripped = utterance.strip()
    lowered = stripped.lower()
    if lowered.startswith("open "):
        return stripped[len("open ") :].strip()
    if stripped.startswith(OPEN_CN_PREFIX_WITH_SPACE):
        return stripped[len(OPEN_CN_PREFIX_WITH_SPACE) :].strip()
    if stripped.startswith(OPEN_CN_PREFIX):
        return stripped[len(OPEN_CN_PREFIX) :].strip()
    return ""


def normalize_bare_domain_uri(target: str) -> str | None:
    candidate = target.strip()
    if not candidate or "://" in candidate:
        return None
    if not BARE_DOMAIN_PATTERN.fullmatch(candidate):
        return None
    return f"https://{candidate}"


def extract_clipboard_content(raw: str, prefix: str) -> str:
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


class OpenAICompatibleIntentBroker(IntentBroker):
    def __init__(self) -> None:
        load_dotenv()
        self.provider = os.environ.get("VIBEOS_MODEL_PROVIDER", "openai-compatible").strip().lower()
        if self.provider == "deepseek":
            self.api_key = os.environ.get("DEEPSEEK_API_KEY") or os.environ.get("OPENAI_API_KEY")
            self.base_url = (
                os.environ.get("DEEPSEEK_BASE_URL")
                or os.environ.get("OPENAI_BASE_URL")
                or "https://api.deepseek.com"
            ).rstrip("/")
            self.model = os.environ.get("DEEPSEEK_MODEL") or os.environ.get("OPENAI_MODEL") or "deepseek-v4-flash"
        else:
            self.api_key = os.environ.get("OPENAI_API_KEY")
            self.base_url = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
            self.model = os.environ.get("OPENAI_MODEL")
        self.fallback = RuleIntentBroker()

    def parse(self, utterance: str) -> Intent:
        if not self.api_key or not self.model:
            return self.fallback.parse(utterance)

        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": utterance},
            ],
            "temperature": 0,
            "response_format": {"type": "json_object"},
            "max_tokens": 512,
        }
        request = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )

        try:
            with urllib.request.urlopen(request, timeout=provider_timeout_seconds()) as response:
                response_data = json.loads(response.read().decode("utf-8"))
            content = response_data["choices"][0]["message"]["content"]
            return parse_intent_json(content)
        except urllib.error.URLError:
            return Intent.unknown("model provider is unavailable")
        except TimeoutError:
            return Intent.unknown("model provider timed out")
        except (KeyError, IntentValidationError, json.JSONDecodeError):
            return Intent.unknown("model provider returned an invalid intent payload")
