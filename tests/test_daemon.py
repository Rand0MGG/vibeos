import json
import urllib.request
from pathlib import Path
from uuid import uuid4

from vibeos.apps import AppRegistry
from vibeos.audit import AuditLog
from vibeos.broker import CapabilityBroker
from vibeos.intent import RuleIntentBroker
from vibeos.models import AppEntry
from vibeos.daemon import build_status_payload, create_http_server, start_http_server_thread
from vibeos.reviews import ReviewStore


class FakeApps(AppRegistry):
    def list_apps(self):
        return [AppEntry(desktop_id="firefox.desktop", name="Firefox", keywords=("browser",))]

    def open_app(self, app):
        return {"status": "opened", "desktop_id": app.desktop_id}


class FakeClipboard:
    def write(self, text: str) -> dict[str, object]:
        return {"status": "written", "text": text}


def test_http_server_serves_status_and_capabilities() -> None:
    server = create_http_server(
        CapabilityBroker(),
        "127.0.0.1",
        0,
        status_payload=build_status_payload(["http", "dbus"], host="127.0.0.1", port=0),
    )
    thread = start_http_server_thread(server)
    host, port = server.server_address[:2]
    base_url = f"http://{host}:{port}"
    try:
        with urllib.request.urlopen(f"{base_url}/v1/status", timeout=5) as response:
            status_payload = json.loads(response.read().decode("utf-8"))
        with urllib.request.urlopen(f"{base_url}/v1/capabilities", timeout=5) as response:
            capability_payload = json.loads(response.read().decode("utf-8"))
        with urllib.request.urlopen(f"{base_url}/v1/audit/tail?n=5", timeout=5) as response:
            audit_payload = json.loads(response.read().decode("utf-8"))
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    assert status_payload["status"] == "ok"
    assert status_payload["service"] == "vibed"
    assert status_payload["transports"] == ["http", "dbus"]
    assert "capabilities" in capability_payload
    assert "permission_policy" in capability_payload
    assert "entries" in audit_payload


