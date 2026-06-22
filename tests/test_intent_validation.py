from pathlib import Path
import json

from vibeos.config import find_dotenv, strip_env_value
from vibeos.intent import OpenAICompatibleIntentBroker, RuleIntentBroker
from vibeos.nlu import analyze_utterance
from vibeos.validation import IntentValidationError, validate_intent_payload


def test_rule_parser_opens_application() -> None:
    intent = RuleIntentBroker().parse("open browser")
    assert intent.action == "app.open"
    assert intent.target["name"] == "browser"


def test_rule_parser_rejects_dangerous_request() -> None:
    intent = RuleIntentBroker().parse("delete downloads")
    assert intent.action == "unknown"
    assert intent.reason == "request did not match VibeOS capabilities"


def test_rule_parser_recognizes_reviewed_capabilities() -> None:
    assert RuleIntentBroker().parse("close browser").action == "window.close"
    assert RuleIntentBroker().parse("open https://deepseek.com").action == "portal.open_uri"
    assert RuleIntentBroker().parse("open baidu.com").action == "browser.open_url"
    assert RuleIntentBroker().parse("clipboard hello").action == "clipboard.write"


def test_rule_parser_recognizes_browser_search_variants() -> None:
    assert RuleIntentBroker().parse("search web for hello").action == "browser.search_web"
    assert RuleIntentBroker().parse("\u641c\u7d22 hello").action == "browser.search_web"
    site_search = RuleIntentBroker().parse("search zhihu.com for OpenAI")

    assert site_search.action == "browser.open_site_search"
    assert site_search.target == {"site": "zhihu.com", "query": "OpenAI"}


def test_rule_parser_recognizes_media_control_variants() -> None:
    play = RuleIntentBroker().parse("play baby")
    pause = RuleIntentBroker().parse("pause music")

    assert play.action == "media.play"
    assert play.target["query"] == "baby"
    assert pause.action == "media.pause"


def test_rule_parser_recognizes_app_history_search() -> None:
    intent = RuleIntentBroker().parse("search chat history in WeChat for Alice")

    assert intent.action == "app.search_history"
    assert intent.target["app"] == "WeChat"
    assert intent.target["query"] == "Alice"


def test_rule_parser_extracts_clipboard_content() -> None:
    intent = RuleIntentBroker().parse("clipboard VibeOS evidence")
    assert intent.action == "clipboard.write"
    assert intent.target["text"] == "VibeOS evidence"


def test_rule_parser_extracts_copy_variants_for_clipboard() -> None:
    variants = (
        "copy VibeOS evidence",
        "copy to clipboard VibeOS evidence",
        "write VibeOS evidence to clipboard",
    )

    for utterance in variants:
        intent = RuleIntentBroker().parse(utterance)
        assert intent.action == "clipboard.write"
        assert intent.target["text"] == "VibeOS evidence"


def test_rule_parser_treats_named_web_targets_as_browser_requests() -> None:
    intent = RuleIntentBroker().parse("\u6253\u5f00\u767e\u5ea6\u5b98\u7f51")

    assert intent.action == "browser.search_web"
    assert intent.target["query"] == "\u767e\u5ea6\u5b98\u7f51"


def test_chat_mentions_delete_without_triggering_raw_keyword_rejection() -> None:
    analysis = analyze_utterance("what do you think about a document that contains the word delete")

    assert analysis.type == "chat"


def test_supported_request_containing_delete_is_not_front_door_rejected() -> None:
    analysis = analyze_utterance("copy delete to clipboard")

    assert analysis.type == "task"
    assert analysis.task_spans[0].domain == "clipboard"


def test_validator_rejects_unknown_action() -> None:
    try:
        validate_intent_payload({"action": "shell.run", "target": {}})
    except IntentValidationError as exc:
        assert "unsupported action" in str(exc)
    else:
        raise AssertionError("expected validation error")


def test_validator_rejects_shell_key() -> None:
    try:
        validate_intent_payload({"action": "app.open", "target": {"name": "Firefox"}, "shell": "gtk-launch firefox"})
    except IntentValidationError as exc:
        assert "forbidden keys" in str(exc)
    else:
        raise AssertionError("expected validation error")


def test_validator_rejects_nested_shell_key() -> None:
    try:
        validate_intent_payload({"action": "app.open", "target": {"name": {"shell": "gtk-launch firefox"}}})
    except IntentValidationError as exc:
        assert "forbidden keys" in str(exc)
    else:
        raise AssertionError("expected validation error")


def test_deepseek_provider_defaults(monkeypatch) -> None:
    monkeypatch.setenv("VIBEOS_MODEL_PROVIDER", "deepseek")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    monkeypatch.delenv("DEEPSEEK_BASE_URL", raising=False)
    monkeypatch.delenv("DEEPSEEK_MODEL", raising=False)
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    monkeypatch.delenv("OPENAI_MODEL", raising=False)

    broker = OpenAICompatibleIntentBroker()

    assert broker.provider == "deepseek"
    assert broker.api_key == "test-key"
    assert broker.base_url == "https://api.deepseek.com"
    assert broker.model == "deepseek-v4-flash"


def test_model_parse_failure_returns_explicit_provider_error(monkeypatch) -> None:
    monkeypatch.setenv("VIBEOS_MODEL_PROVIDER", "deepseek")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    monkeypatch.setenv("DEEPSEEK_MODEL", "deepseek-v4-flash")

    def fail_urlopen(_request, timeout=30):
        raise urllib.error.URLError("offline")

    import urllib.error

    monkeypatch.setattr("urllib.request.urlopen", fail_urlopen)

    broker = OpenAICompatibleIntentBroker()
    intent = broker.parse("open browser")

    assert intent.action == "unknown"
    assert "provider" in intent.reason


def test_model_broker_reuses_successful_parse_for_same_utterance(monkeypatch) -> None:
    monkeypatch.setenv("VIBEOS_MODEL_PROVIDER", "deepseek")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    monkeypatch.setenv("DEEPSEEK_MODEL", "deepseek-v4-flash")

    payload = {
        "choices": [
            {
                "message": {
                    "content": json.dumps(
                        {
                            "action": "browser.open_url",
                            "target": {"url": "https://www.baidu.com"},
                            "reason": "open Baidu official site",
                            "requires_confirmation": False,
                        }
                    )
                }
            }
        ]
    }
    calls = {"count": 0}

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return json.dumps(payload).encode("utf-8")

    def fake_urlopen(_request, timeout=30):
        calls["count"] += 1
        return FakeResponse()

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    broker = OpenAICompatibleIntentBroker()

    first = broker.parse("\u5e2e\u6211\u6253\u5f00\u767e\u5ea6\u5b98\u7f51")
    second = broker.parse("\u5e2e\u6211\u6253\u5f00\u767e\u5ea6\u5b98\u7f51")

    assert first.action == "browser.open_url"
    assert first.target["url"] == "https://www.baidu.com"
    assert second == first
    assert calls["count"] == 1


def test_env_value_strips_matching_quotes() -> None:
    assert strip_env_value('"deepseek-v4-flash"') == "deepseek-v4-flash"
    assert strip_env_value("'deepseek-v4-flash'") == "deepseek-v4-flash"
    assert strip_env_value("deepseek-v4-flash") == "deepseek-v4-flash"


def test_find_dotenv_honors_explicit_env_file(monkeypatch) -> None:
    monkeypatch.setenv("VIBEOS_ENV_FILE", "/tmp/vibeos.env")
    assert find_dotenv(Path.cwd()) == "/tmp/vibeos.env"
