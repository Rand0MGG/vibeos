from __future__ import annotations

import asyncio
from collections.abc import Callable


class TaskSchedulerComponent:
    name = "task-scheduler"

    def __init__(
        self,
        *,
        scan: Callable[[], tuple[str, ...]],
        resume: Callable[[str], None],
        poll_seconds: float = 1.0,
        max_concurrency: int = 8,
    ) -> None:
        self._scan = scan
        self._resume = resume
        self._poll_seconds = poll_seconds
        self._max_concurrency = max(1, max_concurrency)
        self._stop = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        self._status = "stopped"
        self._message = "task scheduler is stopped"

    async def start(self) -> None:
        self._stop.clear()
        self._ready()
        self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        self._stop.set()
        if self._task is not None:
            await self._task
        self._task = None
        self._status = "stopped"
        self._message = "task scheduler is stopped"

    async def tick(self) -> int:
        try:
            task_ids = await asyncio.to_thread(self._scan)
        except Exception as exc:
            self._degrade("scan", exc)
            return 0
        semaphore = asyncio.Semaphore(self._max_concurrency)

        async def resume_one(task_id: str) -> BaseException | None:
            async with semaphore:
                try:
                    await asyncio.to_thread(self._resume, task_id)
                except Exception as exc:
                    return exc
            return None

        failures = tuple(item for item in await asyncio.gather(*(resume_one(task_id) for task_id in task_ids)) if item is not None)
        if failures:
            self._degrade("resume", failures[0])
        else:
            self._ready()
        return len(task_ids)

    def health_status(self) -> tuple[str, str]:
        return self._status, self._message

    async def _run(self) -> None:
        while not self._stop.is_set():
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self._poll_seconds)
            except TimeoutError:
                await self.tick()

    def _degrade(self, operation: str, error: BaseException) -> None:
        self._status = "degraded"
        self._message = f"task scheduler {operation} failed: {type(error).__name__}: {error}"

    def _ready(self) -> None:
        self._status = "ready"
        self._message = "task scheduler scans persisted runnable and due tasks"


class OutboxDispatcherComponent:
    name = "outbox-dispatcher"

    def __init__(
        self,
        *,
        claim: Callable[[], tuple[object, ...]],
        consume: Callable[[object], None],
        poll_seconds: float = 0.5,
        max_concurrency: int = 8,
    ) -> None:
        self._claim = claim
        self._consume = consume
        self._poll_seconds = poll_seconds
        self._max_concurrency = max(1, max_concurrency)
        self._stop = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        self._status = "stopped"
        self._message = "outbox dispatcher is stopped"

    async def start(self) -> None:
        self._stop.clear()
        self._ready()
        self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        self._stop.set()
        if self._task is not None:
            await self._task
        self._task = None
        self._status = "stopped"
        self._message = "outbox dispatcher is stopped"

    async def tick(self) -> int:
        try:
            messages = await asyncio.to_thread(self._claim)
        except Exception as exc:
            self._degrade("claim", exc)
            return 0
        semaphore = asyncio.Semaphore(self._max_concurrency)

        async def consume_one(message: object) -> BaseException | None:
            async with semaphore:
                try:
                    await asyncio.to_thread(self._consume, message)
                except Exception as exc:
                    return exc
            return None

        failures = tuple(item for item in await asyncio.gather(*(consume_one(message) for message in messages)) if item is not None)
        if failures:
            self._degrade("consume", failures[0])
        else:
            self._ready()
        return len(messages)

    def health_status(self) -> tuple[str, str]:
        return self._status, self._message

    async def _run(self) -> None:
        while not self._stop.is_set():
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self._poll_seconds)
            except TimeoutError:
                await self.tick()

    def _degrade(self, operation: str, error: BaseException) -> None:
        self._status = "degraded"
        self._message = f"outbox dispatcher {operation} failed: {type(error).__name__}: {error}"

    def _ready(self) -> None:
        self._status = "ready"
        self._message = "outbox dispatcher provides at-least-once delivery"
