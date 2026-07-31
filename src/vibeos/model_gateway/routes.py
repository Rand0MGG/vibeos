from __future__ import annotations

from dataclasses import dataclass
import os

from .contracts import ProviderRoute
from .secrets import ProviderRouteRepository


@dataclass(frozen=True)
class ProviderRoutePreferences:
    provider_name: str
    model_name: str | None
    base_url: str


def route_preferences_from_environment(
    *,
    default_openai_model: str | None = "unknown-model",
    default_deepseek_model: str | None = "deepseek-v4-flash",
) -> ProviderRoutePreferences:
    provider_name = os.environ.get("VIBEOS_MODEL_PROVIDER", "openai-compatible").strip().lower()
    if provider_name == "local":
        return ProviderRoutePreferences(provider_name, None, "https://localhost.invalid")
    if provider_name == "deepseek":
        return ProviderRoutePreferences(
            provider_name,
            os.environ.get("DEEPSEEK_MODEL") or os.environ.get("OPENAI_MODEL") or default_deepseek_model,
            (os.environ.get("DEEPSEEK_BASE_URL") or os.environ.get("OPENAI_BASE_URL") or "https://api.deepseek.com").rstrip("/"),
        )
    return ProviderRoutePreferences(
        provider_name,
        os.environ.get("OPENAI_MODEL") or default_openai_model,
        os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/"),
    )


def select_provider_route(
    preferences: ProviderRoutePreferences,
    *,
    route_repository: ProviderRouteRepository | None = None,
) -> ProviderRoute | None:
    repository = route_repository or ProviderRouteRepository()
    explicit_route_id = os.environ.get("VIBEOS_MODEL_ROUTE", "").strip()
    try:
        if explicit_route_id:
            return repository.get(explicit_route_id)
        routes = repository.list_routes()
    except (OSError, ValueError):
        return None
    matching = tuple(
        route
        for route in routes
        if (preferences.model_name is None or route.model == preferences.model_name) and route.base_url.rstrip("/") == preferences.base_url.rstrip("/")
    )
    if len(matching) == 1:
        return matching[0]
    if len(routes) == 1:
        return routes[0]
    return None
