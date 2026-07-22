from vibeos.intent import OpenAICompatibleIntentBroker
from vibeos.validation import IntentValidationError, validate_intent_payload


def test_missing_provider_config_returns_unknown_intent() -> None:
    broker = OpenAICompatibleIntentBroker()
    intent = broker.parse("open browser")

    assert intent.action == "unknown"
    assert "provider" in intent.reason


def test_legacy_provider_environment_is_not_a_credential_path(monkeypatch) -> None:
    monkeypatch.setenv("VIBEOS_MODEL_PROVIDER", "deepseek")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    monkeypatch.setenv("DEEPSEEK_MODEL", "deepseek-v4-flash")

    broker = OpenAICompatibleIntentBroker()
    intent = broker.parse("open browser")

    assert intent.action == "unknown"
    assert "provider" in intent.reason


def test_legacy_provider_cannot_bypass_model_gateway(monkeypatch) -> None:
    monkeypatch.setenv("VIBEOS_MODEL_PROVIDER", "deepseek")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    monkeypatch.setenv("DEEPSEEK_MODEL", "deepseek-v4-flash")

    calls = {"count": 0}

    def fake_urlopen(_request, timeout=30):
        calls["count"] += 1
        raise AssertionError("legacy provider transport must not run")

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    broker = OpenAICompatibleIntentBroker()

    first = broker.parse("帮我打开百度官网")
    second = broker.parse("帮我打开百度官网")

    assert first.action == "unknown"
    assert second == first
    assert calls["count"] == 0


def test_validator_rejects_unknown_action() -> None:
    try:
        validate_intent_payload({"action": "shell.run", "target": {}})
    except IntentValidationError as exc:
        assert "unsupported action" in str(exc)
    else:
        raise AssertionError("expected validation error")
