from __future__ import annotations

from typing import Any

from ..clipboard import ClipboardAdapter
from ..tool_protocol import ToolExecutionContext, ToolResult, ToolSpec


def clipboard_tool_specs(clipboard: ClipboardAdapter) -> tuple[ToolSpec, ...]:
    def write(payload: dict[str, Any], context: ToolExecutionContext) -> ToolResult:
        text = str(payload.get("text") or payload.get("content") or "").strip()
        if not text:
            return ToolResult(status="failed", message="missing clipboard text", failure_class="semantic_mismatch")
        if context.environment.dry_run:
            return ToolResult(
                status="succeeded",
                output={"selected_target": "clipboard", "adapter": "clipboard.write", "adapter_status": "dry_run"},
                evidence={"text_length": len(text), "dry_run": True},
                state_updates={"selected_target": "clipboard"},
            )
        result = clipboard.write(text)
        status = str(result.get("status") or "failed")
        if status == "written":
            return ToolResult(
                status="succeeded",
                output={"selected_target": "clipboard", "adapter": str(result.get("adapter") or "clipboard.helper"), "capability_adapter": "clipboard.write", "adapter_status": "succeeded", **result},
                evidence={"text_length": len(text)},
                state_updates={"selected_target": "clipboard"},
            )
        return ToolResult(
            status="failed",
            message=str(result.get("error") or "clipboard write failed"),
            output={"adapter": str(result.get("adapter") or "clipboard.helper"), "capability_adapter": "clipboard.write", "adapter_status": status, **result},
            evidence={"text_length": len(text)},
            failure_class="tool_timeout" if status == "timeout" else "environment_unreachable" if status == "unavailable" else "acceptance_failed",
        )

    return (ToolSpec("clipboard.write", "action", "desktop-linux", write),)
