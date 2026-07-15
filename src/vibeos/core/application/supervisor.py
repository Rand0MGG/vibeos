from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import TypeVar

from ..ports import LifecycleComponent

T = TypeVar("T")


class SupervisorState(StrEnum):
    STOPPED = "stopped"
    STARTING = "starting"
    READY = "ready"
    DRAINING = "draining"
    FAILED = "failed"


class SupervisorError(RuntimeError):
    pass


class SupervisorNotReady(SupervisorError):
    pass


class SupervisorStartError(SupervisorError):
    pass


@dataclass(frozen=True)
class ComponentHealth:
    name: str
    status: str
    message: str


@dataclass(frozen=True)
class SupervisorHealth:
    state: SupervisorState
    ready: bool
    accepting_requests: bool
    active_requests: int
    components: tuple[ComponentHealth, ...]


class AsyncSupervisor:
    """One asyncio owner for daemon components and in-flight requests."""

    def __init__(self) -> None:
        self._state = SupervisorState.STOPPED
        self._components: list[LifecycleComponent] = []
        self._started: list[LifecycleComponent] = []
        self._active: set[asyncio.Task[object]] = set()
        self._state_lock = asyncio.Lock()

    @property
    def state(self) -> SupervisorState:
        return self._state

    def add_component(self, component: LifecycleComponent) -> None:
        if self._state is not SupervisorState.STOPPED:
            raise SupervisorError("components can only be added while the supervisor is stopped")
        if any(existing.name == component.name for existing in self._components):
            raise SupervisorError(f"duplicate component name: {component.name}")
        self._components.append(component)

    async def start(self) -> None:
        async with self._state_lock:
            if self._state is not SupervisorState.STOPPED:
                raise SupervisorStartError(f"cannot start supervisor from {self._state.value}")
            self._state = SupervisorState.STARTING
        try:
            for component in self._components:
                await component.start()
                self._started.append(component)
        except Exception as exc:
            await self._stop_started()
            self._state = SupervisorState.FAILED
            raise SupervisorStartError(f"daemon startup failed in {component.name}") from exc
        self._state = SupervisorState.READY

    async def submit(self, operation: Callable[[], T]) -> T:
        if self._state is not SupervisorState.READY:
            raise SupervisorNotReady(f"daemon is not accepting requests while {self._state.value}")
        task = asyncio.create_task(asyncio.to_thread(operation))
        tracked = task  # Preserve the generic result while tracking as object.
        self._active.add(tracked)
        try:
            return await task
        finally:
            self._active.discard(tracked)

    async def drain(self) -> None:
        async with self._state_lock:
            if self._state is SupervisorState.DRAINING:
                return
            if self._state is not SupervisorState.READY:
                raise SupervisorError(f"cannot drain supervisor from {self._state.value}")
            self._state = SupervisorState.DRAINING
        if self._active:
            await asyncio.gather(*tuple(self._active), return_exceptions=True)

    async def stop(self) -> None:
        if self._state is SupervisorState.READY:
            await self.drain()
        if self._state not in {SupervisorState.DRAINING, SupervisorState.FAILED, SupervisorState.STARTING}:
            if self._state is SupervisorState.STOPPED:
                return
            raise SupervisorError(f"cannot stop supervisor from {self._state.value}")
        await self._stop_started()
        self._state = SupervisorState.STOPPED

    def health(self) -> SupervisorHealth:
        components = tuple(
            ComponentHealth(name=component.name, status=component.health_status()[0], message=component.health_status()[1]) for component in self._components
        )
        return SupervisorHealth(
            state=self._state,
            ready=self._state is SupervisorState.READY,
            accepting_requests=self._state is SupervisorState.READY,
            active_requests=len(self._active),
            components=components,
        )

    async def _stop_started(self) -> None:
        for component in reversed(self._started):
            try:
                await component.stop()
            except Exception:
                continue
        self._started.clear()
