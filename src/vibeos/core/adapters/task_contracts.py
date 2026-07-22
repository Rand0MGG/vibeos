from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


TaskStatusValue = Literal[
    "created",
    "planning",
    "ready",
    "running",
    "verifying",
    "waiting",
    "awaiting_review",
    "awaiting_clarification",
    "retry_wait",
    "replanning",
    "reconciling",
    "paused",
    "cancel_requested",
    "taken_over",
    "dry_run",
    "succeeded",
    "failed",
    "cancelled",
    "blocked",
]


class StrictTaskContract(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class TerminalOutcomePayloadV2(StrictTaskContract):
    schema_version: Literal["v2"] = "v2"
    task_id: str = Field(min_length=1, max_length=240)
    status: Literal["dry_run", "succeeded", "failed", "cancelled", "blocked"]
    reason: str = Field(max_length=20_000)
    evidence_ids: tuple[str, ...]
    finished_at: str
    diagnosis: str | None = Field(default=None, max_length=2_000)
    action: str | None = Field(default=None, max_length=160)
    current_state: str | None = Field(default=None, max_length=500)
    completion_judgment: str | None = Field(default=None, max_length=2_000)
    unresolved_risks: tuple[str, ...] = ()


class TaskStatePayloadV2(StrictTaskContract):
    schema_version: Literal["v2"] = "v2"
    task_id: str = Field(min_length=1, max_length=240)
    contract_id: str = Field(min_length=1, max_length=240)
    status: TaskStatusValue
    revision: int = Field(ge=0)
    created_at: str
    updated_at: str
    active_plan_revision_id: str | None = None
    current_step_id: str | None = None
    completed_step_ids: tuple[str, ...] = ()
    pending_interaction_id: str | None = None
    pending_reason: str | None = Field(default=None, max_length=20_000)
    next_wake_at: str | None = None
    wait_event_key: str | None = None
    deadline_at: str | None = None
    suspended_status: TaskStatusValue | None = None
    cancel_requested: bool = False
    takeover_owner: str | None = Field(default=None, max_length=240)
    last_event: str = Field(min_length=1, max_length=120)
    terminal_outcome: TerminalOutcomePayloadV2 | None = None


class GoalContractPayloadV2(StrictTaskContract):
    schema_version: Literal["v2"] = "v2"
    contract_id: str = Field(min_length=1, max_length=240)
    task_id: str = Field(min_length=1, max_length=240)
    goal: str = Field(min_length=1, max_length=20_000)
    scope: tuple[str, ...]
    completion_conditions: tuple[str, ...]
    allowed_effects: tuple[str, ...]
    reality_boundaries: tuple[str, ...]
    version: int = Field(ge=1)
    created_at: str
    dry_run: bool | None = None


class TaskEventPayloadV2(StrictTaskContract):
    schema_version: Literal["v2"] = "v2"
    event_id: str = Field(min_length=1, max_length=240)
    task_id: str = Field(min_length=1, max_length=240)
    event_type: str = Field(min_length=1, max_length=120)
    occurred_at: str
    reason: str = Field(default="", max_length=20_000)
    interaction_id: str | None = None
    plan_revision_id: str | None = None
    step_id: str | None = None
    wake_at: str | None = None
    owner: str | None = None
    terminal_status: TaskStatusValue | None = None
    evidence_ids: tuple[str, ...] = ()
    diagnosis: str | None = Field(default=None, max_length=2_000)
    action: str | None = Field(default=None, max_length=160)
    current_state: str | None = Field(default=None, max_length=500)
    completion_judgment: str | None = Field(default=None, max_length=2_000)
    unresolved_risks: tuple[str, ...] = ()
    state_revision: int = Field(ge=1)


class TaskEffectPayloadV2(StrictTaskContract):
    schema_version: Literal["v2"] = "v2"
    effect_id: str = Field(min_length=1, max_length=320)
    task_id: str = Field(min_length=1, max_length=240)
    kind: Literal["plan", "dispatch_action", "verify", "reconcile", "schedule_timer", "cancel_action", "notify"]
    step_id: str | None = None
    not_before: str | None = None


class PlanRevisionPayloadV2(StrictTaskContract):
    schema_version: Literal["v2"] = "v2"
    plan_revision_id: str = Field(min_length=1, max_length=240)
    task_id: str = Field(min_length=1, max_length=240)
    revision: int = Field(ge=1)
    plan_id: str = Field(min_length=1, max_length=240)
    payload_json: str
    created_at: str
    reason: str = Field(max_length=20_000)


class StepPayloadV2(StrictTaskContract):
    schema_version: Literal["v2"] = "v2"
    step_id: str = Field(min_length=1, max_length=240)
    task_id: str = Field(min_length=1, max_length=240)
    plan_revision_id: str = Field(min_length=1, max_length=240)
    ordinal: int = Field(ge=0)
    action: str = Field(min_length=1, max_length=120)
    capability_id: str = Field(min_length=1, max_length=120)
    status: str = Field(min_length=1, max_length=40)
    idempotency_key: str = Field(min_length=1, max_length=320)
    payload_json: str
    created_at: str
    updated_at: str


class ActionProposalPayloadV2(StrictTaskContract):
    schema_version: Literal["v2"] = "v2"
    proposal_id: str = Field(min_length=1, max_length=240)
    task_id: str = Field(min_length=1, max_length=240)
    step_id: str = Field(min_length=1, max_length=240)
    attempt_id: str = Field(min_length=1, max_length=240)
    idempotency_key: str = Field(min_length=1, max_length=320)
    action: str = Field(min_length=1, max_length=120)
    capability_id: str = Field(min_length=1, max_length=120)
    request_json: str
    status: Literal["proposed", "dispatching", "succeeded", "failed", "unknown"]
    created_at: str
    updated_at: str


class AttemptPayloadV2(StrictTaskContract):
    schema_version: Literal["v2"] = "v2"
    attempt_id: str = Field(min_length=1, max_length=240)
    task_id: str = Field(min_length=1, max_length=240)
    step_id: str | None = Field(default=None, max_length=240)
    attempt_number: int = Field(ge=1)
    classification: str = Field(min_length=1, max_length=80)
    status: str = Field(min_length=1, max_length=40)
    started_at: str
    finished_at: str | None = None
    detail_json: str


class ActionReceiptPayloadV2(StrictTaskContract):
    schema_version: Literal["v2"] = "v2"
    receipt_id: str = Field(min_length=1, max_length=240)
    task_id: str = Field(min_length=1, max_length=240)
    step_id: str = Field(min_length=1, max_length=240)
    proposal_id: str = Field(min_length=1, max_length=240)
    idempotency_key: str = Field(min_length=1, max_length=320)
    status: str = Field(min_length=1, max_length=40)
    adapter: str | None = Field(default=None, max_length=240)
    external_reference: str | None = Field(default=None, max_length=500)
    result_json: str
    occurred_at: str


class EvidenceBundlePayloadV2(StrictTaskContract):
    schema_version: Literal["v2"] = "v2"
    evidence_id: str = Field(min_length=1, max_length=240)
    task_id: str = Field(min_length=1, max_length=240)
    step_id: str | None = Field(default=None, max_length=240)
    receipt_id: str | None = Field(default=None, max_length=240)
    status: str = Field(min_length=1, max_length=40)
    summary: str = Field(max_length=20_000)
    payload_json: str
    observed_at: str


class TaskControlRequestV2(StrictTaskContract):
    schema_version: Literal["v2"] = "v2"
    task_id: str = Field(min_length=1, max_length=240)
    operation: Literal["pause", "resume", "cancel", "takeover", "release"]
    expected_revision: int = Field(ge=0)
    owner: str | None = Field(default=None, max_length=240)
    reason: str = Field(default="", max_length=20_000)

    @model_validator(mode="after")
    def takeover_requires_owner(self) -> "TaskControlRequestV2":
        if self.operation == "takeover" and not (self.owner or "").strip():
            raise ValueError("takeover requires an owner")
        return self


class TaskListRequestV2(StrictTaskContract):
    schema_version: Literal["v2"] = "v2"
    status: TaskStatusValue | None = None
    limit: int = Field(default=100, ge=0, le=1000)
