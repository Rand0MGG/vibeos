from __future__ import annotations

from dataclasses import asdict
from typing import Any

from pydantic import ValidationError

from .contracts import EvidencePayloadV1, NotificationRequestV1, ReceiptPayloadV1, StatusRequestV1
from ..application import FoundationSliceService
from ..domain import NotificationCommand, SliceResult, StatusQuery
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
            request = StatusRequestV1.model_validate(raw, strict=True)
            result = service.query_status(StatusQuery(action_id=request.action_id, task_step_id=request.task_step_id, dry_run=request.dry_run))
        except ValidationError as exc:
            return _invalid_contract(exc)
        except Exception as exc:
            return _persistence_failure(exc)
        assert result.status_snapshot is not None
        snapshot = result.status_snapshot
        output: dict[str, Any] = {
            "adapter": result.receipt.adapter,
            "adapter_status": result.receipt.adapter_status,
            "portal": {key: value for key, value in asdict(snapshot.portal).items() if value is not None},
            "capabilities": list(snapshot.capabilities),
            "capability_details": [asdict(item) for item in snapshot.capability_details],
            "permission_policy": {
                "L0": snapshot.permission_policy.l0,
                "L1": snapshot.permission_policy.l1,
                "L2": snapshot.permission_policy.l2,
                "L3": snapshot.permission_policy.l3,
            },
            **_result_metadata(result),
        }
        return ToolResult(
            status="succeeded",
            output=output,
            evidence={"capability_count": len(snapshot.capabilities), **_evidence_payload(result)},
        )

    def notification(payload: dict[str, Any], context: ToolExecutionContext) -> ToolResult:
        raw = dict(payload)
        raw.setdefault("action_id", _action_id(context, raw))
        raw.setdefault("dry_run", bool(context.environment.dry_run))
        try:
            request = NotificationRequestV1.model_validate(raw, strict=True)
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
            return _persistence_failure(exc)
        output = {
            "adapter": result.receipt.adapter,
            "adapter_status": result.receipt.adapter_status,
            **_result_metadata(result),
        }
        evidence: dict[str, object] = {"title": title, **_evidence_payload(result)}
        if request.dry_run:
            evidence["dry_run"] = True
        else:
            output["notification_adapter"] = result.evidence.delivery_adapter
            output["status"] = "sent" if result.receipt.status.value == "succeeded" else result.receipt.adapter_status
            evidence["notification_adapter"] = result.evidence.delivery_adapter
            if result.receipt.status.value == "succeeded":
                output["title"] = title
            elif result.receipt.error:
                output["error"] = result.receipt.error
        if result.receipt.selected_target is not None:
            output["selected_target"] = result.receipt.selected_target
        if result.receipt.status.value == "succeeded":
            return ToolResult(
                status="succeeded",
                output=output,
                evidence=evidence,
                state_updates={"selected_target": title},
            )
        return ToolResult(
            status="failed",
            message=result.receipt.error or "notification send failed",
            output=output,
            evidence=evidence,
            failure_class=(
                "environment_unreachable"
                if result.receipt.adapter_status == "unavailable"
                else "tool_timeout"
                if result.receipt.adapter_status == "timeout"
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


def _result_metadata(result: SliceResult) -> dict[str, object]:
    return {
        "action_receipt": ReceiptPayloadV1.model_validate(asdict(result.receipt), strict=True).model_dump(mode="json"),
        "observation_evidence": EvidencePayloadV1.model_validate(asdict(result.evidence), strict=True).model_dump(mode="json"),
    }


def _evidence_payload(result: SliceResult) -> dict[str, object]:
    return {"observation_evidence": EvidencePayloadV1.model_validate(asdict(result.evidence), strict=True).model_dump(mode="json")}


def _invalid_contract(exc: ValidationError) -> ToolResult:
    return ToolResult(
        status="failed",
        message="foundation slice request failed strict validation",
        output={"adapter": "foundation.contract", "adapter_status": "rejected", "validation_errors": exc.errors(include_url=False)},
        failure_class="invalid_contract",
    )


def _persistence_failure(exc: Exception) -> ToolResult:
    return ToolResult(
        status="failed",
        message=f"foundation slice could not persist an authoritative outcome: {type(exc).__name__}",
        output={"adapter": "foundation.database", "adapter_status": "failed"},
        failure_class="persistence_unavailable",
    )
