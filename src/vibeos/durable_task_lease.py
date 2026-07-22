from __future__ import annotations

from threading import Event, Lock, Thread

from .core.adapters.task_repository import SqliteTaskRepository, TaskLeaseLost
from .core.domain.task import TaskLease
from .durable_task_support import after_seconds, now_iso


class LeaseHeartbeat:
    """Renews one fenced task lease while a worker is performing external I/O."""

    def __init__(
        self,
        repository: SqliteTaskRepository,
        lease: TaskLease,
        *,
        lease_seconds: int,
        interval_seconds: float,
    ) -> None:
        self.repository = repository
        self.lease = lease
        self.lease_seconds = lease_seconds
        self.interval_seconds = max(0.05, min(interval_seconds, lease_seconds / 3))
        self._stop = Event()
        self._lock = Lock()
        self._error: TaskLeaseLost | None = None
        self._thread: Thread | None = None

    def __enter__(self) -> LeaseHeartbeat:
        self._thread = Thread(target=self._run, name=f"task-lease-{self.lease.task_id}", daemon=True)
        self._thread.start()
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=max(1.0, self.interval_seconds * 2))
        if exc is None:
            self.assert_valid()

    def assert_valid(self) -> None:
        with self._lock:
            error = self._error
        if error is not None:
            raise error

    def _run(self) -> None:
        while not self._stop.wait(self.interval_seconds):
            try:
                renewed = self.repository.renew(
                    self.lease,
                    now=now_iso(),
                    expires_at=after_seconds(self.lease_seconds),
                )
            except TaskLeaseLost as exc:
                with self._lock:
                    self._error = exc
                self._stop.set()
                return
            with self._lock:
                self.lease = renewed
