from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from vibeos.audit import AuditLog
from vibeos.broker import CapabilityBroker
from vibeos.cli import main
from vibeos.core.adapters.database import CoreDatabase
from vibeos.core.adapters.http import HttpRequest
from vibeos.core.adapters.lifecycle import DatabaseLifecycleComponent
from vibeos.core.application import AsyncSupervisor
from vibeos.daemon import DaemonHttpRouter, build_status_payload
from vibeos.models import CommandRequest
from vibeos.runtime import LocalRuntime

from tests.support_intent_broker import FixtureIntentBroker


class FixturePortal:
    def status(self) -> dict[str, bool | str]:
        return {"available": False, "reason": "fixture portal unavailable"}

    def open_uri(self, uri: str) -> dict[str, str]:
        return {"status": "opened", "uri": uri, "adapter": "fixture-portal"}


class FixtureNotifications:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def send(self, title: str, body: str = "") -> dict[str, str]:
        self.calls.append((title, body))
        return {"status": "sent", "title": title, "adapter": "fixture-notify"}


def test_cli_system_status_uses_new_slice_and_returns_one_canonical_receipt(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    broker = make_broker(tmp_path)
    monkeypatch.setattr("vibeos.cli.build_runtime", lambda: LocalRuntime(broker))

    exit_code = main(["ask", "status", "--json"])
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    receipt = broker.task_repository.receipts(payload["result"]["task_id"])[0]
    receipt_payload = json.loads(receipt.result_json)
    assert receipt_payload["capability_id"] == "system.status"
    assert recursive_find(receipt_payload, "evidence")["capability_count"] == 19
    assert payload["transport"] == "local"


def test_http_transport_uses_same_slice_and_rejects_requests_after_drain(tmp_path: Path) -> None:
    async def scenario() -> None:
        broker = make_broker(tmp_path)
        supervisor = AsyncSupervisor()
        supervisor.add_component(DatabaseLifecycleComponent(broker.database))

        def status_payload() -> dict[str, object]:
            return build_status_payload(["http"], health=supervisor.health())

        router = DaemonHttpRouter(broker=broker, supervisor=supervisor, status_payload=status_payload)
        body = json.dumps({"schema_version": "v2", "utterance": "status"}).encode("utf-8")
        request = HttpRequest("POST", "/v2/command", {"content-type": "application/json"}, body)

        before_ready = await router.handle(request)
        assert before_ready.status == 503
        await supervisor.start()
        response = await router.handle(request)
        payload = json.loads(response.body)
        assert response.status == 200
        assert broker.task_repository.receipts(payload["result"]["task_id"])[0].step_id
        assert payload["transport"] == "http"

        invalid = await router.handle(
            HttpRequest(
                "POST",
                "/v2/command",
                {"content-type": "application/json"},
                json.dumps({"schema_version": "v2", "utterance": "status", "unknown": True}).encode("utf-8"),
            )
        )
        assert invalid.status == 400
        assert json.loads(invalid.body)["error"] == "invalid_contract"

        await supervisor.drain()
        after_drain = await router.handle(request)
        assert after_drain.status == 503
        await supervisor.stop()

    asyncio.run(scenario())


def test_e1_notification_production_composition_has_no_legacy_tool_logic(tmp_path: Path) -> None:
    notifications = FixtureNotifications()
    broker = make_broker(tmp_path, notifications=notifications)

    result = broker.handle(CommandRequest("notify completed", transport="local"))
    receipt = broker.task_repository.receipts(result.result["task_id"])[0]
    receipt_payload = json.loads(receipt.result_json)

    assert result.status == "executed"
    assert receipt_payload["capability_id"] == "notification.send"
    assert recursive_find(receipt_payload, "evidence")["delivery_adapter"] == "fixture-notify"
    assert notifications.calls == [("VibeOS", "completed")]
    assert broker.tool_registry.get("notification.send").runner.__module__ == "vibeos.core.adapters.tooling"
    assert broker.tool_registry.get("system.status").runner.__module__ == "vibeos.core.adapters.tooling"


def make_broker(tmp_path: Path, *, notifications: FixtureNotifications | None = None) -> CapabilityBroker:
    return CapabilityBroker(
        intent_broker=FixtureIntentBroker(),
        portal=FixturePortal(),
        notifications=notifications or FixtureNotifications(),
        audit=AuditLog(tmp_path / "audit.jsonl"),
        database=CoreDatabase(tmp_path / "tasks.sqlite3"),
    )


def recursive_find(value: Any, key: str) -> dict[str, Any]:
    if isinstance(value, dict):
        candidate = value.get(key)
        if isinstance(candidate, dict):
            return candidate
        for item in value.values():
            try:
                return recursive_find(item, key)
            except KeyError:
                continue
    elif isinstance(value, (list, tuple)):
        for item in value:
            try:
                return recursive_find(item, key)
            except KeyError:
                continue
    raise KeyError(key)
