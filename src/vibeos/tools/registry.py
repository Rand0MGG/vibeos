from __future__ import annotations

from dataclasses import dataclass
from ..apps import AppRegistry
from ..clipboard import ClipboardAdapter
from ..portal import PortalAdapter
from ..task_models import TaskPlan, TaskStep
from ..tool_protocol import ToolRegistry, ToolSpec
from ..verifiers import VerifierHarness
from ..windows import WindowRegistry
from .apps import app_tool_specs
from .browser import browser_tool_specs
from .clipboard import clipboard_tool_specs
from .fixtures import fixture_tool_specs
from .media import media_tool_specs
from .system import system_tool_specs
from .system_service import SYSTEM_SERVICE_RECOVERY_ACTION, system_service_tool_specs
from .windows import window_tool_specs


@dataclass(frozen=True)
class ToolCall:
    tool_id: str
    payload: dict[str, object]


class CapabilityRecipeRegistry:
    """Host-owned recipes from a validated task step to registered tools."""

    def calls_for(self, plan: TaskPlan, step: TaskStep) -> tuple[ToolCall, ...]:
        target = dict(step.target)
        target["task_step_id"] = step.id
        action = step.action
        if action == "app.list":
            return (ToolCall("app.list", target),)
        if action == "app.open":
            name = str(target.get("name") or target.get("app") or "")
            payload = {"name": name, "task_step_id": step.id}
            return (ToolCall("apps.resolve_installed", payload), ToolCall("app.open", payload))
        if action == "app.search_history":
            app = str(target.get("app") or target.get("name") or "")
            query = str(target.get("query") or "")
            payload = {"app": app, "query": query, "task_step_id": step.id}
            if plan.selected_route_id == "app_shortcut_search_route" or str(target.get("interaction_surface") or "") == "shortcut":
                return (
                    ToolCall("app.fixture.activate_search_shortcut", payload),
                    ToolCall("app.fixture.enter_search_query", payload),
                    ToolCall("app.fixture.observe_results", payload),
                )
            return (
                ToolCall("app.fixture.locate_search_control", payload),
                ToolCall("app.fixture.enter_search_query", payload),
                ToolCall("app.fixture.observe_results", payload),
            )
        if action == "window.list":
            return (ToolCall("window.list", target),)
        if action in {"window.focus", "window.minimize", "window.maximize", "window.close"}:
            payload = {"name": str(target.get("name") or target.get("window") or "current"), "task_step_id": step.id}
            return (ToolCall("window.resolve", payload), ToolCall(action, payload))
        if action in {"notification.send", "clipboard.write", "portal.open_uri", "system.status", "media.search", "media.play", "media.pause"}:
            return (ToolCall(action, target),)
        if action == SYSTEM_SERVICE_RECOVERY_ACTION:
            return (ToolCall(SYSTEM_SERVICE_RECOVERY_ACTION, target),)
        if action == "browser.open_named_target":
            payload = {
                "name": str(target.get("name") or target.get("target_name") or ""),
                "resolution_mode": str(target.get("resolution_mode") or "direct"),
                "task_step_id": step.id,
            }
            return (ToolCall("browser.resolve_named_target", payload), ToolCall("browser.open_resolved_target", payload))
        if action in {"browser.open_url", "browser.open_site_search"}:
            return (ToolCall(action, target),)
        if action == "browser.search_web":
            calls = [ToolCall("browser.search_web", target)]
            if bool(target.get("follow_search_result")):
                calls.extend((ToolCall("browser.observe_search_results", target), ToolCall("browser.follow_search_result", target)))
            return tuple(calls)
        return ()


def build_tool_registry(
    *,
    apps: AppRegistry,
    windows: WindowRegistry,
    portal: PortalAdapter,
    clipboard: ClipboardAdapter,
    verifiers: VerifierHarness,
    foundation_specs: tuple[ToolSpec, ...],
    system_service_provider=None,
) -> ToolRegistry:
    """Compose domain-owned ToolSpecs without broker-owned handlers."""

    return ToolRegistry(
        (
            *app_tool_specs(apps),
            *window_tool_specs(windows),
            *clipboard_tool_specs(clipboard),
            *system_tool_specs(portal),
            *foundation_specs,
            *browser_tool_specs(portal, verifiers),
            *fixture_tool_specs(),
            *media_tool_specs(),
            *(system_service_tool_specs(system_service_provider) if system_service_provider is not None else ()),
        )
    )
