from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

from .capabilities import executable_actions
from .config import load_dotenv
from .models import Intent
from .validation import IntentValidationError, parse_intent_json

ALLOWED_ACTIONS_TEXT = ", ".join([*executable_actions(), "unknown"])

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

    def parse(self, utterance: str) -> Intent:
        text = utterance.strip().lower()
        if not text:
            return Intent.unknown("empty command")

        dangerous_terms = ("删除", "删掉", "rm ", "安装", "发微信", "sudo", "格式化", "shell", "命令行执行")
        if any(term in text for term in dangerous_terms):
            return Intent.unknown("request is outside VibeOS v0 safe capability scope")

        if any(term in text for term in ("列出窗口", "窗口列表", "list windows", "show windows")):
            return Intent(action="window.list", reason="user asked to list windows")
        if any(term in text for term in ("列出应用", "应用列表", "list apps", "show apps")):
            return Intent(action="app.list", reason="user asked to list apps")
        if any(term in text for term in ("系统状态", "status", "状态")):
            return Intent(action="system.status", reason="user asked for VibeOS status")

        focus_prefixes = ("切到", "切换到", "聚焦", "focus ", "switch to ")
        for prefix in focus_prefixes:
            if text.startswith(prefix):
                name = utterance.strip()[len(prefix) :].strip()
                return Intent(action="window.focus", target={"name": name}, reason="user asked to focus a window")

        window_actions = (
            ("最大化", "window.maximize", "user asked to maximize a window"),
            ("最小化", "window.minimize", "user asked to minimize a window"),
            ("关闭", "window.close", "user asked to close a window"),
            ("maximize ", "window.maximize", "user asked to maximize a window"),
            ("minimize ", "window.minimize", "user asked to minimize a window"),
            ("close ", "window.close", "user asked to close a window"),
        )
        for prefix, action, reason in window_actions:
            if text.startswith(prefix):
                name = utterance.strip()[len(prefix) :].strip() or "current"
                return Intent(action=action, target={"name": name}, reason=reason)

        if text.startswith("打开 http://") or text.startswith("打开 https://") or text.startswith("open http://") or text.startswith("open https://"):
            uri = utterance.strip().split(maxsplit=1)[-1] if " " in utterance.strip() else utterance.strip()[2:].strip()
            return Intent(action="portal.open_uri", target={"uri": uri}, reason="user asked to open a URI")

        if text.startswith("发一个通知") or text.startswith("发送通知") or text.startswith("notify "):
            content = utterance.strip()
            for marker in ("内容是", "内容：", ":", "："):
                if marker in content:
                    content = content.split(marker, 1)[1].strip()
                    break
            return Intent(action="notification.send", target={"title": "VibeOS", "body": content}, reason="user asked to send a notification")

        if text.startswith("写入剪贴板") or text.startswith("复制到剪贴板") or text.startswith("clipboard "):
            content = utterance.strip()
            for marker in ("内容是", "内容：", ":", "："):
                if marker in content:
                    content = content.split(marker, 1)[1].strip()
                    break
            return Intent(action="clipboard.write", target={"text": content}, reason="user asked to write clipboard")

        open_prefixes = ("打开", "启动", "open ", "launch ")
        for prefix in open_prefixes:
            if text.startswith(prefix):
                name = utterance.strip()[len(prefix) :].strip()
                return Intent(action="app.open", target={"name": name}, reason="user asked to open an application")

        return Intent.unknown("request did not match VibeOS v0 capabilities")


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
            with urllib.request.urlopen(request, timeout=30) as response:
                response_data = json.loads(response.read().decode("utf-8"))
            content = response_data["choices"][0]["message"]["content"]
            return parse_intent_json(content)
        except (KeyError, IntentValidationError, urllib.error.URLError, TimeoutError, json.JSONDecodeError):
            return Intent.unknown("model intent parsing failed")
