from __future__ import annotations

import json
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from typing import Any

from .core.adapters.task_repository import SqliteTaskRepository
from .core.domain.task import ActionReceipt, GoalContract, PlanRevision, Step, TaskEvent, TaskEventType, TaskRun, TaskStatus
from .models import CommandRequest
from .planning_models import PlanningArtifacts
from .planning_service import PlanningService
from .task_models import FailureClassification, PlanAttempt, PlanExecutionResult, StepExecutionResult, TaskPlan


def now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def after_seconds(seconds: int) -> str:
    return (datetime.now(UTC) + timedelta(seconds=seconds)).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def stable_id(prefix: str, *parts: object, length: int = 20) -> str:
    source = ":".join(str(part) for part in parts)
    return f"{prefix}_{sha256(source.encode('utf-8')).hexdigest()[:length]}"


def execution_message(execution: PlanExecutionResult) -> str:
    acceptance = execution.acceptance_result or {}
    return str(acceptance.get("message") or execution.error or "task completed with verified evidence")


def overall_status(state: TaskRun, execution: PlanExecutionResult | None) -> str:
    if execution is not None:
        return str(execution.overall_status)
    return {
        TaskStatus.DRY_RUN: "dry_run",
        TaskStatus.SUCCEEDED: "completed",
        TaskStatus.AWAITING_REVIEW: "needs_review",
        TaskStatus.AWAITING_CLARIFICATION: "needs_user_input",
        TaskStatus.PAUSED: "blocked",
        TaskStatus.TAKEN_OVER: "blocked",
        TaskStatus.WAITING: "blocked",
        TaskStatus.RETRY_WAIT: "blocked",
        TaskStatus.CANCELLED: "rejected",
    }.get(state.status, "failed")


def new_contract(task_id: str, request: CommandRequest, created_at: str) -> GoalContract:
    return GoalContract(
        contract_id=stable_id("contract", task_id, request.utterance),
        task_id=task_id,
        goal=request.utterance.strip() or "resumed durable task",
        scope=(),
        completion_conditions=("registered verifier and semantic acceptance determine completion",),
        allowed_effects=("effects declared by the validated task plan",),
        reality_boundaries=("material ambiguity requires user clarification",),
        version=1,
        created_at=created_at,
    )


def new_task(task_id: str, contract: GoalContract, created_at: str, *, timeout_seconds: int = 6 * 60 * 60) -> TaskRun:
    return TaskRun(
        task_id=task_id,
        contract_id=contract.contract_id,
        status=TaskStatus.CREATED,
        revision=0,
        created_at=created_at,
        updated_at=created_at,
        deadline_at=after_seconds(timeout_seconds),
    )


def event(state: TaskRun, kind: TaskEventType, *, reason: str = "", **fields: object) -> TaskEvent:
    occurred_at = now_iso()
    terminal_status = fields.get("terminal_status")
    raw_evidence_ids = fields.get("evidence_ids", ())
    evidence_ids = tuple(str(item) for item in raw_evidence_ids if item) if isinstance(raw_evidence_ids, (list, tuple, set)) else ()
    return TaskEvent(
        event_id=stable_id("tevt", state.task_id, state.revision + 1, kind.value, occurred_at),
        task_id=state.task_id,
        event_type=kind,
        occurred_at=occurred_at,
        reason=reason,
        interaction_id=_optional_text(fields.get("interaction_id")),
        plan_revision_id=_optional_text(fields.get("plan_revision_id")),
        step_id=_optional_text(fields.get("step_id")),
        wake_at=_optional_text(fields.get("wake_at")),
        owner=_optional_text(fields.get("owner")),
        terminal_status=terminal_status if isinstance(terminal_status, TaskStatus) else None,
        evidence_ids=evidence_ids,
    )


