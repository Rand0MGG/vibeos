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
    JsonObjectGatewayResult,
    JsonObjectModelRequest,
    JsonObjectModelResponse,
    ModelRequest,
    ModelResponse,
    ProviderRoute,
    RedactedTransportReceipt,
    validate_service_diagnosis,
)
from .provider_payloads import classify_provider_status, json_object_request_payload, parse_json_object_response, parse_service_response, service_request_body
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
            body = service_request_body(route, request)
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
            diagnosis, usage, provider_request_id = parse_service_response(payload)
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

    def execute_json_object(self, route: ProviderRoute, request: JsonObjectModelRequest) -> JsonObjectGatewayResult:
        """Execute one allowlisted compatibility purpose inside the secret process."""

        started = monotonic()
        if request.cancellation.requested:
            return self._json_failure(route, request, FailureCode.CANCELLED, "model request was cancelled", retryable=False)
        try:
            secret = self.secret_store.resolve(route.secret_ref)
        except SecretStoreLocked:
            return self._json_failure(
                route,
                request,
                FailureCode.KEYRING_LOCKED,
                "provider credential is waiting for the session keyring to be unlocked",
                retryable=True,
                status="waiting",
                wait_event_key=f"secret-service:unlocked:{route.secret_ref.secret_id}",
            )
        except SecretNotFound:
            return self._json_failure(
                route,
                request,
                FailureCode.SECRET_NOT_FOUND,
                "provider credential reference is not available",
                retryable=False,
            )
        except SecretStoreError:
            return self._json_failure(
                route,
                request,
                FailureCode.TRANSPORT_ERROR,
                "Secret Service operation failed",
                retryable=True,
            )

        request_payload = json_object_request_payload(route, request)
        try:
            elapsed = monotonic() - started
            remaining = request.budget.total_budget_seconds - elapsed
            if remaining <= 0:
                return self._json_failure(
                    route,
                    request,
                    FailureCode.BUDGET_EXHAUSTED,
                    "model request budget was exhausted",
                    retryable=False,
                    secret_resolved=True,
                )
            response = self.http_client.post(
                url=f"{route.base_url.rstrip('/')}/chat/completions",
                body=json.dumps(request_payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8"),
                headers={
                    "Authorization": f"Bearer {secret}",
                    "Content-Type": "application/json",
                    "Idempotency-Key": request.request_id,
                    "X-VibeOS-Request-Id": request.request_id,
                    "X-VibeOS-Purpose": request.purpose,
                },
                timeout=min(request.budget.timeout_seconds, remaining),
            )
        except (TimeoutError, socket.timeout):
            return self._json_failure(
                route,
                request,
                FailureCode.PROVIDER_TIMEOUT,
                "provider request timed out",
                retryable=True,
                delivery="unknown",
                secret_resolved=True,
            )
        except (urllib.error.URLError, OSError):
            return self._json_failure(
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

        status_failure = classify_provider_status(response.status)
        if status_failure is not None:
            code, message, retryable = status_failure
            return self._json_failure(
                route,
                request,
                code,
                message,
                retryable=retryable,
                delivery="confirmed",
                secret_resolved=True,
            )
        try:
            response_payload, parsed_object, usage, provider_request_id = parse_json_object_response(response.body)
        except (UnicodeDecodeError, json.JSONDecodeError):
            return self._json_failure(
                route,
                request,
                FailureCode.INVALID_JSON,
                "provider returned invalid JSON",
                retryable=False,
                delivery="confirmed",
                secret_resolved=True,
            )
        except (KeyError, IndexError, TypeError, ValueError, ValidationError):
            return self._json_failure(
                route,
                request,
                FailureCode.SCHEMA_MISMATCH,
                "provider response did not match the JSON object contract",
                retryable=False,
                delivery="confirmed",
                secret_resolved=True,
            )
        if usage.total_tokens > request.budget.max_total_tokens or usage.output_tokens > request.budget.max_output_tokens:
            return self._json_failure(
                route,
                request,
                FailureCode.BUDGET_EXHAUSTED,
                "provider response exceeded the bound token budget",
                retryable=False,
                delivery="confirmed",
                secret_resolved=True,
            )
        model_response = JsonObjectModelResponse(
            request_id=request.request_id,
            binding=request.binding,
            request_payload=request_payload,
            response_payload=response_payload,
            parsed_object=parsed_object,
            usage=usage,
            receipt=self._receipt(route, provider_request_id=provider_request_id, delivery="confirmed", secret_resolved=True),
        )
        return JsonObjectGatewayResult(status="succeeded", response=model_response)

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

    def _json_failure(
        self,
        route: ProviderRoute,
        request: JsonObjectModelRequest,
        code: FailureCode,
        message: str,
        *,
        retryable: bool,
        delivery: DeliveryState = "not_sent",
        status: Literal["failed", "waiting"] = "failed",
        wait_event_key: str | None = None,
        secret_resolved: bool = False,
    ) -> JsonObjectGatewayResult:
        failure = GatewayFailure(
            request_id=request.request_id,
            binding=request.binding,
            code=code,
            retryable=retryable,
            delivery=delivery,
            safe_message=message,
            wait_event_key=wait_event_key,
            receipt=self._receipt(route, delivery=delivery, secret_resolved=secret_resolved),
        )
        return JsonObjectGatewayResult(status=status, failure=failure)
