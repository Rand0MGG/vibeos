from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class EffectLevel(StrEnum):
    E0 = "E0"
    E1 = "E1"


class ActionStatus(StrEnum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"


@dataclass(frozen=True)
class CapabilityDetail:
    action: str
    risk_level: str
    review_required: bool
    allowed: bool
    reason: str
    effects: tuple[str, ...]
    reversible: bool
    parallel_safe: bool
    constraints: tuple[str, ...]


@dataclass(frozen=True)
class PermissionSummary:
    l0: str
    l1: str
    l2: str
    l3: str


@dataclass(frozen=True)
class PortalStatus:
    available: bool
    reason: str | None = None
    open_uri: bool | None = None
    screenshot: bool | None = None
    remote_desktop: bool | None = None


@dataclass(frozen=True)
class StatusSnapshot:
    portal: PortalStatus
    capabilities: tuple[str, ...]
    capability_details: tuple[CapabilityDetail, ...]
    permission_policy: PermissionSummary


@dataclass(frozen=True)
class NotificationDelivery:
    status: ActionStatus
    adapter_status: str
    adapter: str | None
    title: str
    message: str = ""


@dataclass(frozen=True)
class StatusQuery:
    action_id: str
    task_step_id: str
    dry_run: bool


@dataclass(frozen=True)
class NotificationCommand:
    action_id: str
    task_step_id: str
    title: str
    body: str
    dry_run: bool


@dataclass(frozen=True)
class Evidence:
    evidence_id: str
    action_id: str
    capability_id: str
    kind: str
    summary: str
    observed_at: str
    capability_count: int | None = None
    title: str | None = None
    delivery_adapter: str | None = None
    dry_run: bool = False


@dataclass(frozen=True)
class ActionReceipt:
    receipt_id: str
    action_id: str
    capability_id: str
    effect_level: EffectLevel
    status: ActionStatus
    adapter: str
    adapter_status: str
    occurred_at: str
    evidence_id: str
    selected_target: str | None = None
    error: str | None = None
    dry_run: bool = False


@dataclass(frozen=True)
class SliceResult:
    receipt: ActionReceipt
    evidence: Evidence
    status_snapshot: StatusSnapshot | None = None


@dataclass(frozen=True)
class ActionState:
    state_key: str
    action_id: str
    capability_id: str
    status: ActionStatus
    version: int
    receipt: ActionReceipt
    evidence: Evidence
    updated_at: str


@dataclass(frozen=True)
class ActionEvent:
    event_id: str
    state_key: str
    action_id: str
    capability_id: str
    event_type: str
    occurred_at: str
    receipt: ActionReceipt
    evidence: Evidence


@dataclass(frozen=True)
class OutboxMessage:
    message_id: str
    state_key: str
    action_id: str
    capability_id: str
    topic: str
    occurred_at: str
    receipt_id: str
    evidence_id: str


@dataclass(frozen=True)
class ActionTransition:
    state: ActionState
    event: ActionEvent
    outbox: OutboxMessage
