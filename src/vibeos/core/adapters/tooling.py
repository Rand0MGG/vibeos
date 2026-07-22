from __future__ import annotations

from dataclasses import asdict
from typing import Any

from pydantic import ValidationError

from .contracts import NotificationRequestV2, StatusRequestV2
from ..application import FoundationSliceService
from ..domain import NotificationCommand, StatusQuery
from ...tool_protocol import ToolExecutionContext, ToolResult, ToolSpec


def foundation_tool_specs(service: FoundationSliceService) -> tuple[ToolSpec, ...]:
    """Bridge legacy recipes to the new typed application boundary.

    These are the only production ToolSpecs for the two migrated capabilities.
    """

    def status(payload: dict[str, Any], context: ToolExecutionContext) -> ToolResult:
        raw = dict(payload)
        raw.setdefault("action_id", _action_id(context, raw))
        raw.setdefault("dry_run", bool(context.environment.dry_run))
        try:
            raw.setdefault("schema_version", "v2")
            request = StatusRequestV2.model_validate(raw, strict=True)
            result = service.query_status(StatusQuery(action_id=request.action_id, task_step_id=request.task_step_id, dry_run=request.dry_run))
        except ValidationError as exc:
            return _invalid_contract(exc)
        except Exception as exc:
            return _adapter_failure(exc)
        assert result.status_snapshot is not None
        snapshot = result.status_snapshot
        output: dict[str, Any] = {
            "adapter": result.adapter,
            "adapter_status": result.adapter_status,
            "portal": {key: value for key, value in asdict(snapshot.portal).items() if value is not None},
            "capabilities": list(snapshot.capabilities),
            "capability_details": [asdict(item) for item in snapshot.capability_details],
            "effect_policy": {
                "E0": snapshot.effect_policy.e0,
                "E1": snapshot.effect_policy.e1,
                "E2": snapshot.effect_policy.e2,
                "E3": snapshot.effect_policy.e3,
                "E4": snapshot.effect_policy.e4,
            },
        }
        return ToolResult(
            status="succeeded",
            output=output,
            evidence=dict(result.evidence_material),
        )

    def notification(payload: dict[str, Any], context: ToolExecutionContext) -> ToolResult:
        raw = dict(payload)
        raw.setdefault("action_id", _action_id(context, raw))
        raw.setdefault("dry_run", bool(context.environment.dry_run))
        try:
            raw.setdefault("schema_version", "v2")
            request = NotificationRequestV2.model_validate(raw, strict=True)
            title = request.canonical_title()
            result = service.send_notification(
                NotificationCommand(
                    action_id=request.action_id,
                    task_step_id=request.task_step_id,
                    title=title,
                    body=request.canonical_body(),
                    dry_run=request.dry_run,
                )
            )
        except ValidationError as exc:
            return _invalid_contract(exc)
        except Exception as exc:
            return _adapter_failure(exc)
        output = {
            "adapter": result.adapter,
            "adapter_status": result.adapter_status,
            **result.output,
        }
        evidence: dict[str, object] = dict(result.evidence_material)
        if request.dry_run:
            evidence["dry_run"] = True
        else:
            output["notification_adapter"] = result.evidence_material.get("delivery_adapter")
            output["status"] = "sent" if result.status.value == "succeeded" else result.adapter_status
            evidence["notification_adapter"] = result.evidence_material.get("delivery_adapter")
            if result.status.value == "succeeded":
                output["title"] = title
            elif result.error:
                output["error"] = result.error
        if result.external_reference is not None:
            output["selected_target"] = result.external_reference
        if result.status.value == "succeeded":
            return ToolResult(
                status="succeeded",
                output=output,
                evidence=evidence,
                state_updates={"selected_target": title},
            )
        return ToolResult(
            status="failed",
            message=result.error or "notification send failed",
            output=output,
            evidence=evidence,
            failure_class=(
                "environment_unreachable"
                if result.adapter_status == "unavailable"
                else "tool_timeout"
                if result.adapter_status == "timeout"
                else "acceptance_failed"
            ),
        )

    return (
        ToolSpec("system.status", "action", "desktop-linux", status),
        ToolSpec("notification.send", "action", "desktop-linux", notification),
    )


def _action_id(context: ToolExecutionContext, payload: dict[str, Any]) -> str:
    step_id = str(payload.get("task_step_id") or "step")
    return f"{context.attempt_id}:{step_id}"


def _invalid_contract(exc: ValidationError) -> ToolResult:
    return ToolResult(
        status="failed",
        message="foundation slice request failed strict validation",
        output={"adapter": "foundation.contract", "adapter_status": "rejected", "validation_errors": exc.errors(include_url=False)},
        failure_class="invalid_contract",
    )


def _adapter_failure(exc: Exception) -> ToolResult:
    return ToolResult(
        status="failed",
        message=f"foundation adapter failed: {type(exc).__name__}",
        output={"adapter": "foundation.adapter", "adapter_status": "failed"},
        failure_class="environment_unreachable",
    )
