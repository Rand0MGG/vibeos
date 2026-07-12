from __future__ import annotations

from typing import Any

from ..tool_protocol import ToolExecutionContext, ToolResult, ToolSpec


def media_tool_specs() -> tuple[ToolSpec, ...]:
    """Registered unavailable media actions preserve the capability boundary.

    VibeOS has no local media adapter yet. Returning a registered, typed
    unavailable receipt is safer than routing these actions through a broker
    fallback or an arbitrary external command.
    """

    def unavailable(tool_id: str, payload: dict[str, Any], context: ToolExecutionContext) -> ToolResult:
        target = str(payload.get("query") or tool_id)
        if context.environment.dry_run:
            return ToolResult(
                status="succeeded",
                output={"selected_target": target, "adapter": "media.unavailable", "adapter_status": "dry_run"},
                evidence={"tool_id": tool_id, "target": target, "dry_run": True},
            )
        return ToolResult(
            status="unavailable",
            message="dedicated media execution unavailable on local host",
            output={"selected_target": target, "adapter": "media.unavailable", "adapter_status": "unavailable"},
            evidence={"tool_id": tool_id, "target": target},
            failure_class="environment_unreachable",
        )

    return (
        ToolSpec("media.search", "action", "desktop-linux", lambda payload, context: unavailable("media.search", payload, context)),
        ToolSpec("media.play", "action", "desktop-linux", lambda payload, context: unavailable("media.play", payload, context)),
        ToolSpec("media.pause", "action", "desktop-linux", lambda payload, context: unavailable("media.pause", payload, context)),
    )