def test_http_command_endpoint_returns_plan_review_metadata() -> None:
    broker = CapabilityBroker(
        intent_broker=RuleIntentBroker(),
        apps=FakeApps(),
        audit=AuditLog(make_audit_path("http-plan-review")),
        reviews=ReviewStore(make_review_path("http-plan-review")),
    )
    server = create_http_server(
        broker,
        "127.0.0.1",
        0,
        status_payload=build_status_payload(["http"], host="127.0.0.1", port=0),
    )
    thread = start_http_server_thread(server)
    host, port = server.server_address[:2]
    base_url = f"http://{host}:{port}"
    try:
        request = urllib.request.Request(
            f"{base_url}/v1/command",
            data=json.dumps({"utterance": "explain clipboard permissions and then copy hello to clipboard"}).encode("utf-8"),
            headers={"Content-Type": "application/json; charset=utf-8"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=5) as response:
            payload = json.loads(response.read().decode("utf-8"))
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    assert payload["status"] == "review_required"
    assert payload["transport"] == "http"
    assert payload["review_id"]
    assert payload["result"]["analysis"]["type"] == "mixed"
    assert payload["result"]["plan"]["schema_version"] == "v0.5"
    assert payload["result"]["plan_review"]["status"] == "review_required"


def test_http_command_endpoint_returns_plan_execution_metadata() -> None:
    broker = CapabilityBroker(
        intent_broker=RuleIntentBroker(),
        apps=FakeApps(),
        audit=AuditLog(make_audit_path("http-plan-execution")),
        reviews=ReviewStore(make_review_path("http-plan-execution")),
    )
    server = create_http_server(
        broker,
        "127.0.0.1",
        0,
        status_payload=build_status_payload(["http"], host="127.0.0.1", port=0),
    )
    thread = start_http_server_thread(server)
    host, port = server.server_address[:2]
    base_url = f"http://{host}:{port}"
    try:
        request = urllib.request.Request(
            f"{base_url}/v1/command",
            data=json.dumps({"utterance": "open browser"}).encode("utf-8"),
            headers={"Content-Type": "application/json; charset=utf-8"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=5) as response:
            payload = json.loads(response.read().decode("utf-8"))
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    assert payload["status"] == "executed"
    assert payload["transport"] == "http"
    assert payload["selected_target"] == "firefox.desktop"
    assert payload["result"]["analysis"]["type"] == "task"
    assert payload["result"]["plan"]["schema_version"] == "v0.5"
    assert payload["result"]["execution"]["status"] == "succeeded"


def test_http_review_lifecycle_executes_stored_task_plan() -> None:
    broker = CapabilityBroker(
        intent_broker=RuleIntentBroker(),
        apps=FakeApps(),
        clipboard=FakeClipboard(),
        audit=AuditLog(make_audit_path("http-review-lifecycle")),
        reviews=ReviewStore(make_review_path("http-review-lifecycle")),
    )
    server = create_http_server(
        broker,
        "127.0.0.1",
        0,
        status_payload=build_status_payload(["http"], host="127.0.0.1", port=0),
    )
    thread = start_http_server_thread(server)
    host, port = server.server_address[:2]
    base_url = f"http://{host}:{port}"
    try:
        create_request = urllib.request.Request(
            f"{base_url}/v1/command",
            data=json.dumps({"utterance": "explain clipboard permissions and then copy hello to clipboard"}).encode("utf-8"),
            headers={"Content-Type": "application/json; charset=utf-8"},
            method="POST",
        )
        with urllib.request.urlopen(create_request, timeout=5) as response:
            created = json.loads(response.read().decode("utf-8"))

        with urllib.request.urlopen(f"{base_url}/v1/reviews/pending", timeout=5) as response:
            pending_before = json.loads(response.read().decode("utf-8"))

        approve_request = urllib.request.Request(
            f"{base_url}/v1/command",
            data=json.dumps({"review_id": created["review_id"], "approve": True}).encode("utf-8"),
            headers={"Content-Type": "application/json; charset=utf-8"},
            method="POST",
        )
        with urllib.request.urlopen(approve_request, timeout=5) as response:
            approved = json.loads(response.read().decode("utf-8"))

        with urllib.request.urlopen(f"{base_url}/v1/reviews/pending", timeout=5) as response:
            pending_after = json.loads(response.read().decode("utf-8"))
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    assert created["status"] == "review_required"
    assert pending_before["reviews"][0]["review_kind"] == "plan"
    assert pending_before["reviews"][0]["plan_payload"]["schema_version"] == "v0.5"
    assert approved["status"] == "executed"
    assert approved["result"]["plan_id"] == pending_before["reviews"][0]["plan_id"]
    assert approved["result"]["step_results"][0]["adapter"] == "clipboard.helper"
    assert approved["result"]["step_results"][0]["capability_id"] == "clipboard.write"
    assert approved["result"]["step_results"][0]["diagnostics"]["selected_target"] == "clipboard"
    assert pending_after["reviews"] == []


def test_http_command_endpoint_accepts_supplemental_input() -> None:
    captured = {}

    class FakeBroker:
        def handle(self, request):
            captured["request"] = request
            from vibeos.models import CommandResult, Intent

            return CommandResult(
                status="executed",
                intent=Intent(action="app.open", target={"name": "browser"}),
                result={"ok": True},
                execution_status="succeeded",
                acceptance_status="passed",
                overall_status="completed",
            )

    server = create_http_server(
        FakeBroker(),
        "127.0.0.1",
        0,
        status_payload=build_status_payload(["http"], host="127.0.0.1", port=0),
    )
    thread = start_http_server_thread(server)
    host, port = server.server_address[:2]
    base_url = f"http://{host}:{port}"
    try:
        request = urllib.request.Request(
            f"{base_url}/v1/command",
            data=json.dumps({"review_id": "rev_user_input", "supplemental_input": "browser"}).encode("utf-8"),
            headers={"Content-Type": "application/json; charset=utf-8"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=5) as response:
            payload = json.loads(response.read().decode("utf-8"))
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    assert payload["status"] == "executed"
    assert captured["request"].review_id == "rev_user_input"
    assert captured["request"].supplemental_input == "browser"


def make_review_path(name: str) -> Path:
    return Path(".vibeos") / f"test-{name}-{uuid4().hex}.jsonl"


def make_audit_path(name: str) -> Path:
    return Path(".vibeos") / f"audit-{name}-{uuid4().hex}.jsonl"
