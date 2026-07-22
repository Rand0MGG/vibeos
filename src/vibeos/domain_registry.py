from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Callable
from urllib.parse import urlparse

from .browser_state import browser_context_snapshot, record_browser_window_observation
from .capabilities import CAPABILITIES
from .domain_models import ContextBudget, DomainPack
from .models import utc_now_iso
from .windows import WindowRegistry


ContextProducer = Callable[[], dict[str, Any]]


@dataclass(frozen=True)
class RouteDefinition:
    route_id: str
    domain_id: str
    builder_name: str
    required_capability_ids: tuple[str, ...]
    required_context_package_ids: tuple[str, ...]
    default_verifier_ids: tuple[str, ...]
    preconditions: tuple[str, ...] = ()


@dataclass(frozen=True)
class ContextPackageDefinition:
    package_id: str
    producer: ContextProducer
    budget: ContextBudget
    schema_name: str
    redaction_policy: str


def default_session_context() -> dict[str, Any]:
    return {
        "session_id": "local-session",
        "locale": "und",
        "transport": "local",
        "captured_at": utc_now_iso(),
        "notes": (),
    }


def default_window_context() -> dict[str, Any]:
    return {
        "status": "unavailable",
        "windows": (),
        "captured_at": utc_now_iso(),
    }


def default_browser_context() -> dict[str, Any]:
    snapshot = browser_context_snapshot()
    windows = WindowRegistry().list_windows()
    browser_window = None
    if snapshot.get("requested_url") or snapshot.get("active_url"):
        browser_window = next(
            (window for window in windows if window.focused and _looks_like_browser_app(window.app_id, window.title)),
            None,
        )
        if browser_window is None:
            browser_window = next((window for window in windows if _looks_like_browser_app(window.app_id, window.title)), None)
    if browser_window is not None and str(snapshot.get("status") or "") in {"loaded", "failed", "timeout"}:
        record_browser_window_observation(page_title=browser_window.title, app_id=browser_window.app_id)
        snapshot["page_title"] = browser_window.title
        snapshot["app_id"] = browser_window.app_id
    active_url = snapshot.get("active_url")
    known_sites = _known_sites(active_url, None)
    error_state = snapshot.get("error_state") or _infer_browser_error_state(str(snapshot.get("page_title") or ""))
    status = str(snapshot.get("status") or "unavailable")
    return {
        "run_id": snapshot.get("run_id"),
        "attempt_id": snapshot.get("attempt_id"),
        "route_id": snapshot.get("route_id"),
        "status": status,
        "active_url": active_url,
        "requested_url": snapshot.get("requested_url"),
        "requested_query": snapshot.get("requested_query"),
        "page_title": snapshot.get("page_title"),
        "app_id": snapshot.get("app_id"),
        "known_sites": known_sites,
        "query": snapshot.get("query"),
        "error_state": error_state,
        "adapter": snapshot.get("adapter"),
        "captured_at": snapshot.get("captured_at") or utc_now_iso(),
    }


def default_media_context() -> dict[str, Any]:
    return {
        "status": "unavailable",
        "playback_state": "unavailable",
        "title": None,
        "captured_at": utc_now_iso(),
    }


def default_system_context() -> dict[str, Any]:
    return {
        "status": "ok",
        "runtime": "local",
        "captured_at": utc_now_iso(),
    }


def _looks_like_browser_app(app_id: str, title: str) -> bool:
    haystack = f"{app_id} {title}".lower()
    return any(token in haystack for token in ("firefox", "chrome", "chromium", "browser", "edge"))


def _known_sites(active_url: Any, site: Any) -> tuple[str, ...]:
    items: list[str] = []
    if isinstance(site, str) and site.strip():
        items.append(site.strip())
    if isinstance(active_url, str) and active_url.strip():
        parsed = urlparse(active_url)
        if parsed.netloc:
            items.append(parsed.netloc)
    return tuple(dict.fromkeys(items))


