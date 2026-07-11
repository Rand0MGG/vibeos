from __future__ import annotations

from dataclasses import asdict
from typing import Any

from ..tool_protocol import ToolExecutionContext, ToolResult, ToolSpec
from ..windows import WindowRegistry


def window_tool_specs(windows: WindowRegistry) -> tuple[ToolSpec, ...]:
    def preview_window_id(name: str) -> str:
        return f"preview:{name.strip().lower() or 'current'}"

    def list_windows(_payload: dict[str, Any], _context: ToolExecutionContext) -> ToolResult:
        items = [asdict(window) for window in windows.list_windows()]
        return ToolResult(
            status="succeeded",
            output={"windows": items, "adapter": "windows.registry", "adapter_status": "succeeded"},
            evidence={"window_count": len(items)},
            state_updates={"selected_target": items[0]["window_id"] if items else None},
        )

    def resolve(payload: dict[str, Any], context: ToolExecutionContext) -> ToolResult:
        name = str(payload.get("name") or "current")
        matches = windows.resolve(name)
        selected = matches[0].window_id if matches else None
        if context.environment.dry_run and not selected:
            selected = preview_window_id(name)
        return ToolResult(
            status="succeeded",
            output={"resolved_window_id": selected, "matches": [asdict(item) for item in matches]},
            evidence={"requested_name": name, "match_count": len(matches), "preview_only": context.environment.dry_run and not matches},
            state_updates={"resolved_window_id": selected},
        )

    def action(tool_id: str, payload: dict[str, Any], context: ToolExecutionContext) -> ToolResult:
        name = str(payload.get("name") or "current")
        resolved = str(context.state.get("resolved_window_id") or "")
        matches = windows.resolve(name) if not resolved else []
        window = next((item for item in windows.list_windows() if item.window_id == resolved), None) if resolved else (matches[0] if matches else None)
        if context.environment.dry_run:
            selected = resolved or (window.window_id if window is not None else preview_window_id(name))
            return ToolResult(
                status="succeeded",
                output={"selected_target": selected, "adapter": "windows.registry", "adapter_status": "dry_run"},
                evidence={"requested_name": name, "window_id": selected, "dry_run": True, "preview_only": window is None},
                state_updates={"selected_target": selected},
            )
        if window is None:
            return ToolResult(status="failed", message=f"no window matched {name!r}", evidence={"requested_name": name}, failure_class="semantic_mismatch")
        handler = {
            "window.focus": windows.focus,
            "window.minimize": windows.minimize,
            "window.maximize": windows.maximize,
            "window.close": windows.close,
        }[tool_id]
        result = handler(window)
        status = str(result.get("status") or "failed")
        if status in {"focused", "minimized", "maximized", "closed"}:
            return ToolResult(
                status="succeeded",
                output={"selected_target": window.window_id, "adapter": "windows.registry", "adapter_status": "succeeded", **result},
                evidence={"requested_name": name, "window_id": window.window_id},
                state_updates={"selected_target": window.window_id},
            )
        return ToolResult(
            status="failed",
            message=str(result.get("error") or f"window action {tool_id} failed"),
            output={"adapter": "windows.registry", "adapter_status": status, **result},
            evidence={"requested_name": name, "window_id": window.window_id},
            failure_class="environment_unreachable",
        )

    return (
        ToolSpec("window.list", "action", "desktop-linux", list_windows),
        ToolSpec("window.resolve", "resolver", "desktop-linux", resolve),
        ToolSpec("window.focus", "action", "desktop-linux", lambda payload, context: action("window.focus", payload, context)),
        ToolSpec("window.minimize", "action", "desktop-linux", lambda payload, context: action("window.minimize", payload, context)),
        ToolSpec("window.maximize", "action", "desktop-linux", lambda payload, context: action("window.maximize", payload, context)),
        ToolSpec("window.close", "action", "desktop-linux", lambda payload, context: action("window.close", payload, context)),
    )
