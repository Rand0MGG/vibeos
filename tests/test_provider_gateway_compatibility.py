from __future__ import annotations

import json

from vibeos.cli import main
from vibeos.model_gateway.contracts import (
    JsonObjectGatewayResult,
    JsonObjectModelResponse,
    ModelUsage,
    ProviderRoute,
    RedactedTransportReceipt,
    SecretRef,
)
from vibeos.model_gateway.secrets import ProviderRouteRepository
from vibeos.provider_client import load_openai_compatible_provider_config


def _save_deepseek_route() -> ProviderRoute:
    route = ProviderRoute(
        route_id="agent-primary",
        model="deepseek-v4-pro",
        base_url="https://api.deepseek.com",
        secret_ref=SecretRef(secret_id="agent-primary", provider="openai-compatible"),
    )
    ProviderRouteRepository().save(route)
    return route


def test_legacy_environment_key_is_ignored_while_secretref_route_is_selected(monkeypatch) -> None:
    route = _save_deepseek_route()
    monkeypatch.setenv("VIBEOS_MODEL_PROVIDER", "deepseek")
    monkeypatch.setenv("DEEPSEEK_MODEL", route.model)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "must-not-be-read")

    config = load_openai_compatible_provider_config()

    assert config.configured is True
    assert config.route == route
    assert "api_key" not in config.__dataclass_fields__


def test_plain_vibe_ask_reaches_goal_understanding_through_model_gateway(monkeypatch, capsys) -> None:
    route = _save_deepseek_route()
    calls: list[dict[str, object]] = []

    class FakeGateway:
        def request_json_object(self, **kwargs: object) -> JsonObjectGatewayResult:
            calls.append(kwargs)
            binding = kwargs["binding"]
            request_id = str(kwargs["request_id"])
            return JsonObjectGatewayResult(
                status="succeeded",
                response=JsonObjectModelResponse(
                    request_id=request_id,
                    binding=binding,  # type: ignore[arg-type]
                    request_payload={"model": route.model, "messages": []},
                    response_payload={"id": "provider-request-understanding"},
                    parsed_object={
                        "type": "chat",
                        "confidence": 0.98,
                        "domains": [],
                        "explanation": "The user is starting a casual conversation.",
                        "chat_response": "你好！",
                    },
                    usage=ModelUsage(input_tokens=20, output_tokens=8, total_tokens=28),
                    receipt=RedactedTransportReceipt(
                        route_id=route.route_id,
                        provider_request_id="provider-request-understanding",
                        delivery="confirmed",
                        transport_pid=123,
                        secret_ref_uri=route.secret_ref.uri,
                        secret_resolved=True,
                    ),
                ),
            )

    monkeypatch.setattr("vibeos.provider_client.ModelGateway", FakeGateway)
    monkeypatch.setenv("VIBEOS_RUNTIME", "local")
    monkeypatch.setenv("VIBEOS_MODEL_PROVIDER", "deepseek")
    monkeypatch.setenv("DEEPSEEK_MODEL", route.model)
    monkeypatch.setenv("VIBEOS_ENABLE_MODEL_UNDERSTANDING", "1")

    exit_code = main(["ask", "你好，聊聊今天的心情", "--json"])
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert payload["overall_status"] == "completed"
    assert calls
    assert calls[0]["purpose"] == "goal_understanding"
    assert calls[0]["route"] == route