def _infer_browser_error_state(title: str) -> str | None:
    lowered = title.lower()
    patterns = {
        "dns_error": ("server not found", "site can’t be reached", "site can't be reached", "无法访问此网站", "找不到服务器"),
        "network_error": ("problem loading page", "network error", "连接已重置", "连接超时"),
        "tls_error": ("warning: potential security risk", "your connection is not private", "证书", "隐私错误"),
        "http_404": ("404", "not found", "页面不存在"),
        "blocked": ("blocked", "forbidden", "access denied", "拒绝访问"),
    }
    for error_state, phrases in patterns.items():
        if any(phrase in lowered for phrase in phrases):
            return error_state
    return None


class ContextPackageRegistry:
    def __init__(self, packages: tuple[ContextPackageDefinition, ...]) -> None:
        self._packages = {package.package_id: package for package in packages}

    def get(self, package_id: str) -> ContextPackageDefinition | None:
        return self._packages.get(package_id)

    def ids(self) -> tuple[str, ...]:
        return tuple(sorted(self._packages))

    def validate(self) -> tuple[str, ...]:
        errors: list[str] = []
        for package in self._packages.values():
            if package.budget.max_bytes <= 0:
                errors.append(f"context package {package.package_id!r} must define max_bytes > 0")
            if package.budget.ttl_ms <= 0:
                errors.append(f"context package {package.package_id!r} must define ttl_ms > 0")
            if not callable(package.producer):
                errors.append(f"context package {package.package_id!r} producer must be callable")
        return tuple(errors)


class DomainRegistry:
    def __init__(
        self,
        packs: tuple[DomainPack, ...],
        routes: tuple[RouteDefinition, ...],
        context_registry: ContextPackageRegistry,
        known_verifier_ids: tuple[str, ...],
    ) -> None:
        self._packs = {pack.domain_id: pack for pack in packs}
        self._routes = {route.route_id: route for route in routes}
        self._context_registry = context_registry
        self._known_verifier_ids = set(known_verifier_ids)

    @property
    def context_registry(self) -> ContextPackageRegistry:
        return self._context_registry

    def get_pack(self, domain_id: str) -> DomainPack | None:
        return self._packs.get(domain_id)

    def get_route(self, route_id: str) -> RouteDefinition | None:
        return self._routes.get(route_id)

    def packs(self) -> tuple[DomainPack, ...]:
        return tuple(self._packs[domain_id] for domain_id in sorted(self._packs))

    def routes_for_domains(self, domain_ids: tuple[str, ...]) -> tuple[RouteDefinition, ...]:
        allowed = set(domain_ids)
        return tuple(route for route in self._routes.values() if route.domain_id in allowed)

    def validate(self) -> tuple[str, ...]:
        errors = list(self._context_registry.validate())
        known_context_ids = set(self._context_registry.ids())
        known_route_ids = set(self._routes)
        known_domains = set(self._packs)
        for pack in self._packs.values():
            if not pack.route_ids:
                errors.append(f"domain pack {pack.domain_id!r} must expose at least one route")
            for route_id in pack.route_ids:
                if route_id not in known_route_ids:
                    errors.append(f"domain pack {pack.domain_id!r} references unknown route {route_id!r}")
            for package_id in pack.allowed_context_package_ids:
                if package_id not in known_context_ids:
                    errors.append(f"domain pack {pack.domain_id!r} references unknown context package {package_id!r}")
            for fallback_domain_id in pack.optional_fallback_domain_ids:
                if fallback_domain_id not in known_domains:
                    errors.append(f"domain pack {pack.domain_id!r} references unknown fallback domain {fallback_domain_id!r}")
            for verifier_id in pack.default_verifier_ids:
                if verifier_id not in self._known_verifier_ids:
                    errors.append(f"domain pack {pack.domain_id!r} references unknown verifier {verifier_id!r}")

        for route in self._routes.values():
            if route.domain_id not in known_domains:
                errors.append(f"route {route.route_id!r} references unknown domain {route.domain_id!r}")
            for capability_id in route.required_capability_ids:
                if capability_id not in CAPABILITIES:
                    errors.append(f"route {route.route_id!r} references unknown capability {capability_id!r}")
            for package_id in route.required_context_package_ids:
                if package_id not in known_context_ids:
                    errors.append(f"route {route.route_id!r} references unknown context package {package_id!r}")
            for verifier_id in route.default_verifier_ids:
                if verifier_id not in self._known_verifier_ids:
                    errors.append(f"route {route.route_id!r} references unknown verifier {verifier_id!r}")
        return tuple(errors)


