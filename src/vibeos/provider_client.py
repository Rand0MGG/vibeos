from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from hashlib import sha256
import os
from time import monotonic
from collections.abc import Iterator
from uuid import uuid4

from .config import command_model_budget_seconds, provider_timeout_seconds
from .model_gateway.contracts import (
    CancellationBinding,
    CompatibilityPurpose,
    FailureCode,
    ModelBudget,
    ProviderRoute,
    TaskAttemptBinding,
)
from .model_gateway.gateway import ModelGateway
from .model_gateway.routes import route_preferences_from_environment, select_provider_route
from .model_gateway.secrets import ProviderRouteRepository
from .task_trace import current_trace_session


_MODEL_DEADLINE: ContextVar[float | None] = ContextVar("vibeos_model_deadline", default=None)


@dataclass(frozen=True)
class OpenAICompatibleProviderConfig:
    provider_name: str
    model_name: str | None
    base_url: str
    route: ProviderRoute | None = None

    @property
    def configured(self) -> bool:
        return self.route is not None and bool(self.route.model)


@dataclass(frozen=True)
class ProviderJsonObjectResponse:
    request_payload: dict[str, object]
    response_payload: dict[str, object]
    parsed_object: dict[str, object]


def load_openai_compatible_provider_config(
    *,
    default_openai_model: str | None = "unknown-model",
    default_deepseek_model: str | None = "deepseek-v4-flash",
    route_repository: ProviderRouteRepository | None = None,
) -> OpenAICompatibleProviderConfig:
    preferences = route_preferences_from_environment(
        default_openai_model=default_openai_model,
        default_deepseek_model=default_deepseek_model,
    )
    if preferences.provider_name == "local":
        return OpenAICompatibleProviderConfig(
            provider_name=preferences.provider_name,
            base_url=preferences.base_url,
            model_name=None,
        )
    config = OpenAICompatibleProviderConfig(
        provider_name=preferences.provider_name,
        base_url=preferences.base_url,
        model_name=preferences.model_name,
    )
    route = select_provider_route(preferences, route_repository=route_repository)
    if route is None:
        return config
    routed_provider = "deepseek" if "deepseek" in route.base_url.lower() else config.provider_name
    return OpenAICompatibleProviderConfig(
        provider_name=routed_provider,
        base_url=route.base_url,
        model_name=route.model,
        route=route,
    )


def request_json_object(
    *,
    config: OpenAICompatibleProviderConfig,
    system_prompt: str,
    user_content: str,
    max_tokens: int,
    purpose: CompatibilityPurpose,
    temperature: int | float = 0,
) -> ProviderJsonObjectResponse:
    if config.route is None:
        raise ValueError("Model Gateway route is not configured")
    timeout = bounded_provider_timeout_seconds()
    total_budget = remaining_model_budget_seconds()
    request_seed = f"{purpose}:{system_prompt}:{user_content}:{uuid4().hex}"
    request_id = f"modelreq_{sha256(request_seed.encode('utf-8')).hexdigest()[:24]}"
    trace = current_trace_session()
    task_id = trace.run_id if trace is not None else f"adhoc_{sha256(user_content.encode('utf-8')).hexdigest()[:16]}"
    result = ModelGateway().request_json_object(
        route=config.route,
        binding=TaskAttemptBinding(
            task_id=task_id,
            attempt_id=f"attempt_{request_id}",
            attempt_number=1,
        ),
        purpose=purpose,
        system_prompt=system_prompt,
        user_content=user_content,
        temperature=float(temperature),
        budget=ModelBudget(
            timeout_seconds=min(timeout, total_budget),
            total_budget_seconds=total_budget,
            max_output_tokens=max_tokens,
            max_total_tokens=min(32_768, max(4_096, max_tokens * 8)),
        ),
        cancellation=CancellationBinding(token_id=f"cancel_{request_id}"),
        request_id=request_id,
    )
    if result.response is None:
        failure = result.failure
        if failure is not None and failure.code in {FailureCode.PROVIDER_TIMEOUT, FailureCode.BUDGET_EXHAUSTED}:
            raise TimeoutError(failure.safe_message)
        message = failure.safe_message if failure is not None else "Model Gateway failed closed"
        raise ValueError(message)
    return ProviderJsonObjectResponse(
        request_payload=result.response.request_payload,
        response_payload=result.response.response_payload,
        parsed_object=result.response.parsed_object,
    )


def env_flag_enabled(env_name: str) -> bool:
    raw = os.environ.get(env_name, "1").strip().lower()
    return raw not in {"0", "false", "no", "off"}


@contextmanager
def model_request_budget() -> Iterator[None]:
    current = _MODEL_DEADLINE.get()
    if current is not None:
        yield
        return
    token = _MODEL_DEADLINE.set(monotonic() + command_model_budget_seconds())
    try:
        yield
    finally:
        _MODEL_DEADLINE.reset(token)


def bounded_provider_timeout_seconds() -> float:
    configured = float(provider_timeout_seconds())
    deadline = _MODEL_DEADLINE.get()
    if deadline is None:
        return configured
    remaining = deadline - monotonic()
    if remaining <= 0:
        raise TimeoutError("command model-call budget was exhausted")
    return min(configured, max(0.1, remaining))


def remaining_model_budget_seconds() -> float:
    deadline = _MODEL_DEADLINE.get()
    if deadline is None:
        return float(min(180, command_model_budget_seconds()))
    remaining = deadline - monotonic()
    if remaining <= 0:
        raise TimeoutError("command model-call budget was exhausted")
    return min(180.0, max(0.1, remaining))
