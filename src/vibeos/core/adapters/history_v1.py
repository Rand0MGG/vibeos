from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from .database import CoreDatabase
from .metadata import current_state
from ..domain import ActionStatus, EffectLevel


@dataclass(frozen=True)
class HistoricalEvidenceV1:
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
class HistoricalActionReceiptV1:
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
class HistoricalActionStateV1:
    state_key: str
    action_id: str
    capability_id: str
    status: ActionStatus
    version: int
    receipt: HistoricalActionReceiptV1
    evidence: HistoricalEvidenceV1
    updated_at: str


class _FrozenHistoryContract(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class _ReceiptPayloadV1(_FrozenHistoryContract):
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


class _EvidencePayloadV1(_FrozenHistoryContract):
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


class _StatePayloadV1(_FrozenHistoryContract):
    schema_version: Literal["v1"] = "v1"
    state_key: str
    action_id: str
    capability_id: Literal["system.status", "notification.send"]
    status: Literal["succeeded", "failed"]
    version: int = Field(ge=1)
    receipt: _ReceiptPayloadV1
    evidence: _EvidencePayloadV1
    updated_at: str


class LegacyActionHistoryReader:
    """Read-only decoder for frozen Goal 01 action aggregates.

    It intentionally exposes no commit/update method. Historical rows are
    immutable and are never resumed as live Goal04 work.
    """

    def __init__(self, database: CoreDatabase) -> None:
        self._database = database

    def get(self, state_key: str) -> HistoricalActionStateV1 | None:
        with self._database.engine.connect() as connection:
            row = connection.execute(
                select(current_state.c.schema_version, current_state.c.payload_json).where(
                    current_state.c.state_key == state_key,
                    current_state.c.aggregate_type == "action",
                )
            ).one_or_none()
        if row is None:
            return None
        if row.schema_version != "v1":
            raise ValueError(f"unsupported frozen action history schema: {row.schema_version}")
        payload = _StatePayloadV1.model_validate_json(str(row.payload_json), strict=True)
        receipt = payload.receipt
        evidence = payload.evidence
        return HistoricalActionStateV1(
            state_key=payload.state_key,
            action_id=payload.action_id,
            capability_id=payload.capability_id,
            status=ActionStatus(payload.status),
            version=payload.version,
            receipt=HistoricalActionReceiptV1(
                receipt_id=receipt.receipt_id,
                action_id=receipt.action_id,
                capability_id=receipt.capability_id,
                effect_level=EffectLevel(receipt.effect_level),
                status=ActionStatus(receipt.status),
                adapter=receipt.adapter,
                adapter_status=receipt.adapter_status,
                occurred_at=receipt.occurred_at,
                evidence_id=receipt.evidence_id,
                selected_target=receipt.selected_target,
                error=receipt.error,
                dry_run=receipt.dry_run,
            ),
            evidence=HistoricalEvidenceV1(
                evidence_id=evidence.evidence_id,
                action_id=evidence.action_id,
                capability_id=evidence.capability_id,
                kind=evidence.kind,
                summary=evidence.summary,
                observed_at=evidence.observed_at,
                capability_count=evidence.capability_count,
                title=evidence.title,
                delivery_adapter=evidence.delivery_adapter,
                dry_run=evidence.dry_run,
            ),
            updated_at=payload.updated_at,
        )