def default_context_registry() -> ContextPackageRegistry:
    packages = (
        ContextPackageDefinition(
            package_id="session_context",
            producer=default_session_context,
            budget=ContextBudget(max_items=8, max_bytes=2048, ttl_ms=30_000, redaction_policy="none"),
            schema_name="session_context_v1",
            redaction_policy="none",
        ),
        ContextPackageDefinition(
            package_id="window_context",
            producer=default_window_context,
            budget=ContextBudget(max_items=16, max_bytes=4096, ttl_ms=10_000, redaction_policy="window_titles_redacted", sensitive_fields=("titles",)),
            schema_name="window_context_v1",
            redaction_policy="window_titles_redacted",
        ),
        ContextPackageDefinition(
            package_id="browser_context",
            producer=default_browser_context,
            budget=ContextBudget(
                max_items=8, max_bytes=3072, ttl_ms=10_000, redaction_policy="urls_redacted", sensitive_fields=("active_url", "requested_url")
            ),
            schema_name="browser_context_v1",
            redaction_policy="urls_redacted",
        ),
        ContextPackageDefinition(
            package_id="media_context",
            producer=default_media_context,
            budget=ContextBudget(max_items=8, max_bytes=2048, ttl_ms=5_000, redaction_policy="titles_redacted", sensitive_fields=("title",)),
            schema_name="media_context_v1",
            redaction_policy="titles_redacted",
        ),
        ContextPackageDefinition(
            package_id="system_context",
            producer=default_system_context,
            budget=ContextBudget(max_items=8, max_bytes=2048, ttl_ms=10_000, redaction_policy="none"),
            schema_name="system_context_v1",
            redaction_policy="none",
        ),
    )
    return ContextPackageRegistry(packages)


