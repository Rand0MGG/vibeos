from __future__ import annotations

from typing import Any

from ..app_fixtures import AppSearchFixture
from ..tool_protocol import ToolExecutionContext, ToolResult, ToolSpec


def fixture_tool_specs() -> tuple[ToolSpec, ...]:
    def fixture_for(context: ToolExecutionContext, app_name: str) -> AppSearchFixture | None:
        catalog = getattr(context.environment, "app_fixture_catalog", {})
        fixture = catalog.get(app_name.lower()) if isinstance(catalog, dict) else None
        return fixture if isinstance(fixture, AppSearchFixture) else None

    def locate(payload: dict[str, Any], context: ToolExecutionContext) -> ToolResult:
        app_name = str(payload.get("app") or "").strip()
        fixture = fixture_for(context, app_name)
        if fixture is None:
            return ToolResult(status="failed", message="no app fixture matched the requested application", evidence={"app": app_name}, failure_class="environment_unreachable")
        if not fixture.has_control("search_box"):
            return ToolResult(status="failed", message="structured search control was not visible in the app fixture", evidence={"app": app_name, "fixture_id": fixture.fixture_id}, failure_class="semantic_mismatch")
        return ToolResult(status="succeeded", output={"search_control_id": "search_box"}, evidence={"app": app_name, "fixture_id": fixture.fixture_id, "control_id": "search_box"}, state_updates={"search_control_id": "search_box"})

    def shortcut(payload: dict[str, Any], context: ToolExecutionContext) -> ToolResult:
        app_name = str(payload.get("app") or "").strip()
        fixture = fixture_for(context, app_name)
        if fixture is None:
            return ToolResult(status="failed", message="no app fixture matched the requested application", evidence={"app": app_name}, failure_class="environment_unreachable")
        if not fixture.shortcut_search_enabled:
            return ToolResult(status="failed", message="shortcut search mode is unavailable in the app fixture", evidence={"app": app_name, "fixture_id": fixture.fixture_id}, failure_class="semantic_mismatch")
        return ToolResult(status="succeeded", output={"search_mode": "shortcut"}, evidence={"app": app_name, "fixture_id": fixture.fixture_id, "shortcut": "Ctrl+K"}, state_updates={"search_mode": "shortcut"})

    def enter(payload: dict[str, Any], context: ToolExecutionContext) -> ToolResult:
        app_name, query = str(payload.get("app") or "").strip(), str(payload.get("query") or "").strip()
        fixture = fixture_for(context, app_name)
        if fixture is None:
            return ToolResult(status="failed", message="no app fixture matched the requested application", evidence={"app": app_name}, failure_class="environment_unreachable")
        if not query:
            return ToolResult(status="failed", message="missing app search query", failure_class="semantic_mismatch")
        if not context.state.get("search_control_id") and context.state.get("search_mode") != "shortcut":
            return ToolResult(status="failed", message="search query entry requires a visible control or active shortcut mode", evidence={"app": app_name}, failure_class="semantic_mismatch")
        results = fixture.results_for(query)
        return ToolResult(status="succeeded", output={"search_query": query, "result_count": len(results)}, evidence={"app": app_name, "fixture_id": fixture.fixture_id, "query": query, "result_count": len(results)}, state_updates={"search_query": query, "observed_results": results})

    def observe(payload: dict[str, Any], context: ToolExecutionContext) -> ToolResult:
        results = tuple(str(item) for item in context.state.get("observed_results", ()))
        return ToolResult(status="succeeded", output={"observed_results": results}, evidence={"app": str(payload.get("app") or "").strip(), "observed_results": results}, state_updates={"observed_results": results})

    def verify(payload: dict[str, Any], context: ToolExecutionContext) -> ToolResult:
        query = str(payload.get("query") or context.state.get("search_query") or "").strip()
        observed = tuple(str(item) for item in context.state.get("observed_results", ()))
        accepted = bool(query) and any(item.lower() == query.lower() for item in observed)
        return ToolResult(status="succeeded" if accepted else "failed", message="app verifier observed the requested target in search results" if accepted else "app verifier did not observe the requested target in search results", evidence={"query": query, "observed_results": observed}, accepted=accepted, failure_class="acceptance_failed" if not accepted else "none")

    return (
        ToolSpec("app.fixture.locate_search_control", "resolver", "desktop-linux", locate),
        ToolSpec("app.fixture.activate_search_shortcut", "action", "desktop-linux", shortcut),
        ToolSpec("app.fixture.enter_search_query", "action", "desktop-linux", enter),
        ToolSpec("app.fixture.observe_results", "observer", "desktop-linux", observe),
        ToolSpec("app.fixture.verify_target_presence", "verifier", "desktop-linux", verify),
    )
