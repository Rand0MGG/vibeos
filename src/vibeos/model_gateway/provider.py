from __future__ import annotations

from dataclasses import dataclass
import json
import os
import socket
from time import monotonic
import urllib.error
import urllib.request
from typing import Literal

from pydantic import ValidationError

from .contracts import (
    DeliveryState,
    FailureCode,
    GatewayFailure,
    GatewayResult,
    ModelRequest,
    ModelResponse,
    ModelUsage,
    ProviderRoute,
    RedactedTransportReceipt,
    ServiceDiagnosis,
    validate_service_diagnosis,
)
from .secrets import SecretNotFound, SecretStore, SecretStoreError, SecretStoreLocked


@dataclass(frozen=True)
class ProviderHttpResponse:
    status: int
    headers: dict[str, str]
    body: bytes


class OpenAICompatibleHttpClient:
    def post(self, *, url: str, body: bytes, headers: dict[str, str], timeout: float) -> ProviderHttpResponse:
        request = urllib.request.Request(url, data=body, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return ProviderHttpResponse(
                    status=int(response.status),
                    headers={str(key).lower(): str(value) for key, value in response.headers.items()},
                    body=response.read(),
                )
        except urllib.error.HTTPError as exc:
            return ProviderHttpResponse(
                status=int(exc.code),
                headers={str(key).lower(): str(value) for key, value in exc.headers.items()},
                body=exc.read(),
            )


class OpenAICompatibleTransport:
    """Secret-capable transport. Instantiate only inside the transport process."""

    def __init__(self, secret_store: SecretStore, http_client: OpenAICompatibleHttpClient | None = None) -> None:
        self.secret_store = secret_store
        self.http_client = http_client or OpenAICompatibleHttpClient()

    def execute(self, route: ProviderRoute, request: ModelRequest) -> GatewayResult:
        started = monotonic()
        if request.cancellation.requested:
            return self._failure(route, request, FailureCode.CANCELLED, "model request was cancelled", retryable=False)
        try:
            secret = self.secret_store.resolve(route.secret_ref)
        except SecretStoreLocked:
            return self._failure(
                route,
                request,
                FailureCode.KEYRING_LOCKED,
                "provider credential is waiting for the session keyring to be unlocked",
                retryable=True,
                status="waiting",
                wait_event_key=f"secret-service:unlocked:{route.secret_ref.secret_id}",
            )
        except SecretNotFound:
            return self._failure(route, request, FailureCode.SECRET_NOT_FOUND, "provider credential reference is not available", retryable=False)
        except SecretStoreError:
            return self._failure(route, request, FailureCode.TRANSPORT_ERROR, "Secret Service operation failed", retryable=True)

        try:
            body = self._request_body(route, request)
            elapsed = monotonic() - started
            remaining = request.budget.total_budget_seconds - elapsed
            if remaining <= 0:
                return self._failure(route, request, FailureCode.BUDGET_EXHAUSTED, "model request budget was exhausted", retryable=False, secret_resolved=True)
            response = self.http_client.post(
                url=f"{route.base_url.rstrip('/')}/chat/completions",
                body=body,
                headers={
                    "Authorization": f"Bearer {secret}",
                    "Content-Type": "application/json",
                    "Idempotency-Key": request.request_id,
                    "X-VibeOS-Request-Id": request.request_id,
                },
                timeout=min(request.budget.timeout_seconds, remaining),
            )
        except (TimeoutError, socket.timeout):
            return self._failure(
                route, request, FailureCode.PROVIDER_TIMEOUT, "provider request timed out", retryable=True, delivery="unknown", secret_resolved=True
            )
        except (urllib.error.URLError, OSError):
            return self._failure(
                route,
                request,
                FailureCode.UNKNOWN_DELIVERY,
                "provider delivery outcome is unknown; reconciliation is required",
                retryable=False,
                delivery="unknown",
                secret_resolved=True,
            )
        finally:
            secret = ""

        if response.status == 429:
            return self._failure(
                route, request, FailureCode.RATE_LIMITED, "provider rate limit was reached", retryable=True, delivery="confirmed", secret_resolved=True
            )
        if response.status >= 500:
            return self._failure(
                route,
                request,
                FailureCode.PROVIDER_SERVER_ERROR,
                "provider server failed the request",
                retryable=True,
                delivery="confirmed",
                secret_resolved=True,
            )
        if response.status < 200 or response.status >= 300:
            return self._failure(
                route, request, FailureCode.TRANSPORT_ERROR, "provider rejected the request", retryable=False, delivery="confirmed", secret_resolved=True
            )
        try:
            payload = json.loads(response.body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return self._failure(
                route, request, FailureCode.INVALID_JSON, "provider returned invalid JSON", retryable=False, delivery="confirmed", secret_resolved=True
            )
        try:
            diagnosis, usage, provider_request_id = self._parse_response(payload)
            validate_service_diagnosis(request, diagnosis)
        except json.JSONDecodeError:
            return self._failure(
                route, request, FailureCode.INVALID_JSON, "provider returned invalid JSON", retryable=False, delivery="confirmed", secret_resolved=True
            )
        except (KeyError, TypeError, ValueError, ValidationError):
            return self._failure(
                route,
                request,
                FailureCode.SCHEMA_MISMATCH,
                "provider response did not match the strict schema",
                retryable=False,
                delivery="confirmed",
                secret_resolved=True,
            )
        if usage.total_tokens > request.budget.max_total_tokens or usage.output_tokens > request.budget.max_output_tokens:
            return self._failure(
                route,
                request,
                FailureCode.BUDGET_EXHAUSTED,
                "provider response exceeded the bound token budget",
                retryable=False,
                delivery="confirmed",
                secret_resolved=True,
            )
        receipt = self._receipt(route, provider_request_id=provider_request_id, delivery="confirmed", secret_resolved=True)
        model_response = ModelResponse(
            request_id=request.request_id,
            binding=request.binding,
            result=diagnosis,
            usage=usage,
            receipt=receipt,
        )
        return GatewayResult(status="succeeded", response=model_response)

    @staticmethod
    def _request_body(route: ProviderRoute, request: ModelRequest) -> bytes:
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

    @staticmethod
    def _parse_response(payload: object) -> tuple[ServiceDiagnosis, ModelUsage, str | None]:
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

    @staticmethod
    def _receipt(
        route: ProviderRoute,
        *,
        provider_request_id: str | None = None,
        delivery: DeliveryState = "not_sent",
        secret_resolved: bool = False,
    ) -> RedactedTransportReceipt:
        return RedactedTransportReceipt(
            route_id=route.route_id,
            provider_request_id=provider_request_id,
            delivery=delivery,
            transport_pid=os.getpid(),
            secret_ref_uri=route.secret_ref.uri,
            secret_resolved=secret_resolved,
        )

    def _failure(
        self,
        route: ProviderRoute,
        request: ModelRequest,
        code: FailureCode,
        message: str,
        *,
        retryable: bool,
        delivery: DeliveryState = "not_sent",
        status: Literal["failed", "waiting"] = "failed",
        wait_event_key: str | None = None,
        secret_resolved: bool = False,
    ) -> GatewayResult:
        receipt = self._receipt(route, delivery=delivery, secret_resolved=secret_resolved)
        failure = GatewayFailure(
            request_id=request.request_id,
            binding=request.binding,
            code=code,
            retryable=retryable,
            delivery=delivery,
            safe_message=message,
            wait_event_key=wait_event_key,
            receipt=receipt,
        )
        return GatewayResult(status=status, failure=failure)
