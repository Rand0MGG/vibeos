from __future__ import annotations

import json

from .contracts import FailureCode, JsonObjectModelRequest, ModelRequest, ModelUsage, ProviderRoute, ServiceDiagnosis


def classify_provider_status(status: int) -> tuple[FailureCode, str, bool] | None:
    if status == 429:
        return FailureCode.RATE_LIMITED, "provider rate limit was reached", True
    if status >= 500:
        return FailureCode.PROVIDER_SERVER_ERROR, "provider server failed the request", True
    if status < 200 or status >= 300:
        return FailureCode.TRANSPORT_ERROR, "provider rejected the request", False
    return None


def service_request_body(route: ProviderRoute, request: ModelRequest) -> bytes:
    facts = request.context.items[0]
    system_prompt = (
        "Diagnose only the fixed VibeOS systemd user-service fixture. Return one JSON object matching service_diagnosis/v1. "
        "Return JSON only, without Markdown fences or explanatory text. Do not invent units or arguments. "
        "action must be start, restart, or none; effect_level is E1 for start/restart and E0 for none."
    )
    response_example = {
        "schema_version": "v1",
        "diagnosis": "The fixed fixture is unhealthy and requires a bounded restart.",
        "confidence": 0.95,
        "proposal": {
            "action": "restart",
            "unit": "vibeos-goal04-fixture.service",
            "arguments": [],
            "effect_level": "E1",
            "fact_digest": facts.sha256,
        },
    }
    content = {
        "operation": request.operation,
        "response_schema": request.response_schema.model_dump(mode="json"),
        "json_output_example": response_example,
        "fact_digest": facts.sha256,
        "service_facts": facts.payload.model_dump(mode="json"),
    }
    payload = {
        "model": route.model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": json.dumps(content, ensure_ascii=False, sort_keys=True)},
        ],
        "temperature": 0,
        "thinking": {"type": "disabled"},
        "response_format": {"type": "json_object"},
        "max_tokens": request.budget.max_output_tokens,
    }
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def parse_service_response(payload: object) -> tuple[ServiceDiagnosis, ModelUsage, str | None]:
    if not isinstance(payload, dict):
        raise ValueError("response is not an object")
    content = payload["choices"][0]["message"]["content"]
    if not isinstance(content, str):
        raise ValueError("provider response content is invalid")
    json.loads(content)
    diagnosis = ServiceDiagnosis.model_validate_json(content)
    usage_payload = payload["usage"]
    usage = ModelUsage(
        input_tokens=usage_payload["prompt_tokens"],
        output_tokens=usage_payload["completion_tokens"],
        total_tokens=usage_payload["total_tokens"],
    )
    response_id = payload.get("id")
    if response_id is not None and not isinstance(response_id, str):
        raise ValueError("provider response id is invalid")
    return diagnosis, usage, response_id


def json_object_request_payload(route: ProviderRoute, request: JsonObjectModelRequest) -> dict[str, object]:
    payload: dict[str, object] = {
        "model": route.model,
        "messages": [
            {"role": "system", "content": request.system_prompt},
            {"role": "user", "content": request.user_content},
        ],
        "temperature": request.temperature,
        "response_format": {"type": "json_object"},
        "max_tokens": request.budget.max_output_tokens,
    }
    if "deepseek" in route.base_url.lower():
        payload["thinking"] = {"type": "disabled"}
    return payload


def parse_json_object_response(body: bytes) -> tuple[dict[str, object], dict[str, object], ModelUsage, str | None]:
    response_payload = json.loads(body.decode("utf-8"))
    if not isinstance(response_payload, dict):
        raise ValueError("provider response is not an object")
    content = response_payload["choices"][0]["message"]["content"]
    if not isinstance(content, str):
        raise ValueError("provider response content is invalid")
    parsed_object = json.loads(content)
    if not isinstance(parsed_object, dict):
        raise ValueError("provider response content is not a JSON object")
    usage_payload = response_payload["usage"]
    usage = ModelUsage(
        input_tokens=usage_payload["prompt_tokens"],
        output_tokens=usage_payload["completion_tokens"],
        total_tokens=usage_payload["total_tokens"],
    )
    response_id = response_payload.get("id")
    provider_request_id = response_id if isinstance(response_id, str) else None
    return response_payload, parsed_object, usage, provider_request_id
