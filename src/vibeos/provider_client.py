from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
import os
from time import monotonic
from collections.abc import Iterator

from .config import command_model_budget_seconds, provider_timeout_seconds


_MODEL_DEADLINE: ContextVar[float | None] = ContextVar("vibeos_model_deadline", default=None)


@dataclass(frozen=True)
class OpenAICompatibleProviderConfig:
    provider_name: str
    model_name: str | None
    base_url: str

    @property
    def configured(self) -> bool:
        # Direct provider I/O is disabled until Goal05 migrates this purpose to
        # Model Gateway v1. No ordinary Core process receives a credential.
        return False


@dataclass(frozen=True)
class ProviderJsonObjectResponse:
    request_payload: dict[str, object]
    response_payload: dict[str, object]
    parsed_object: dict[str, object]


def load_openai_compatible_provider_config(
    *,
    default_openai_model: str | None = "unknown-model",
    default_deepseek_model: str | None = "deepseek-v4-flash",
) -> OpenAICompatibleProviderConfig:
    provider_name = os.environ.get("VIBEOS_MODEL_PROVIDER", "openai-compatible").strip().lower()
    if provider_name == "deepseek":
        return OpenAICompatibleProviderConfig(
            provider_name=provider_name,
            base_url=(os.environ.get("DEEPSEEK_BASE_URL") or os.environ.get("OPENAI_BASE_URL") or "https://api.deepseek.com").rstrip("/"),
            model_name=os.environ.get("DEEPSEEK_MODEL") or os.environ.get("OPENAI_MODEL") or default_deepseek_model,
        )
    return OpenAICompatibleProviderConfig(
        provider_name=provider_name,
        base_url=os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/"),
        model_name=os.environ.get("OPENAI_MODEL") or default_openai_model,
    )


def request_json_object(
    *,
    config: OpenAICompatibleProviderConfig,
    system_prompt: str,
    user_content: str,
    max_tokens: int,
    temperature: int | float = 0,
) -> ProviderJsonObjectResponse:
    del config, system_prompt, user_content, max_tokens, temperature
    raise RuntimeError("legacy direct provider transport is disabled; migrate this purpose to Model Gateway v1")


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
