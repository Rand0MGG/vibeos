from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class EffectLevel(StrEnum):
    E0 = "E0"
    E1 = "E1"
    E2 = "E2"
    E3 = "E3"
    E4 = "E4"


class ActionStatus(StrEnum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"


@dataclass(frozen=True)
class CapabilityDetail:
    action: str
    effect_level: EffectLevel
    review_required: bool
    allowed: bool
    reason: str
    effects: tuple[str, ...]
    reversible: bool
    parallel_safe: bool
    constraints: tuple[str, ...]


@dataclass(frozen=True)
class EffectPolicySummary:
    e0: str
    e1: str
    e2: str
    e3: str
    e4: str


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
    effect_policy: EffectPolicySummary


@dataclass(frozen=True)
class AdapterResult:
    """A provider-local result used to mint one canonical durable outcome.

    This value is deliberately not an ActionReceipt or EvidenceBundle. It does
    not assert task completion and it has no independent task persistence.
    """

    status: ActionStatus
    adapter: str
    adapter_status: str
    evidence_material: dict[str, object]
    output: dict[str, object]
    external_reference: str | None = None
    error: str | None = None
    status_snapshot: StatusSnapshot | None = None


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
