from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


FIXTURE_UNIT = "vibeos-goal04-fixture.service"


class StrictSystemServiceContract(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class ServiceProcessFactV2(StrictSystemServiceContract):
    schema_version: Literal["v2"] = "v2"
    main_pid: int = Field(ge=0)
    running: bool
    exit_code: int | None = None
    exit_status: int | None = None


class ServiceJournalFactV2(StrictSystemServiceContract):
    schema_version: Literal["v2"] = "v2"
    unit: Literal[FIXTURE_UNIT] = FIXTURE_UNIT
    since: str
    until: str
    lines: tuple[str, ...] = Field(max_length=40)
    truncated: bool
    redacted: bool = True


class ServiceFactsV2(StrictSystemServiceContract):
    """Bounded D0 facts captured through the canonical observation path."""

    schema_version: Literal["v2"] = "v2"
    unit: Literal[FIXTURE_UNIT] = FIXTURE_UNIT
    load_state: Literal["loaded", "not-found", "error"]
    active_state: Literal["active", "inactive", "failed", "activating", "deactivating", "unknown"]
    sub_state: str = Field(min_length=1, max_length=80)
    result: str = Field(max_length=120)
    restart_count: int = Field(ge=0)
    process: ServiceProcessFactV2
    journal: ServiceJournalFactV2 | None = None
    source: Literal["systemd_user_dbus", "fixed_systemctl_argv"]
    captured_at: str
    ttl_seconds: int = Field(gt=0, le=60)
    sensitivity: Literal["D0"] = "D0"
    evidence_reference: str = Field(min_length=1, max_length=240)


class SystemServiceActionSpecV2(StrictSystemServiceContract):
    """The only service mutation admitted by Goal04's fixed scenario."""

    schema_version: Literal["v2"] = "v2"
    operation: Literal["start", "restart"]
    unit: Literal[FIXTURE_UNIT] = FIXTURE_UNIT
    resource_scope: Literal["systemd_user_fixture"] = "systemd_user_fixture"
    effect_level: Literal["E1"] = "E1"
    required_load_state: Literal["loaded"] = "loaded"
    allowed_pre_states: tuple[Literal["inactive", "failed"]] = ("inactive", "failed")
    timeout_seconds: int = Field(default=15, ge=1, le=30)
    idempotency_key: str = Field(min_length=16, max_length=320)
    max_dispatches: Literal[1] = 1
    verify_active_state: Literal["active"] = "active"
    reconcile_before_redispatch: Literal[True] = True

    @model_validator(mode="after")
    def restart_requires_failed_precondition(self) -> "SystemServiceActionSpecV2":
        if self.operation == "restart" and "failed" not in self.allowed_pre_states:
            raise ValueError("restart must remain bound to an observed failed fixture")
        return self


class SystemServiceAdapterResultV2(StrictSystemServiceContract):
    """Provider-local result; deliberately contains no task receipt or evidence ID."""

    schema_version: Literal["v2"] = "v2"
    operation: Literal["observe", "start", "restart"]
    unit: Literal[FIXTURE_UNIT] = FIXTURE_UNIT
    status: Literal["succeeded", "failed", "unknown"]
    adapter: Literal["systemd_user_dbus", "fixed_systemctl_argv"]
    adapter_status: str = Field(min_length=1, max_length=120)
    external_reference: str | None = Field(default=None, max_length=240)
    error_code: str | None = Field(default=None, max_length=120)
    error: str | None = Field(default=None, max_length=2_000)
