from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from .adapters.database import CoreDatabase
from .adapters.runtime import NotificationSource, PortalStatusSource, RuntimeNotificationSender, RuntimeStatusReader
from .adapters.tooling import foundation_tool_specs
from .application import FoundationSliceService
from ..tool_protocol import ToolSpec


@dataclass(frozen=True)
class FoundationComponents:
    database: CoreDatabase
    slices: FoundationSliceService
    tool_specs: tuple[ToolSpec, ...]


def compose_foundation(
    *,
    database: CoreDatabase,
    portal: PortalStatusSource,
    notifications: NotificationSource,
    capabilities: Callable[[], dict[str, object]],
) -> FoundationComponents:
    slices = FoundationSliceService(
        status_reader=RuntimeStatusReader(portal=portal, capabilities=capabilities),
        notification_sender=RuntimeNotificationSender(notifications),
    )
    return FoundationComponents(
        database=database,
        slices=slices,
        tool_specs=foundation_tool_specs(slices),
    )