def plan_artifacts(task: TaskRun, planning: PlanningArtifacts, service: PlanningService) -> tuple[PlanRevision, tuple[Step, ...]]:
    plan = planning.plan
    if plan is None:
        raise ValueError("cannot persist a missing task plan")
    timestamp = now_iso()
    revision_number = task.revision + 1
    plan_revision_id = stable_id("planrev", task.task_id, revision_number, plan.plan_id)
    payload_json = json.dumps(service.payload(planning), ensure_ascii=False, separators=(",", ":"))
    revision = PlanRevision(
        plan_revision_id=plan_revision_id,
        task_id=task.task_id,
        revision=revision_number,
        plan_id=plan.plan_id,
        payload_json=payload_json,
        created_at=timestamp,
        reason="initial plan" if task.active_plan_revision_id is None else "replan",
    )
    steps = tuple(
        Step(
            step_id=item.id,
            task_id=task.task_id,
            plan_revision_id=plan_revision_id,
            ordinal=index,
            action=item.action,
            capability_id=item.capability_id,
            status="pending",
            idempotency_key=stable_id("idem", task.task_id, plan_revision_id, item.id, item.action, length=32),
            payload_json=json.dumps(asdict(item), ensure_ascii=False, separators=(",", ":")),
            created_at=timestamp,
            updated_at=timestamp,
        )
        for index, item in enumerate(plan.steps)
    )
    return revision, steps


def load_planning(task: TaskRun, repository: SqliteTaskRepository, service: PlanningService) -> PlanningArtifacts | None:
    if task.active_plan_revision_id is None:
        return None
    raw = repository.plan_payload(task.active_plan_revision_id)
    contract = repository.contract(task.task_id)
    if raw is None or contract is None:
        return None
    payload = json.loads(raw)
    if not isinstance(payload, dict):
        return None
    return service.from_snapshot(utterance=contract.goal, payload=payload)


def restore_step_results(receipts: tuple[ActionReceipt, ...]) -> tuple[StepExecutionResult, ...]:
    restored: list[StepExecutionResult] = []
    for receipt in receipts:
        try:
            payload = json.loads(receipt.result_json)
        except json.JSONDecodeError:
            continue
        if not isinstance(payload, dict):
            continue
        restored.append(step_result_from_payload(payload))
    return tuple(restored)


def step_result_from_payload(payload: dict[str, Any]) -> StepExecutionResult:
    return StepExecutionResult(
        step_id=str(payload["step_id"]),
        layer=str(payload.get("layer", "registered_tool_execute")),
        status=str(payload.get("status", "failed")),
        step_safety_review_id=_optional_text(payload.get("step_safety_review_id")),
        adapter=_optional_text(payload.get("adapter")),
        capability_id=_optional_text(payload.get("capability_id")),
        attempt=int(payload.get("attempt", 1)),
        attempt_id=_optional_text(payload.get("attempt_id")),
        duration_ms=int(payload["duration_ms"]) if payload.get("duration_ms") is not None else None,
        adapter_status=_optional_text(payload.get("adapter_status")),
        diagnostics=dict(payload.get("diagnostics", {})) if isinstance(payload.get("diagnostics"), dict) else {},
        error_code=_optional_text(payload.get("error_code")),
        result=dict(payload.get("result", {})) if isinstance(payload.get("result"), dict) else {},
        error=_optional_text(payload.get("error")),
        audit_id=_optional_text(payload.get("audit_id")),
    )


def public_attempts(run_id: str, plan: TaskPlan | None, results: tuple[StepExecutionResult, ...]) -> tuple[PlanAttempt, ...]:
    if plan is None:
        return ()
    attempts: list[PlanAttempt] = []
    for index, result in enumerate(results, start=1):
        failure = None
        if result.status != "succeeded":
            failure = FailureClassification(
                failure_class=str(result.error_code or "unsupported_request"),
                message=result.error or "step failed",
                retryable=False,
                replannable=True,
            )
        attempts.append(
            PlanAttempt(
                attempt_id=result.attempt_id or stable_id("attempt", run_id, index, result.step_id),
                run_id=run_id,
                attempt_index=index,
                trigger="durable_dispatch",
                selected_route_id=plan.selected_route_id,
                task_plan=plan,
                failure=failure,
            )
        )
    return tuple(attempts)


def selected_target(results: tuple[StepExecutionResult, ...]) -> str | None:
    for item in reversed(results):
        for source in (item.result, item.diagnostics):
            for key in ("selected_target", "uri", "resolved_url"):
                if source.get(key) is not None:
                    return str(source[key])
    return None


def _optional_text(value: object) -> str | None:
    return str(value) if value is not None else None
