from __future__ import annotations

from typing import Any

from ..notifications import NotificationAdapter
from ..tool_protocol import ToolExecutionContext, ToolResult, ToolSpec


def notification_tool_specs(notifications: NotificationAdapter) -> tuple[ToolSpec, ...]:
    def send(payload: dict[str, Any], context: ToolExecutionContext) -> ToolResult:
        title = str(payload.get("title") or "VibeOS").strip() or "VibeOS"
        body = str(payload.get("body") or payload.get("message") or "").strip()
        if context.environment.dry_run:
            return ToolResult(
                status="succeeded",
                output={"selected_target": title, "adapter": "notifications.send", "adapter_status": "dry_run"},
                evidence={"title": title, "body": body, "dry_run": True},
                state_updates={"selected_target": title},
            )
        result = notifications.send(title, body)
        status = str(result.get("status") or "failed")
        output = {"adapter": "notifications.send", "adapter_status": "succeeded" if status == "sent" else status, "notification_adapter": result.get("adapter"), **{key: value for key, value in result.items() if key != "adapter"}}
        evidence = {"title": title, "body": body, "notification_adapter": result.get("adapter")}
        if status == "sent":
            return ToolResult(status="succeeded", output={"selected_target": title, **output}, evidence=evidence, state_updates={"selected_target": title})
        return ToolResult(
            status="failed",
            message=str(result.get("error") or "notification send failed"),
            output=output,
            evidence=evidence,
            failure_class="environment_unreachable" if status == "unavailable" else "tool_timeout" if status == "timeout" else "acceptance_failed",
        )

    return (ToolSpec("notification.send", "action", "desktop-linux", send),)
