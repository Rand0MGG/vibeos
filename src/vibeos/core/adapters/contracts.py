from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictContract(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class StatusRequestV2(StrictContract):
    schema_version: Literal["v2"] = "v2"
    capability_id: Literal["system.status"] = "system.status"
    action_id: str = Field(min_length=1, max_length=240)
    task_step_id: str = Field(min_length=1, max_length=240)
    dry_run: bool


class NotificationRequestV2(StrictContract):
    schema_version: Literal["v2"] = "v2"
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
    schema_version: Literal["v2"] = "v2"
    action: str
    effect_level: Literal["E0", "E1", "E2", "E3", "E4"]
    review_required: bool
    allowed: bool
    reason: str
    effects: tuple[str, ...]
    reversible: bool
    parallel_safe: bool
    constraints: tuple[str, ...]


class EffectPolicyContract(StrictContract):
    e0: str = Field(alias="E0")
    e1: str = Field(alias="E1")
    e2: str = Field(alias="E2")
    e3: str = Field(alias="E3")
    e4: str = Field(alias="E4")


class CapabilityPayloadContract(StrictContract):
    schema_version: Literal["v2"] = "v2"
    capabilities: list[str]
    capability_details: list[CapabilityDetailContract]
    effect_policy: EffectPolicyContract


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


class TransportCommandRequestV2(StrictContract):
    schema_version: Literal["v2"] = "v2"
    utterance: str = Field(default="", max_length=20_000)
    mode: Literal["auto_low_risk"] = "auto_low_risk"
    dry_run: bool = False
    approve: bool = False
    review_id: str | None = Field(default=None, min_length=1, max_length=240)
    supplemental_input: str | None = Field(default=None, max_length=20_000)
    reject: bool = False
    debug: bool = False

    @model_validator(mode="after")
    def require_command_identity(self) -> "TransportCommandRequestV2":
        if not self.utterance.strip() and self.review_id is None:
            raise ValueError("utterance or review_id is required")
        if self.reject and self.review_id is None:
            raise ValueError("reject requires review_id")
        return self
