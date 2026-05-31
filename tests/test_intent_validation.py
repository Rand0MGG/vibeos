from vibeos.intent import OpenAICompatibleIntentBroker, RuleIntentBroker
from vibeos.validation import IntentValidationError, validate_intent_payload
from pathlib import Path

from vibeos.config import find_dotenv, strip_env_value


def test_rule_parser_opens_application() -> None:
    intent = RuleIntentBroker().parse("打开浏览器")
    assert intent.action == "app.open"
    assert intent.target["name"] == "浏览器"


def test_rule_parser_rejects_dangerous_request() -> None:
    intent = RuleIntentBroker().parse("删除下载目录")
    assert intent.action == "unknown"


def test_rule_parser_recognizes_reviewed_capabilities() -> None:
    assert RuleIntentBroker().parse("关闭浏览器").action == "window.close"
    assert RuleIntentBroker().parse("打开 https://deepseek.com").action == "portal.open_uri"
    assert RuleIntentBroker().parse("写入剪贴板 内容是 hello").action == "clipboard.write"


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


def test_env_value_strips_matching_quotes() -> None:
    assert strip_env_value('"deepseek-v4-flash"') == "deepseek-v4-flash"
    assert strip_env_value("'deepseek-v4-flash'") == "deepseek-v4-flash"
    assert strip_env_value("deepseek-v4-flash") == "deepseek-v4-flash"


def test_find_dotenv_honors_explicit_env_file(monkeypatch) -> None:
    monkeypatch.setenv("VIBEOS_ENV_FILE", "/tmp/vibeos.env")
    assert find_dotenv(Path.cwd()) == Path("/tmp/vibeos.env")
