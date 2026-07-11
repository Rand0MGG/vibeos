from __future__ import annotations

from collections.abc import Callable
from typing import Any

from ..portal import PortalAdapter
from ..tool_protocol import ToolExecutionContext, ToolResult, ToolSpec


def system_tool_specs(portal: PortalAdapter, capabilities: Callable[[], dict[str, object]]) -> tuple[ToolSpec, ...]:
    def status(_payload: dict[str, Any], _context: ToolExecutionContext) -> ToolResult:
        payload = {"portal": portal.status(), **capabilities()}
        return ToolResult(
            status="succeeded",
            output={"adapter": "system.status", "adapter_status": "succeeded", **payload},
            evidence={"capability_count": len(payload.get("capabilities", []))},
        )

    return (ToolSpec("system.status", "action", "desktop-linux", status),)
