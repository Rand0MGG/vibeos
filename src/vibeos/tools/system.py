from __future__ import annotations

from typing import Any

from ..portal import PortalAdapter
from ..tool_protocol import ToolExecutionContext, ToolResult, ToolSpec


def system_tool_specs(portal: PortalAdapter) -> tuple[ToolSpec, ...]:
    def open_uri(payload: dict[str, Any], context: ToolExecutionContext) -> ToolResult:
        uri = str(payload.get("uri") or payload.get("url") or "").strip()
        if not uri:
            return ToolResult(status="failed", message="missing URI", failure_class="semantic_mismatch")
        if context.environment.dry_run:
            return ToolResult(
                status="succeeded",
                output={"selected_target": uri, "uri": uri, "adapter": "portal.open_uri", "adapter_status": "dry_run"},
                evidence={"uri": uri, "dry_run": True},
            )
        result = portal.open_uri(uri)
        status = str(result.get("status") or "failed")
        if status == "opened":
            return ToolResult(
                status="succeeded",
                output={"selected_target": uri, "uri": uri, "adapter": "portal.open_uri", "adapter_status": "succeeded", **result},
                evidence={"uri": uri},
            )
        return ToolResult(
            status="failed",
            message=str(result.get("error") or "portal failed to open URI"),
            output={"uri": uri, "adapter": "portal.open_uri", "adapter_status": status, **result},
            evidence={"uri": uri},
            failure_class="tool_timeout" if status == "timeout" else "environment_unreachable",
        )

    return (ToolSpec("portal.open_uri", "action", "desktop-linux", open_uri),)
