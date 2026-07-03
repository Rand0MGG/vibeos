import json
import urllib.error

from vibeos.intent import OpenAICompatibleIntentBroker
from vibeos.validation import IntentValidationError, validate_intent_payload


def test_missing_provider_config_returns_unknown_intent() -> None:
    broker = OpenAICompatibleIntentBroker()
    intent = broker.parse("open browser")

    assert intent.action == "unknown"
    assert "provider" in intent.reason


def test_model_parse_failure_returns_unknown_intent(monkeypatch) -> None:
    monkeypatch.setenv("VIBEOS_MODEL_PROVIDER", "deepseek")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    monkeypatch.setenv("DEEPSEEK_MODEL", "deepseek-v4-flash")

    def fail_urlopen(_request, timeout=30):
        raise urllib.error.URLError("offline")

    monkeypatch.setattr("urllib.request.urlopen", fail_urlopen)

    broker = OpenAICompatibleIntentBroker()
    intent = broker.parse("open browser")

    assert intent.action == "unknown"
    assert "provider" in intent.reason


def test_model_broker_reuses_successful_parse(monkeypatch) -> None:
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

    first = broker.parse("帮我打开百度官网")
    second = broker.parse("帮我打开百度官网")

    assert first.action == "browser.open_url"
    assert second == first
    assert calls["count"] == 1


def test_validator_rejects_unknown_action() -> None:
    try:
        validate_intent_payload({"action": "shell.run", "target": {}})
    except IntentValidationError as exc:
        assert "unsupported action" in str(exc)
    else:
        raise AssertionError("expected validation error")
