from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from vibeos.broker import CapabilityBroker
from vibeos.core.adapters.database import CoreDatabase
from vibeos.core.adapters.http import AsyncHttpServer, HttpRequest, HttpResponse
from vibeos.core.adapters.lifecycle import DatabaseLifecycleComponent
from vibeos.core.application import AsyncSupervisor
from vibeos.daemon import DaemonHttpRouter, build_status_payload
from vibeos.dbus_service import DBusServiceComponent
from vibeos.intent import RuleIntentBroker
from vibeos.models import CommandRequest
from vibeos.runtime import HTTPDaemonClient, HTTPDaemonRuntime, build_runtime


def test_cli_dbus_http_and_python_share_one_durable_task_store(tmp_path: Path) -> None:
    async def scenario() -> None:
        broker = CapabilityBroker(
            intent_broker=RuleIntentBroker(),
            database=CoreDatabase(tmp_path / "tasks.sqlite3"),
        )
        supervisor = AsyncSupervisor()
        supervisor.add_component(DatabaseLifecycleComponent(broker.database))

        def status_payload() -> dict[str, object]:
            return build_status_payload(["dbus", "http"], "127.0.0.1", 8765, health=supervisor.health())

        router = DaemonHttpRouter(broker=broker, supervisor=supervisor, status_payload=status_payload)
        dbus = DBusServiceComponent(broker=broker, supervisor=supervisor, status_payload=status_payload)
        await supervisor.start()
        try:
            for path in (
                "/v2/status",
                "/v2/apps",
                "/v2/windows",
                "/v2/capabilities",
                "/v2/reviews/pending",
                "/v2/audit/tail?n=5",
            ):
                response = await router.handle(HttpRequest("GET", path, {}, b""))
                assert response.status == 200
                assert response.headers["Deprecation"] == "true"
                assert json.loads(response.body)

            baseline_task_count = len(broker.tasks())
            body = json.dumps({"schema_version": "v2", "utterance": "status"}).encode()
            http_result = await router.handle(HttpRequest("POST", "/v2/command", {}, body))
            assert http_result.status == 200
            assert json.loads(http_result.body)["transport"] == "http"
            assert len(broker.tasks()) == baseline_task_count + 1

            dbus_result = json.loads(await dbus._execute(CommandRequest("status", transport="dbus")))
            assert dbus_result["transport"] == "dbus"
            assert len(broker.tasks()) == baseline_task_count + 2

            python_result = broker.handle(CommandRequest("status", transport="local"))
            assert python_result.transport == "local"
            assert len(broker.tasks()) == baseline_task_count + 3

            tasks_response = await router.handle(HttpRequest("GET", "/v2/tasks", {}, b""))
            normalized_python_tasks = json.loads(json.dumps(broker.tasks()))
            assert json.loads(tasks_response.body)["tasks"] == normalized_python_tasks
        finally:
            await supervisor.stop()

    asyncio.run(scenario())


def test_http_compatibility_is_loopback_only() -> None:
    async def handler(_request: HttpRequest) -> HttpResponse:
        return HttpResponse(200, b"{}")

    with pytest.raises(ValueError, match="loopback-only"):
        AsyncHttpServer("0.0.0.0", 0, handler)
    with pytest.raises(ValueError, match="loopback-only"):
        HTTPDaemonClient("http://192.0.2.10:8765")


def test_explicit_http_runtime_mode_remains_supported(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VIBEOS_RUNTIME", "http")
    monkeypatch.delenv("VIBEOS_PREFER_LOCAL_BROKER", raising=False)
    monkeypatch.setattr("vibeos.runtime.HTTPDaemonClient.is_available", lambda self: True)

    assert isinstance(build_runtime(), HTTPDaemonRuntime)
