from __future__ import annotations

from typing import Any

from pydantic import ValidationError

from ..system_service_contracts import FIXTURE_UNIT, SYSTEM_SERVICE_RECOVERY_ACTION, SystemServiceActionSpecV2
from ..system_service_provider import SystemdUserServiceProvider
from ..tool_protocol import ToolExecutionContext, ToolResult, ToolSpec


def system_service_tool_specs(provider: SystemdUserServiceProvider) -> tuple[ToolSpec, ...]:
    def recover(payload: dict[str, Any], context: ToolExecutionContext) -> ToolResult:
        try:
            spec = SystemServiceActionSpecV2.model_validate(
                {
                    "operation": payload.get("operation"),
                    "unit": payload.get("unit", FIXTURE_UNIT),
                    "idempotency_key": payload.get("idempotency_key"),
                }
            )
        except ValidationError:
            return ToolResult(status="blocked", message="system-service action contract was rejected", failure_class="unsupported_request")
        if context.environment.dry_run:
            return ToolResult(
                status="succeeded",
                output={
                    "unit": spec.unit,
                    "operation": spec.operation,
                    "adapter": "systemd_user_dbus",
                    "adapter_status": "dry_run",
                },
                evidence={"unit": spec.unit, "operation": spec.operation, "dry_run": True},
            )
        result = provider.execute(spec)
        output = result.model_dump(mode="json")
        if result.status == "succeeded":
            return ToolResult(
                status="succeeded",
                output=output,
                evidence={
                    "unit": result.unit,
                    "operation": result.operation,
                    "adapter": result.adapter,
                    "adapter_status": result.adapter_status,
                    "external_reference": result.external_reference,
                },
            )
        return ToolResult(
            status="failed",
            message=result.error or "system-service action failed",
            output=output,
            evidence={"unit": result.unit, "operation": result.operation, "adapter_status": result.adapter_status},
            failure_class=result.error_code or "environment_unreachable",
        )

    return (ToolSpec(SYSTEM_SERVICE_RECOVERY_ACTION, "action", "desktop-linux", recover),)
