from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Protocol
from uuid import uuid4

from .contracts import CapabilityPayloadContract, NotificationAdapterResultContract, PortalStatusContract
from ..domain import (
    ActionStatus,
    CapabilityDetail,
    NotificationDelivery,
    PermissionSummary,
    PortalStatus,
    StatusSnapshot,
)


class PortalStatusSource(Protocol):
    def status(self) -> dict[str, bool | str]: ...


class NotificationSource(Protocol):
    def send(self, title: str, body: str = "") -> dict[str, str]: ...


class SystemClock:
    def now_iso(self) -> str:
        return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


class UuidIdGenerator:
    def new_id(self, prefix: str) -> str:
        return f"{prefix}_{uuid4().hex}"


class RuntimeStatusReader:
    def __init__(self, *, portal: PortalStatusSource, capabilities: Callable[[], dict[str, object]]) -> None:
        self._portal = portal
        self._capabilities = capabilities

    def read_status(self) -> StatusSnapshot:
        portal = PortalStatusContract.model_validate(self._portal.status(), strict=True)
        capabilities = CapabilityPayloadContract.model_validate(self._capabilities(), strict=True)
        return StatusSnapshot(
            portal=PortalStatus(
                available=portal.available,
                reason=portal.reason,
                open_uri=portal.open_uri,
                screenshot=portal.screenshot,
                remote_desktop=portal.remote_desktop,
            ),
            capabilities=tuple(capabilities.capabilities),
            capability_details=tuple(
                CapabilityDetail(
                    action=item.action,
                    risk_level=item.risk_level,
                    review_required=item.review_required,
                    allowed=item.allowed,
                    reason=item.reason,
                    effects=tuple(item.effects),
                    reversible=item.reversible,
                    parallel_safe=item.parallel_safe,
                    constraints=tuple(item.constraints),
                )
                for item in capabilities.capability_details
            ),
            permission_policy=PermissionSummary(
                l0=capabilities.permission_policy.l0,
                l1=capabilities.permission_policy.l1,
                l2=capabilities.permission_policy.l2,
                l3=capabilities.permission_policy.l3,
            ),
        )


class RuntimeNotificationSender:
    def __init__(self, source: NotificationSource) -> None:
        self._source = source

    def send(self, title: str, body: str, *, dry_run: bool) -> NotificationDelivery:
        if dry_run:
            return NotificationDelivery(
                status=ActionStatus.SUCCEEDED,
                adapter_status="dry_run",
                adapter=None,
                title=title,
            )
        result = NotificationAdapterResultContract.model_validate(self._source.send(title, body), strict=True)
        succeeded = result.status == "sent"
        return NotificationDelivery(
            status=ActionStatus.SUCCEEDED if succeeded else ActionStatus.FAILED,
            adapter_status="succeeded" if succeeded else result.status,
            adapter=result.adapter,
            title=title,
            message=result.error or "",
        )
