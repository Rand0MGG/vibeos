from __future__ import annotations

from dataclasses import asdict
from typing import Any

from ..apps import AppRegistry
from ..tool_protocol import ToolExecutionContext, ToolResult, ToolSpec


def app_tool_specs(apps: AppRegistry) -> tuple[ToolSpec, ...]:
    def resolve(payload: dict[str, Any], _context: ToolExecutionContext) -> ToolResult:
        name = str(payload.get("name") or "")
        matches = apps.resolve(name)
        selected = matches[0].desktop_id if matches else None
        return ToolResult(
            status="succeeded",
            output={"resolved_desktop_id": selected, "matches": [asdict(item) for item in matches]},
            evidence={"requested_name": name, "match_count": len(matches)},
            state_updates={"resolved_desktop_id": selected},
        )

    def open_app(payload: dict[str, Any], context: ToolExecutionContext) -> ToolResult:
        name = str(payload.get("name") or "")
        selected = str(context.state.get("resolved_desktop_id") or "")
        matches = apps.resolve(name) if not selected else []
        if not selected and matches:
            selected = matches[0].desktop_id
        if not selected:
            return ToolResult(
                status="failed",
                message="no installed app matches the requested target",
                evidence={"requested_name": name},
                failure_class="semantic_mismatch",
            )
        if context.environment.dry_run:
            return ToolResult(
                status="succeeded",
                output={"selected_target": selected, "adapter": "apps.registry", "adapter_status": "dry_run"},
                evidence={"requested_name": name, "desktop_id": selected, "dry_run": True},
                state_updates={"selected_target": selected},
            )
        app = next((item for item in apps.list_apps() if item.desktop_id == selected), None)
        if app is None:
            return ToolResult(
                status="failed",
                message="resolved desktop application is unavailable",
                evidence={"desktop_id": selected},
                failure_class="environment_unreachable",
            )
        adapter_result = apps.open_app(app)
        if adapter_result.get("status") == "opened":
            return ToolResult(
                status="succeeded",
                output={"selected_target": selected, "adapter": "apps.registry", "adapter_status": "succeeded", **adapter_result},
                evidence={"requested_name": name, "desktop_id": selected},
                state_updates={"selected_target": selected},
            )
        return ToolResult(
            status="failed",
            message=str(adapter_result.get("error") or "app open failed"),
            output={"adapter": "apps.registry", "adapter_status": str(adapter_result.get("status") or "failed"), **adapter_result},
            evidence={"requested_name": name, "desktop_id": selected},
            failure_class="environment_unreachable",
        )

    return (
        ToolSpec("apps.resolve_installed", "resolver", "desktop-linux", resolve),
        ToolSpec("app.open", "action", "desktop-linux", open_app),
    )
