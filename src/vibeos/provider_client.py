from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
import json
import os
from time import monotonic
import urllib.request
from collections.abc import Iterator

from .config import command_model_budget_seconds, load_dotenv, provider_timeout_seconds


_MODEL_DEADLINE: ContextVar[float | None] = ContextVar("vibeos_model_deadline", default=None)


@dataclass(frozen=True)
class OpenAICompatibleProviderConfig:
    provider_name: str
    model_name: str | None
    api_key: str | None
    base_url: str

    @property
    def configured(self) -> bool:
        return bool(self.api_key and self.model_name)


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
    load_dotenv()
    provider_name = os.environ.get("VIBEOS_MODEL_PROVIDER", "openai-compatible").strip().lower()
    if provider_name == "deepseek":
        return OpenAICompatibleProviderConfig(
            provider_name=provider_name,
            api_key=os.environ.get("DEEPSEEK_API_KEY") or os.environ.get("OPENAI_API_KEY"),
            base_url=(os.environ.get("DEEPSEEK_BASE_URL") or os.environ.get("OPENAI_BASE_URL") or "https://api.deepseek.com").rstrip("/"),
            model_name=os.environ.get("DEEPSEEK_MODEL") or os.environ.get("OPENAI_MODEL") or default_deepseek_model,
        )
    return OpenAICompatibleProviderConfig(
        provider_name=provider_name,
        api_key=os.environ.get("OPENAI_API_KEY"),
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
    if not config.configured:
        raise ValueError("provider is not fully configured")
    request_payload = {
        "model": config.model_name,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ],
        "temperature": temperature,
        "response_format": {"type": "json_object"},
        "max_tokens": max_tokens,
    }
    request = urllib.request.Request(
        f"{config.base_url}/chat/completions",
        data=json.dumps(request_payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {config.api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    timeout = bounded_provider_timeout_seconds()
    with urllib.request.urlopen(request, timeout=timeout) as response:
        response_payload = json.loads(response.read().decode("utf-8"))
    content = response_payload["choices"][0]["message"]["content"]
    parsed = json.loads(content)
    if not isinstance(parsed, dict):
        raise ValueError("provider response must be a JSON object")
    return ProviderJsonObjectResponse(
        request_payload=request_payload,
        response_payload=response_payload,
        parsed_object=parsed,
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
