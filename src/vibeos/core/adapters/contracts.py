from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictContract(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class StatusRequestV1(StrictContract):
    schema_version: Literal["v1"] = "v1"
    capability_id: Literal["system.status"] = "system.status"
    action_id: str = Field(min_length=1, max_length=240)
    task_step_id: str = Field(min_length=1, max_length=240)
    dry_run: bool


class NotificationRequestV1(StrictContract):
    schema_version: Literal["v1"] = "v1"
    capability_id: Literal["notification.send"] = "notification.send"
    action_id: str = Field(min_length=1, max_length=240)
    task_step_id: str = Field(min_length=1, max_length=240)
    title: str = Field(default="VibeOS", max_length=200)
    body: str | None = Field(default=None, max_length=4000)
    message: str | None = Field(default=None, max_length=4000)
    dry_run: bool

    def canonical_title(self) -> str:
        return self.title.strip() or "VibeOS"

    def canonical_body(self) -> str:
        value = self.body if self.body else self.message
        return (value or "").strip()


class CapabilityDetailContract(StrictContract):
    action: str
    risk_level: Literal["L0", "L1", "L2", "L3"]
    review_required: bool
    allowed: bool
    reason: str
    effects: tuple[str, ...]
    reversible: bool
    parallel_safe: bool
    constraints: tuple[str, ...]


class PermissionPolicyContract(StrictContract):
    l0: str = Field(alias="L0")
    l1: str = Field(alias="L1")
    l2: str = Field(alias="L2")
    l3: str = Field(alias="L3")


class CapabilityPayloadContract(StrictContract):
    capabilities: list[str]
    capability_details: list[CapabilityDetailContract]
    permission_policy: PermissionPolicyContract


class PortalStatusContract(StrictContract):
    available: bool
    reason: str | None = None
    open_uri: bool | None = None
    screenshot: bool | None = None
    remote_desktop: bool | None = None


class NotificationAdapterResultContract(StrictContract):
    status: Literal["sent", "failed", "unavailable", "timeout"]
    title: str | None = None
    adapter: str | None = None
    error: str | None = None


class ReceiptPayloadV1(StrictContract):
    schema_version: Literal["v1"] = "v1"
    receipt_id: str
    action_id: str
    capability_id: Literal["system.status", "notification.send"]
    effect_level: Literal["E0", "E1"]
    status: Literal["succeeded", "failed"]
    adapter: str
    adapter_status: str
    occurred_at: str
    evidence_id: str
    selected_target: str | None = None
    error: str | None = None
    dry_run: bool


class EvidencePayloadV1(StrictContract):
    schema_version: Literal["v1"] = "v1"
    evidence_id: str
    action_id: str
    capability_id: Literal["system.status", "notification.send"]
    kind: str
    summary: str
    observed_at: str
    capability_count: int | None = None
    title: str | None = None
    delivery_adapter: str | None = None
    dry_run: bool


class StatePayloadV1(StrictContract):
    schema_version: Literal["v1"] = "v1"
    state_key: str
    action_id: str
    capability_id: Literal["system.status", "notification.send"]
    status: Literal["succeeded", "failed"]
    version: int = Field(ge=1)
    receipt: ReceiptPayloadV1
    evidence: EvidencePayloadV1
    updated_at: str


class EventPayloadV1(StrictContract):
    schema_version: Literal["v1"] = "v1"
    event_id: str
    state_key: str
    action_id: str
    capability_id: Literal["system.status", "notification.send"]
    event_type: str
    occurred_at: str
    receipt: ReceiptPayloadV1
    evidence: EvidencePayloadV1


class OutboxPayloadV1(StrictContract):
    schema_version: Literal["v1"] = "v1"
    message_id: str
    state_key: str
    action_id: str
    capability_id: Literal["system.status", "notification.send"]
    topic: str
    occurred_at: str
    receipt_id: str
    evidence_id: str


class TransportCommandRequestV1(StrictContract):
    schema_version: Literal["v1"] = "v1"
    utterance: str = Field(default="", max_length=20_000)
    mode: Literal["auto_low_risk"] = "auto_low_risk"
    dry_run: bool = False
    approve: bool = False
    review_id: str | None = Field(default=None, min_length=1, max_length=240)
    supplemental_input: str | None = Field(default=None, max_length=20_000)
    reject: bool = False
    debug: bool = False

    @model_validator(mode="after")
    def require_command_identity(self) -> "TransportCommandRequestV1":
        if not self.utterance.strip() and self.review_id is None:
            raise ValueError("utterance or review_id is required")
        if self.reject and self.review_id is None:
            raise ValueError("reject requires review_id")
        return self
