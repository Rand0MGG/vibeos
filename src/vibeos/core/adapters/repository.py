from __future__ import annotations

from dataclasses import asdict

from sqlalchemy import insert, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from .contracts import EventPayloadV1, EvidencePayloadV1, OutboxPayloadV1, ReceiptPayloadV1, StatePayloadV1
from .database import CoreDatabase, FaultInjector
from .metadata import current_state, domain_events, outbox
from ..domain import (
    ActionReceipt,
    ActionState,
    ActionStatus,
    ActionTransition,
    EffectLevel,
    Evidence,
)


class SqliteActionRepository:
    def __init__(self, database: CoreDatabase, *, fault_injector: FaultInjector | None = None) -> None:
        self._database = database
        self._fault_injector = fault_injector

    def commit(self, transition: ActionTransition) -> None:
        state_payload = StatePayloadV1.model_validate(asdict(transition.state), strict=True)
        event_payload = EventPayloadV1.model_validate(asdict(transition.event), strict=True)
        outbox_payload = OutboxPayloadV1.model_validate(asdict(transition.outbox), strict=True)
        with self._database.engine.begin() as connection:
            statement = sqlite_insert(current_state).values(
                state_key=transition.state.state_key,
                aggregate_type="action",
                aggregate_id=transition.state.action_id,
                state_version=transition.state.version,
                status=transition.state.status.value,
                schema_version="v1",
                payload_json=state_payload.model_dump_json(),
                updated_at=transition.state.updated_at,
            )
            connection.execute(
                statement.on_conflict_do_update(
                    index_elements=[current_state.c.state_key],
                    set_={
                        "state_version": transition.state.version,
                        "status": transition.state.status.value,
                        "schema_version": "v1",
                        "payload_json": state_payload.model_dump_json(),
                        "updated_at": transition.state.updated_at,
                    },
                )
            )
            self._inject("after_state")
            connection.execute(
                insert(domain_events).values(
                    event_id=transition.event.event_id,
                    state_key=transition.event.state_key,
                    aggregate_type="action",
                    aggregate_id=transition.event.action_id,
                    event_type=transition.event.event_type,
                    schema_version="v1",
                    occurred_at=transition.event.occurred_at,
                    payload_json=event_payload.model_dump_json(),
                )
            )
            self._inject("after_event")
            connection.execute(
                insert(outbox).values(
                    message_id=transition.outbox.message_id,
                    state_key=transition.outbox.state_key,
                    aggregate_id=transition.outbox.action_id,
                    topic=transition.outbox.topic,
                    schema_version="v1",
                    occurred_at=transition.outbox.occurred_at,
                    payload_json=outbox_payload.model_dump_json(),
                    attempts=0,
                )
            )
            self._inject("after_outbox")

    def get(self, state_key: str) -> ActionState | None:
        with self._database.engine.connect() as connection:
            raw = connection.execute(select(current_state.c.payload_json).where(current_state.c.state_key == state_key)).scalar_one_or_none()
        if raw is None:
            return None
        payload = StatePayloadV1.model_validate_json(str(raw), strict=True)
        receipt = _receipt_from_payload(payload.receipt)
        evidence = _evidence_from_payload(payload.evidence)
        return ActionState(
            state_key=payload.state_key,
            action_id=payload.action_id,
            capability_id=payload.capability_id,
            status=ActionStatus(payload.status),
            version=payload.version,
            receipt=receipt,
            evidence=evidence,
            updated_at=payload.updated_at,
        )

    def _inject(self, stage: str) -> None:
        if self._fault_injector is not None:
            self._fault_injector(stage)


def _receipt_from_payload(payload: ReceiptPayloadV1) -> ActionReceipt:
    return ActionReceipt(
        receipt_id=payload.receipt_id,
        action_id=payload.action_id,
        capability_id=payload.capability_id,
        effect_level=EffectLevel(payload.effect_level),
        status=ActionStatus(payload.status),
        adapter=payload.adapter,
        adapter_status=payload.adapter_status,
        occurred_at=payload.occurred_at,
        evidence_id=payload.evidence_id,
        selected_target=payload.selected_target,
        error=payload.error,
        dry_run=payload.dry_run,
    )


def _evidence_from_payload(payload: EvidencePayloadV1) -> Evidence:
    return Evidence(
        evidence_id=payload.evidence_id,
        action_id=payload.action_id,
        capability_id=payload.capability_id,
        kind=payload.kind,
        summary=payload.summary,
        observed_at=payload.observed_at,
        capability_count=payload.capability_count,
        title=payload.title,
        delivery_adapter=payload.delivery_adapter,
        dry_run=payload.dry_run,
    )
