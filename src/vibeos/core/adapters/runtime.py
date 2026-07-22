from __future__ import annotations

from collections.abc import Callable
from typing import Protocol

from .contracts import CapabilityPayloadContract, NotificationAdapterResultContract, PortalStatusContract
from ..domain import (
    ActionStatus,
    CapabilityDetail,
    EffectLevel,
    EffectPolicySummary,
    NotificationDelivery,
    PortalStatus,
    StatusSnapshot,
)


class PortalStatusSource(Protocol):
    def status(self) -> dict[str, bool | str]: ...


class NotificationSource(Protocol):
    def send(self, title: str, body: str = "") -> dict[str, str]: ...


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
                    effect_level=EffectLevel(item.effect_level),
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
            effect_policy=EffectPolicySummary(
                e0=capabilities.effect_policy.e0,
                e1=capabilities.effect_policy.e1,
                e2=capabilities.effect_policy.e2,
                e3=capabilities.effect_policy.e3,
                e4=capabilities.effect_policy.e4,
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
