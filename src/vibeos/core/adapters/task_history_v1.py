from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from ..domain.task import GoalContract, TaskRun, TaskStatus, TerminalOutcome


class _FrozenTaskContract(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class _TerminalOutcomeV1(_FrozenTaskContract):
    schema_version: Literal["v1"] = "v1"
    task_id: str
    status: Literal["dry_run", "succeeded", "failed", "cancelled", "blocked"]
    reason: str
    evidence_ids: tuple[str, ...]
    finished_at: str


class _TaskStateV1(_FrozenTaskContract):
    schema_version: Literal["v1"] = "v1"
    task_id: str
    contract_id: str
    status: str
    revision: int = Field(ge=0)
    created_at: str
    updated_at: str
    active_plan_revision_id: str | None = None
    current_step_id: str | None = None
    completed_step_ids: tuple[str, ...] = ()
    pending_interaction_id: str | None = None
    pending_reason: str | None = None
    next_wake_at: str | None = None
    wait_event_key: str | None = None
    deadline_at: str | None = None
    suspended_status: str | None = None
    cancel_requested: bool = False
    takeover_owner: str | None = None
    last_event: str
    terminal_outcome: _TerminalOutcomeV1 | None = None


class _GoalContractV1(_FrozenTaskContract):
    schema_version: Literal["v1"] = "v1"
    contract_id: str
    task_id: str
    goal: str
    scope: tuple[str, ...]
    completion_conditions: tuple[str, ...]
    allowed_effects: tuple[str, ...]
    reality_boundaries: tuple[str, ...]
    version: int = Field(ge=1)
    created_at: str
    dry_run: bool | None = None


def decode_terminal_task_v1(raw: str) -> TaskRun:
    payload = _TaskStateV1.model_validate_json(raw, strict=True)
    status = TaskStatus(payload.status)
    if status not in {TaskStatus.DRY_RUN, TaskStatus.SUCCEEDED, TaskStatus.FAILED, TaskStatus.CANCELLED, TaskStatus.BLOCKED}:
        raise ValueError("v1 live task execution is forbidden; migrate the database first")
    terminal = payload.terminal_outcome
    return TaskRun(
        task_id=payload.task_id,
        contract_id=payload.contract_id,
        status=status,
        revision=payload.revision,
        created_at=payload.created_at,
        updated_at=payload.updated_at,
        active_plan_revision_id=payload.active_plan_revision_id,
        current_step_id=payload.current_step_id,
        completed_step_ids=payload.completed_step_ids,
        pending_interaction_id=payload.pending_interaction_id,
        pending_reason=payload.pending_reason,
        next_wake_at=payload.next_wake_at,
        wait_event_key=payload.wait_event_key,
        deadline_at=payload.deadline_at,
        suspended_status=TaskStatus(payload.suspended_status) if payload.suspended_status else None,
        cancel_requested=payload.cancel_requested,
        takeover_owner=payload.takeover_owner,
        last_event=payload.last_event,
        terminal_outcome=(
            TerminalOutcome(
                task_id=terminal.task_id,
                status=terminal.status,
                reason=terminal.reason,
                evidence_ids=terminal.evidence_ids,
                finished_at=terminal.finished_at,
                schema_version="v1",
            )
            if terminal
            else None
        ),
        schema_version="v1",
    )


def decode_terminal_contract_v1(raw: str) -> GoalContract:
    payload = _GoalContractV1.model_validate_json(raw, strict=True)
    return GoalContract(
        contract_id=payload.contract_id,
        task_id=payload.task_id,
        goal=payload.goal,
        scope=payload.scope,
        completion_conditions=payload.completion_conditions,
        allowed_effects=payload.allowed_effects,
        reality_boundaries=payload.reality_boundaries,
        version=payload.version,
        created_at=payload.created_at,
        dry_run=payload.dry_run,
        schema_version="v1",
    )
