from __future__ import annotations

import asyncio
import threading
from dataclasses import dataclass
from pathlib import Path

import pytest

from vibeos.core.adapters.http import AsyncHttpServer, HttpRequest, HttpResponse
from vibeos.core.adapters.database import CoreDatabase
from vibeos.core.adapters.lifecycle import DatabaseLifecycleComponent
from vibeos.core.application import AsyncSupervisor, SupervisorNotReady, SupervisorStartError, SupervisorState


@dataclass
class FakeComponent:
    name: str
    fail_start: bool = False
    starts: int = 0
    stops: int = 0
    status: str = "stopped"

    async def start(self) -> None:
        self.starts += 1
        if self.fail_start:
            self.status = "failed"
            raise RuntimeError("injected startup failure")
        self.status = "ready"

    async def stop(self) -> None:
        self.stops += 1
        self.status = "stopped"

    def health_status(self) -> tuple[str, str]:
        return self.status, self.status


def test_supervisor_has_one_predictable_start_ready_drain_stop_lifecycle() -> None:
    async def scenario() -> None:
        component = FakeComponent("worker")
        supervisor = AsyncSupervisor()
        supervisor.add_component(component)

        await supervisor.start()
        assert supervisor.state is SupervisorState.READY
        assert supervisor.health().accepting_requests is True
        with pytest.raises(SupervisorStartError):
            await supervisor.start()

        operation_started = threading.Event()
        operation_release = threading.Event()

        def blocking_operation() -> str:
            operation_started.set()
            assert operation_release.wait(timeout=5)
            return "finished"

        request = asyncio.create_task(supervisor.submit(blocking_operation))
        assert await asyncio.to_thread(operation_started.wait, 2)
        draining = asyncio.create_task(supervisor.drain())
        await asyncio.sleep(0)
        assert supervisor.state is SupervisorState.DRAINING
        with pytest.raises(SupervisorNotReady):
            await supervisor.submit(lambda: "new work")
        operation_release.set()
        assert await request == "finished"
        await draining
        await supervisor.stop()

        assert supervisor.state is SupervisorState.STOPPED
        assert component.starts == 1
        assert component.stops == 1

    asyncio.run(scenario())


def test_start_failure_stops_started_components_and_reports_failed_health() -> None:
    async def scenario() -> None:
        first = FakeComponent("first")
        second = FakeComponent("second", fail_start=True)
        supervisor = AsyncSupervisor()
        supervisor.add_component(first)
        supervisor.add_component(second)

        with pytest.raises(SupervisorStartError, match="second"):
            await supervisor.start()

        assert supervisor.state is SupervisorState.FAILED
        assert first.stops == 1
        assert supervisor.health().ready is False
        await supervisor.stop()
        assert supervisor.state is SupervisorState.STOPPED

    asyncio.run(scenario())


def test_database_lifecycle_rejects_tableless_database_even_when_pragmas_are_ready(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        database = CoreDatabase(tmp_path / "tableless.sqlite3")
        monkeypatch.setattr(database, "upgrade", lambda: None)
        supervisor = AsyncSupervisor()
        supervisor.add_component(DatabaseLifecycleComponent(database))

        with pytest.raises(SupervisorStartError, match="database"):
            await supervisor.start()

        assert database.health()["ready"] is False
        assert supervisor.health().ready is False
        assert supervisor.health().components[0].status == "failed"
        await supervisor.stop()

    asyncio.run(scenario())


def test_database_lifecycle_fails_on_persistent_migration_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        database = CoreDatabase(tmp_path / "tasks.sqlite3")
        monkeypatch.setattr(
            database,
            "_run_alembic_upgrade",
            lambda: (_ for _ in ()).throw(RuntimeError("persistent migration failure")),
        )
        supervisor = AsyncSupervisor()
        supervisor.add_component(DatabaseLifecycleComponent(database))

        with pytest.raises(SupervisorStartError, match="database"):
            await supervisor.start()

        assert supervisor.health().ready is False
        assert supervisor.health().components[0].status == "failed"
        await supervisor.stop()

    asyncio.run(scenario())


def test_database_lifecycle_recovers_after_transient_migration_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        database = CoreDatabase(tmp_path / "tasks.sqlite3")
        real_upgrade = database._run_alembic_upgrade
        monkeypatch.setattr(database, "_run_alembic_upgrade", lambda: (_ for _ in ()).throw(RuntimeError("transient migration failure")))
        monkeypatch.setattr(database, "_run_alembic_upgrade", real_upgrade)
        supervisor = AsyncSupervisor()
        supervisor.add_component(DatabaseLifecycleComponent(database))

        await supervisor.start()

        assert database.health()["ready"] is True
        assert supervisor.health().ready is True
        await supervisor.stop()

    asyncio.run(scenario())


def test_async_http_adapter_is_owned_by_supervisor_and_rejects_after_drain() -> None:
    async def scenario() -> None:
        supervisor = AsyncSupervisor()

        async def handler(_request: HttpRequest) -> HttpResponse:
            try:
                payload = await supervisor.submit(lambda: b'{"status":"ok"}')
                return HttpResponse(200, payload)
            except SupervisorNotReady:
                return HttpResponse(503, b'{"error":"daemon_not_ready"}')

        http = AsyncHttpServer("127.0.0.1", 0, handler)
        supervisor.add_component(http)
        await supervisor.start()

        ready_response = await http_get(http.port)
        assert b"200 OK" in ready_response
        assert b'{"status":"ok"}' in ready_response

        await supervisor.drain()
        drained_response = await http_get(http.port)
        assert b"503 Service Unavailable" in drained_response
        assert b"daemon_not_ready" in drained_response
        await supervisor.stop()

    asyncio.run(scenario())


async def http_get(port: int) -> bytes:
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    writer.write(b"GET /v1/status HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n")
    await writer.drain()
    response = await reader.read()
    writer.close()
    await writer.wait_closed()
    return response
