from __future__ import annotations

from ..domain import (
    ActionEvent,
    ActionReceipt,
    ActionState,
    ActionStatus,
    ActionTransition,
    EffectLevel,
    Evidence,
    NotificationCommand,
    OutboxMessage,
    SliceResult,
    StatusQuery,
)
from ..ports import ActionRepository, Clock, IdGenerator, NotificationSender, StatusReader


class FoundationSliceService:
    """The two Goal 01 vertical slices, independent of transport and framework."""

    def __init__(
        self,
        *,
        repository: ActionRepository,
        clock: Clock,
        ids: IdGenerator,
        status_reader: StatusReader,
        notification_sender: NotificationSender,
    ) -> None:
        self._repository = repository
        self._clock = clock
        self._ids = ids
        self._status_reader = status_reader
        self._notification_sender = notification_sender

    def query_status(self, query: StatusQuery) -> SliceResult:
        observed_at = self._clock.now_iso()
        snapshot = self._status_reader.read_status()
        evidence = Evidence(
            evidence_id=self._ids.new_id("ev"),
            action_id=query.action_id,
            capability_id="system.status",
            kind="runtime_status_observation",
            summary=f"observed {len(snapshot.capabilities)} registered capabilities",
            observed_at=observed_at,
            capability_count=len(snapshot.capabilities),
            dry_run=query.dry_run,
        )
        receipt = ActionReceipt(
            receipt_id=self._ids.new_id("rcpt"),
            action_id=query.action_id,
            capability_id="system.status",
            effect_level=EffectLevel.E0,
            status=ActionStatus.SUCCEEDED,
            adapter="system.status",
            adapter_status="succeeded",
            occurred_at=observed_at,
            evidence_id=evidence.evidence_id,
            dry_run=query.dry_run,
        )
        result = SliceResult(receipt=receipt, evidence=evidence, status_snapshot=snapshot)
        if not query.dry_run:
            self._repository.commit(self._transition(result))
        return result

    def send_notification(self, command: NotificationCommand) -> SliceResult:
        state_key = self._state_key(command.action_id)
        if not command.dry_run:
            existing = self._repository.get(state_key)
            if existing is not None:
                return SliceResult(receipt=existing.receipt, evidence=existing.evidence)
        observed_at = self._clock.now_iso()
        delivery = self._notification_sender.send(command.title, command.body, dry_run=command.dry_run)
        evidence = Evidence(
            evidence_id=self._ids.new_id("ev"),
            action_id=command.action_id,
            capability_id="notification.send",
            kind="notification_delivery_receipt",
            summary=delivery.message or f"notification adapter reported {delivery.adapter_status}",
            observed_at=observed_at,
            title=command.title,
            delivery_adapter=delivery.adapter,
            dry_run=command.dry_run,
        )
        receipt = ActionReceipt(
            receipt_id=self._ids.new_id("rcpt"),
            action_id=command.action_id,
            capability_id="notification.send",
            effect_level=EffectLevel.E1,
            status=delivery.status,
            adapter="notifications.send",
            adapter_status="dry_run" if command.dry_run else delivery.adapter_status,
            occurred_at=observed_at,
            evidence_id=evidence.evidence_id,
            selected_target=command.title if delivery.status is ActionStatus.SUCCEEDED else None,
            error=delivery.message if delivery.status is ActionStatus.FAILED else None,
            dry_run=command.dry_run,
        )
        result = SliceResult(receipt=receipt, evidence=evidence)
        if not command.dry_run:
            self._repository.commit(self._transition(result))
        return result

    def _transition(self, result: SliceResult) -> ActionTransition:
        state_key = self._state_key(result.receipt.action_id)
        state = ActionState(
            state_key=state_key,
            action_id=result.receipt.action_id,
            capability_id=result.receipt.capability_id,
            status=result.receipt.status,
            version=1,
            receipt=result.receipt,
            evidence=result.evidence,
            updated_at=result.receipt.occurred_at,
        )
        event = ActionEvent(
            event_id=self._ids.new_id("evt"),
            state_key=state_key,
            action_id=result.receipt.action_id,
            capability_id=result.receipt.capability_id,
            event_type=f"action.{result.receipt.status.value}",
            occurred_at=result.receipt.occurred_at,
            receipt=result.receipt,
            evidence=result.evidence,
        )
        outbox = OutboxMessage(
            message_id=self._ids.new_id("msg"),
            state_key=state_key,
            action_id=result.receipt.action_id,
            capability_id=result.receipt.capability_id,
            topic="vibeos.action.outcome.v1",
            occurred_at=result.receipt.occurred_at,
            receipt_id=result.receipt.receipt_id,
            evidence_id=result.evidence.evidence_id,
        )
        return ActionTransition(state=state, event=event, outbox=outbox)

    @staticmethod
    def _state_key(action_id: str) -> str:
        return f"action:{action_id}"
