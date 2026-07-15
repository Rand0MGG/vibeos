from __future__ import annotations

from typing import Protocol

from ..domain import ActionState, ActionTransition, NotificationDelivery, StatusSnapshot


class LifecycleComponent(Protocol):
    @property
    def name(self) -> str: ...

    async def start(self) -> None: ...

    async def stop(self) -> None: ...

    def health_status(self) -> tuple[str, str]: ...


class Clock(Protocol):
    def now_iso(self) -> str: ...


class IdGenerator(Protocol):
    def new_id(self, prefix: str) -> str: ...


class StatusReader(Protocol):
    def read_status(self) -> StatusSnapshot: ...


class NotificationSender(Protocol):
    def send(self, title: str, body: str, *, dry_run: bool) -> NotificationDelivery: ...


class ActionRepository(Protocol):
    def commit(self, transition: ActionTransition) -> None: ...

    def get(self, state_key: str) -> ActionState | None: ...
