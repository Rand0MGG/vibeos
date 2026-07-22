from __future__ import annotations

import os
from pathlib import Path


def load_dotenv(path: str | Path | None = None) -> None:
    env_path = path or find_dotenv()
    if not env_path or not os.path.exists(os.fspath(env_path)):
        return
    with open(os.fspath(env_path), encoding="utf-8") as handle:
        lines = handle.read().splitlines()
    for raw_line in lines:
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = strip_env_value(value.strip())
        if key and key not in os.environ and not _is_secret_environment_name(key):
            os.environ[key] = value


def find_dotenv(start: str | Path | None = None) -> str | None:
    explicit = os.environ.get("VIBEOS_ENV_FILE")
    if explicit:
        return os.path.expanduser(explicit)

    current = os.path.abspath(os.fspath(start) if start is not None else os.getcwd())
    while True:
        candidate = os.path.join(current, ".env")
        if os.path.exists(candidate):
            return candidate
        parent = os.path.dirname(current)
        if parent == current:
            break
        current = parent
    return None


def strip_env_value(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


def _is_secret_environment_name(name: str) -> bool:
    normalized = name.upper()
    return any(marker in normalized for marker in ("API_KEY", "TOKEN", "SECRET", "PASSWORD", "CREDENTIAL"))


def env_int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return value if value > 0 else default


def provider_timeout_seconds() -> int:
    load_dotenv()
    return env_int("VIBEOS_PROVIDER_TIMEOUT_SECONDS", 30)


def transport_timeout_seconds() -> int:
    load_dotenv()
    return env_int("VIBEOS_TRANSPORT_TIMEOUT_SECONDS", 45)


def command_model_budget_seconds() -> int:
    load_dotenv()
    configured = env_int("VIBEOS_COMMAND_MODEL_BUDGET_SECONDS", 30)
    return min(configured, max(1, transport_timeout_seconds() - 5))


def portal_timeout_seconds() -> int:
    load_dotenv()
    return env_int("VIBEOS_PORTAL_TIMEOUT_SECONDS", 15)


def search_engine_template() -> str:
    load_dotenv()
    configured = os.environ.get("VIBEOS_SEARCH_ENGINE_URL_TEMPLATE", "").strip()
    if configured:
        return configured
    engine = os.environ.get("VIBEOS_DEFAULT_SEARCH_ENGINE", "").strip().lower()
    if engine == "baidu":
        return "https://www.baidu.com/s?wd={query}"
    if engine == "bing":
        return "https://www.bing.com/search?q={query}"
    if engine == "duckduckgo":
        return "https://duckduckgo.com/?q={query}"
    return "https://www.google.com/search?q={query}"
