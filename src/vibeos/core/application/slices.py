from __future__ import annotations

from ..domain import (
    ActionStatus,
    AdapterResult,
    NotificationCommand,
    StatusQuery,
)
from ..ports import NotificationSender, StatusReader


class FoundationSliceService:
    """The two Goal 01 vertical slices, independent of transport and framework."""

    def __init__(
        self,
        *,
        status_reader: StatusReader,
        notification_sender: NotificationSender,
    ) -> None:
        self._status_reader = status_reader
        self._notification_sender = notification_sender

    def query_status(self, query: StatusQuery) -> AdapterResult:
        snapshot = self._status_reader.read_status()
        return AdapterResult(
            status=ActionStatus.SUCCEEDED,
            adapter="system.status",
            adapter_status="succeeded",
            evidence_material={
                "kind": "runtime_status_observation",
                "summary": f"observed {len(snapshot.capabilities)} registered capabilities",
                "capability_count": len(snapshot.capabilities),
                "dry_run": query.dry_run,
            },
            output={},
            status_snapshot=snapshot,
        )

    def send_notification(self, command: NotificationCommand) -> AdapterResult:
        delivery = self._notification_sender.send(command.title, command.body, dry_run=command.dry_run)
        return AdapterResult(
            status=delivery.status,
            adapter="notifications.send",
            adapter_status="dry_run" if command.dry_run else delivery.adapter_status,
            evidence_material={
                "kind": "notification_delivery_result",
                "summary": delivery.message or f"notification adapter reported {delivery.adapter_status}",
                "title": command.title,
                "delivery_adapter": delivery.adapter or "",
                "dry_run": command.dry_run,
            },
            output={"title": command.title} if delivery.status is ActionStatus.SUCCEEDED else {},
            external_reference=command.title if delivery.status is ActionStatus.SUCCEEDED else None,
            error=delivery.message if delivery.status is ActionStatus.FAILED else None,
        )