def default_domain_registry(known_verifier_ids: tuple[str, ...]) -> DomainRegistry:
    apps_routes = (
        RouteDefinition(
            route_id="apps_list_route",
            domain_id="apps",
            builder_name="build_apps_list_plan",
            required_capability_ids=("app.list",),
            required_context_package_ids=("session_context",),
            default_verifier_ids=(),
        ),
        RouteDefinition(
            route_id="apps_open_route",
            domain_id="apps",
            builder_name="build_apps_open_plan",
            required_capability_ids=("app.open",),
            required_context_package_ids=("session_context",),
            default_verifier_ids=(),
        ),
    )
    app_interaction_routes = (
        RouteDefinition(
            route_id="app_structured_search_route",
            domain_id="app_interaction",
            builder_name="build_app_structured_search_plan",
            required_capability_ids=("app.search_history",),
            required_context_package_ids=("session_context",),
            default_verifier_ids=(),
        ),
        RouteDefinition(
            route_id="app_shortcut_search_route",
            domain_id="app_interaction",
            builder_name="build_app_shortcut_search_plan",
            required_capability_ids=("app.search_history",),
            required_context_package_ids=("session_context",),
            default_verifier_ids=(),
        ),
    )
    window_routes = (
        RouteDefinition(
            route_id="window_list_route",
            domain_id="window_management",
            builder_name="build_window_list_plan",
            required_capability_ids=("window.list",),
            required_context_package_ids=("session_context", "window_context"),
            default_verifier_ids=(),
        ),
        RouteDefinition(
            route_id="window_focus_route",
            domain_id="window_management",
            builder_name="build_window_focus_plan",
            required_capability_ids=("window.focus",),
            required_context_package_ids=("session_context", "window_context"),
            default_verifier_ids=(),
        ),
        RouteDefinition(
            route_id="window_minimize_route",
            domain_id="window_management",
            builder_name="build_window_state_plan",
            required_capability_ids=("window.minimize",),
            required_context_package_ids=("session_context", "window_context"),
            default_verifier_ids=(),
        ),
        RouteDefinition(
            route_id="window_maximize_route",
            domain_id="window_management",
            builder_name="build_window_state_plan",
            required_capability_ids=("window.maximize",),
            required_context_package_ids=("session_context", "window_context"),
            default_verifier_ids=(),
        ),
        RouteDefinition(
            route_id="window_close_route",
            domain_id="window_management",
            builder_name="build_window_close_plan",
            required_capability_ids=("window.close",),
            required_context_package_ids=("session_context", "window_context"),
            default_verifier_ids=(),
        ),
    )
    clipboard_routes = (
        RouteDefinition(
            route_id="clipboard_write_route",
            domain_id="clipboard",
            builder_name="build_clipboard_write_plan",
            required_capability_ids=("clipboard.write",),
            required_context_package_ids=("session_context",),
            default_verifier_ids=(),
        ),
    )
    notification_routes = (
        RouteDefinition(
            route_id="notification_send_route",
            domain_id="notification",
            builder_name="build_notification_send_plan",
            required_capability_ids=("notification.send",),
            required_context_package_ids=("session_context",),
            default_verifier_ids=(),
        ),
    )
    system_routes = (
        RouteDefinition(
            route_id="system_status_route",
            domain_id="system_observation",
            builder_name="build_system_status_plan",
            required_capability_ids=("system.status",),
            required_context_package_ids=("session_context", "system_context"),
            default_verifier_ids=(),
        ),
    )
    browser_routes = (
        RouteDefinition(
            route_id="portal_open_uri_route",
            domain_id="browser",
            builder_name="build_browser_open_url_plan",
            required_capability_ids=("portal.open_uri",),
            required_context_package_ids=("session_context", "browser_context"),
            default_verifier_ids=("browser_url_opened",),
        ),
        RouteDefinition(
            route_id="browser_open_url_route",
            domain_id="browser",
            builder_name="build_browser_open_url_plan",
            required_capability_ids=("browser.open_url",),
            required_context_package_ids=("session_context", "browser_context"),
            default_verifier_ids=("browser_url_opened",),
        ),
        RouteDefinition(
            route_id="browser_named_target_route",
            domain_id="browser",
            builder_name="build_browser_open_url_plan",
            required_capability_ids=("browser.open_named_target",),
            required_context_package_ids=("session_context", "browser_context"),
            default_verifier_ids=("browser_goal_page_identity",),
        ),
        RouteDefinition(
            route_id="browser_search_web_route",
            domain_id="browser",
            builder_name="build_browser_search_web_plan",
            required_capability_ids=("browser.search_web",),
            required_context_package_ids=("session_context", "browser_context"),
            default_verifier_ids=("browser_search_route_completed",),
        ),
        RouteDefinition(
            route_id="browser_site_search_route",
            domain_id="browser",
            builder_name="build_browser_site_search_plan",
            required_capability_ids=("browser.open_site_search",),
            required_context_package_ids=("session_context", "browser_context"),
            default_verifier_ids=("browser_search_route_completed",),
        ),
    )
    media_routes = (
        RouteDefinition(
            route_id="media_search_route",
            domain_id="media",
            builder_name="build_media_search_plan",
            required_capability_ids=("media.search",),
            required_context_package_ids=("session_context", "media_context"),
            default_verifier_ids=(),
        ),
        RouteDefinition(
            route_id="media_play_route",
            domain_id="media",
            builder_name="build_media_play_plan",
            required_capability_ids=("app.open", "media.search", "media.play"),
            required_context_package_ids=("session_context", "media_context"),
            default_verifier_ids=("media_playback_state_available",),
        ),
        RouteDefinition(
            route_id="media_pause_route",
            domain_id="media",
            builder_name="build_media_pause_plan",
            required_capability_ids=("media.pause",),
            required_context_package_ids=("session_context", "media_context"),
            default_verifier_ids=("media_playback_state_available",),
        ),
        RouteDefinition(
            route_id="browser_music_search_route",
            domain_id="browser",
            builder_name="build_browser_media_fallback_plan",
            required_capability_ids=("browser.search_web",),
            required_context_package_ids=("session_context", "browser_context"),
            default_verifier_ids=("browser_search_route_completed",),
        ),
    )
    packs = (
        DomainPack(
            domain_id="apps",
            label="Apps",
            route_ids=tuple(route.route_id for route in apps_routes),
            allowed_context_package_ids=("session_context",),
            capability_families=("apps",),
            effect_defaults={"list": "E0", "open": "E1"},
            default_verifier_ids=(),
            optional_fallback_domain_ids=(),
        ),
        DomainPack(
            domain_id="app_interaction",
            label="App Interaction",
            route_ids=tuple(route.route_id for route in app_interaction_routes),
            allowed_context_package_ids=("session_context",),
            capability_families=("apps", "app_interaction"),
            effect_defaults={"search_history": "E1"},
            default_verifier_ids=(),
            optional_fallback_domain_ids=(),
        ),
        DomainPack(
            domain_id="window_management",
            label="Window Management",
            route_ids=tuple(route.route_id for route in window_routes),
            allowed_context_package_ids=("session_context", "window_context"),
            capability_families=("windows",),
            effect_defaults={"list": "E0", "focus": "E1", "minimize": "E1", "maximize": "E1", "close": "E3"},
            default_verifier_ids=(),
            optional_fallback_domain_ids=(),
        ),
        DomainPack(
            domain_id="clipboard",
            label="Clipboard",
            route_ids=("clipboard_write_route",),
            allowed_context_package_ids=("session_context",),
            capability_families=("clipboard",),
            effect_defaults={"write": "E1"},
            default_verifier_ids=(),
            optional_fallback_domain_ids=(),
        ),
        DomainPack(
            domain_id="notification",
            label="Notification",
            route_ids=("notification_send_route",),
            allowed_context_package_ids=("session_context",),
            capability_families=("notification",),
            effect_defaults={"send": "E1"},
            default_verifier_ids=(),
            optional_fallback_domain_ids=(),
        ),
        DomainPack(
            domain_id="system_observation",
            label="System Observation",
            route_ids=("system_status_route",),
            allowed_context_package_ids=("session_context", "system_context"),
            capability_families=("system",),
            effect_defaults={"status": "E0"},
            default_verifier_ids=(),
            optional_fallback_domain_ids=(),
        ),
        DomainPack(
            domain_id="browser",
            label="Browser",
            route_ids=tuple(route.route_id for route in browser_routes + media_routes if route.domain_id == "browser"),
            allowed_context_package_ids=("session_context", "window_context", "browser_context"),
            capability_families=("browser", "portal"),
            effect_defaults={"open_url": "E1", "search_web": "E1"},
            default_verifier_ids=("browser_url_opened", "browser_search_route_completed"),
            optional_fallback_domain_ids=(),
        ),
        DomainPack(
            domain_id="media",
            label="Media",
            route_ids=("media_play_route",),
            allowed_context_package_ids=("session_context", "media_context"),
            capability_families=("media",),
            effect_defaults={"play": "E1", "pause": "E1"},
            default_verifier_ids=("media_playback_state_available",),
            optional_fallback_domain_ids=("browser",),
        ),
    )
    registry = DomainRegistry(
        packs=packs,
        routes=apps_routes + app_interaction_routes + window_routes + clipboard_routes + notification_routes + system_routes + browser_routes + media_routes,
        context_registry=default_context_registry(),
        known_verifier_ids=known_verifier_ids,
    )
    errors = registry.validate()
    if errors:
        raise ValueError(json.dumps({"errors": errors}, ensure_ascii=False, indent=2))
    return registry
