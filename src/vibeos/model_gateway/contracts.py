from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
import hashlib
import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ..system_service_contracts import FIXTURE_UNIT, ServiceFactsV2


MODEL_GATEWAY_SCHEMA_VERSION = "v1"
DeliveryState = Literal["not_sent", "confirmed", "unknown"]
GatewayStatus = Literal["succeeded", "failed", "waiting"]


class StrictGatewayContract(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class DataClassification(StrEnum):
    D0 = "D0"
    D1 = "D1"
    D2 = "D2"
    D3 = "D3"


class FailureCode(StrEnum):
    RATE_LIMITED = "rate_limited"
    PROVIDER_SERVER_ERROR = "provider_server_error"
    PROVIDER_TIMEOUT = "provider_timeout"
    INVALID_JSON = "invalid_json"
    SCHEMA_MISMATCH = "schema_mismatch"
    BUDGET_EXHAUSTED = "budget_exhausted"
    CANCELLED = "cancelled"
    UNKNOWN_DELIVERY = "unknown_delivery"
    KEYRING_LOCKED = "keyring_locked"
    SECRET_NOT_FOUND = "secret_not_found"
    CONFIGURATION_ERROR = "configuration_error"
    ISOLATION_VIOLATION = "isolation_violation"
    TRANSPORT_ERROR = "transport_error"


class SecretRef(StrictGatewayContract):
    """Opaque reference. The secret value is never a field in this contract."""

    schema_version: Literal["v1"] = "v1"
    secret_id: str = Field(pattern=r"^[a-zA-Z0-9][a-zA-Z0-9._-]{2,63}$")
    provider: str = Field(min_length=1, max_length=64)
    kind: Literal["provider_api_key"] = "provider_api_key"

    @property
    def uri(self) -> str:
        return f"secret-service://vibeos/{self.kind}/{self.secret_id}"


class ProviderRoute(StrictGatewayContract):
    schema_version: Literal["v1"] = "v1"
    route_id: str = Field(min_length=1, max_length=80)
    provider: Literal["openai-compatible"] = "openai-compatible"
    model: str = Field(min_length=1, max_length=160)
    base_url: str = Field(min_length=8, max_length=500)
    secret_ref: SecretRef

    @model_validator(mode="after")
    def require_https(self) -> "ProviderRoute":
        if not self.base_url.startswith("https://"):
            raise ValueError("provider base_url must use https")
        return self


class TaskAttemptBinding(StrictGatewayContract):
    task_id: str = Field(min_length=1, max_length=160)
    attempt_id: str = Field(min_length=1, max_length=160)
    attempt_number: int = Field(ge=1)


class CancellationBinding(StrictGatewayContract):
    token_id: str = Field(min_length=1, max_length=160)
    requested: bool = False


class ModelBudget(StrictGatewayContract):
    timeout_seconds: float = Field(gt=0, le=120)
    total_budget_seconds: float = Field(gt=0, le=180)
    max_output_tokens: int = Field(gt=0, le=4096)
    max_total_tokens: int = Field(gt=0, le=32768)

    @model_validator(mode="after")
    def timeout_within_total_budget(self) -> "ModelBudget":
        if self.timeout_seconds > self.total_budget_seconds:
            raise ValueError("provider timeout must not exceed total request budget")
        return self


class ContextItem(StrictGatewayContract):
    name: Literal["service_facts"] = "service_facts"
    media_type: Literal["application/vnd.vibeos.service-facts.v2+json"] = "application/vnd.vibeos.service-facts.v2+json"
    data_classification: Literal[DataClassification.D0] = DataClassification.D0
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    payload: ServiceFactsV2


class ContextManifest(StrictGatewayContract):
    schema_version: Literal["v1"] = "v1"
    items: tuple[ContextItem, ...] = Field(min_length=1, max_length=1)
    highest_data_classification: Literal[DataClassification.D0] = DataClassification.D0


class ResponseSchemaBinding(StrictGatewayContract):
    name: Literal["service_diagnosis"] = "service_diagnosis"
    version: Literal["v1"] = "v1"
    strict: Literal[True] = True


class ModelRequest(StrictGatewayContract):
    schema_version: Literal["v1"] = "v1"
    request_id: str = Field(min_length=1, max_length=200)
    purpose: Literal["service_diagnosis"] = "service_diagnosis"
    operation: Literal["diagnose_fixed_user_service"] = "diagnose_fixed_user_service"
    binding: TaskAttemptBinding
    context: ContextManifest
    response_schema: ResponseSchemaBinding = ResponseSchemaBinding()
    budget: ModelBudget
    cancellation: CancellationBinding


class ServiceActionProposal(StrictGatewayContract):
    action: Literal["start", "restart", "none"]
    unit: Literal["vibeos-goal04-fixture.service"] = FIXTURE_UNIT
    arguments: tuple[str, ...] = Field(default=(), max_length=0)
    effect_level: Literal["E0", "E1"]
    fact_digest: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def effect_matches_action(self) -> "ServiceActionProposal":
        expected = "E0" if self.action == "none" else "E1"
        if self.effect_level != expected:
            raise ValueError("proposal effect does not match deterministic action classification")
        return self


class ServiceDiagnosis(StrictGatewayContract):
    schema_version: Literal["v1"] = "v1"
    diagnosis: str = Field(min_length=1, max_length=2_000)
    confidence: float = Field(ge=0, le=1)
    proposal: ServiceActionProposal


class ModelUsage(StrictGatewayContract):
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    total_tokens: int = Field(ge=0)


class RedactedTransportReceipt(StrictGatewayContract):
    schema_version: Literal["v1"] = "v1"
    transport: Literal["openai-compatible-subprocess"] = "openai-compatible-subprocess"
    provider: Literal["openai-compatible"] = "openai-compatible"
    route_id: str
    provider_request_id: str | None = None
    delivery: DeliveryState
    transport_pid: int = Field(gt=0)
    secret_ref_uri: str
    secret_resolved: bool


class ModelResponse(StrictGatewayContract):
    schema_version: Literal["v1"] = "v1"
    request_id: str
    binding: TaskAttemptBinding
    result: ServiceDiagnosis
    usage: ModelUsage
    receipt: RedactedTransportReceipt


class GatewayFailure(StrictGatewayContract):
    schema_version: Literal["v1"] = "v1"
    request_id: str
    binding: TaskAttemptBinding
    code: FailureCode
    retryable: bool
    delivery: DeliveryState
    safe_message: str = Field(min_length=1, max_length=500)
    wait_event_key: str | None = None
    receipt: RedactedTransportReceipt | None = None


class GatewayResult(StrictGatewayContract):
    schema_version: Literal["v1"] = "v1"
    status: GatewayStatus
    response: ModelResponse | None = None
    failure: GatewayFailure | None = None

    @model_validator(mode="after")
    def exactly_one_outcome(self) -> "GatewayResult":
        if (self.response is None) == (self.failure is None):
            raise ValueError("gateway result requires exactly one response or failure")
        if self.status == "succeeded" and self.response is None:
            raise ValueError("succeeded gateway result requires a response")
        if self.status != "succeeded" and self.failure is None:
            raise ValueError("failed gateway result requires a failure")
        return self


class TransportEnvelope(StrictGatewayContract):
    schema_version: Literal["v1"] = "v1"
    route: ProviderRoute
    request: ModelRequest


class SemanticWorkerInvocation(StrictGatewayContract):
    schema_version: Literal["v1"] = "v1"
    request_id: str
    binding: TaskAttemptBinding
    facts: ServiceFactsV2
    budget: ModelBudget
    cancellation: CancellationBinding


class SemanticWorkerOutput(StrictGatewayContract):
    schema_version: Literal["v1"] = "v1"
    request: ModelRequest
    worker_pid: int = Field(gt=0)
    session_bus_present: bool
    secret_environment_present: bool


def canonical_json(value: BaseModel | dict[str, object]) -> str:
    payload = value.model_dump(mode="json") if isinstance(value, BaseModel) else value
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def facts_digest(facts: ServiceFactsV2) -> str:
    return hashlib.sha256(canonical_json(facts).encode("utf-8")).hexdigest()


def build_model_request(invocation: SemanticWorkerInvocation) -> ModelRequest:
    item = ContextItem(sha256=facts_digest(invocation.facts), payload=invocation.facts)
    return ModelRequest(
        request_id=invocation.request_id,
        binding=invocation.binding,
        context=ContextManifest(items=(item,)),
        budget=invocation.budget,
        cancellation=invocation.cancellation,
    )


def validate_service_diagnosis(request: ModelRequest, diagnosis: ServiceDiagnosis, *, now: datetime | None = None) -> None:
    item = request.context.items[0]
    if facts_digest(item.payload) != item.sha256 or diagnosis.proposal.fact_digest != item.sha256:
        raise ValueError("proposal is not bound to the supplied service facts")
    captured_at = datetime.fromisoformat(item.payload.captured_at.replace("Z", "+00:00"))
    reference = now or datetime.now(timezone.utc)
    if captured_at.tzinfo is None:
        captured_at = captured_at.replace(tzinfo=timezone.utc)
    if (reference - captured_at).total_seconds() > item.payload.ttl_seconds:
        raise ValueError("service facts are stale")
    action = diagnosis.proposal.action
    if action in {"start", "restart"} and item.payload.active_state not in {"inactive", "failed"}:
        raise ValueError("mutating proposal is invalid for the observed service state")
    if action == "restart" and item.payload.active_state != "failed":
        raise ValueError("restart requires an observed failed service")
