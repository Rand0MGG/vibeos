import subprocess

import pytest

from vibeos.audit import AuditLog
from vibeos.models import CommandRequest
from vibeos.runtime import DBusDaemonClient, DBusDaemonRuntime, HTTPDaemonRuntime, LocalRuntime, RuntimeSelectionError, build_runtime, detect_runtime_entry


class FakeDaemonClient:
    def __init__(self):
        self.payloads = []

    def request_payload(self, payload):
        self.payloads.append(payload)
        return {
            "status": "dry_run",
            "intent": {
                "action": "app.open",
                "target": {"name": "browser"},
                "reason": "test",
                "requires_confirmation": False,
            },
            "selected_target": "firefox.desktop",
            "review_id": None,
            "message": "",
            "review": {
                "risk_level": "L1",
                "review_required": False,
                "allowed": True,
                "reason": "allowed",
                "effects": [],
                "reversible": True,
            },
        }

    def call_json_method(self, method, *args):
        if method == "Capabilities":
            return {"capabilities": ["app.open"]}
        if method == "AppsList":
            return [{"desktop_id": "firefox.desktop"}]
        if method == "WindowsList":
            return [{"window_id": "1"}]
        if method == "PendingReviews":
            return [{"review_id": "rev_123"}]
        if method == "AuditTail":
            return [{"audit_id": "aud_123", "transport": "dbus"}]
        raise AssertionError(f"unexpected method {method}")


class FakeHttpClient:
    def __init__(self):
        self.payloads = []

    def request_payload(self, payload):
        self.payloads.append(payload)
        return {
            "status": "executed",
            "intent": {
                "action": "window.list",
                "target": {},
                "reason": "test",
                "requires_confirmation": False,
            },
            "result": [{"window_id": "1"}],
            "message": "",
        }

    def get_json(self, path):
        if path == "/v1/apps":
            return {"apps": [{"desktop_id": "firefox.desktop"}]}
        if path == "/v1/windows":
            return {"windows": [{"window_id": "1"}]}
        if path == "/v1/capabilities":
            return {"capabilities": ["window.list"]}
        if path == "/v1/reviews/pending":
            return {"reviews": [{"review_id": "rev_123"}]}
        if path.startswith("/v1/audit/tail?n="):
            return {"entries": [{"audit_id": "aud_456", "transport": "http"}]}
        if path == "/v1/status":
            return {"status": "ok"}
        raise AssertionError(f"unexpected path {path}")


class FailingDaemonClient(FakeDaemonClient):
    def request_payload(self, payload):
        self.payloads.append(payload)
        raise RuntimeError("CommandRequest timed out")


class FailingHttpClient(FakeHttpClient):
    def request_payload(self, payload):
        self.payloads.append(payload)
        raise RuntimeError("POST /v1/command failed")


class FakePlanDaemonClient(FakeDaemonClient):
    def request_payload(self, payload):
        self.payloads.append(payload)
        return {
            "status": "review_required",
            "intent": {
                "action": "clipboard.write",
                "target": {"text": "hello"},
                "reason": "task step step_clipboard_write",
                "requires_confirmation": False,
            },
            "result": {
                "analysis": {"type": "mixed", "chat_response": "explain clipboard permissions"},
                "plan": {"schema_version": "v0.3"},
                "validation": {"ok": True},
                "plan_review": {"status": "review_required", "review_id": "rev_plan_123"},
            },
            "review_id": "rev_plan_123",
            "message": "explicit approval is required",
            "review": {
                "risk_level": "L2",
                "review_required": True,
                "allowed": True,
                "reason": "Stored task plan requires approval before execution.",
                "effects": ["May execute one or more reviewed task plan steps."],
                "reversible": False,
            },
        }

    def call_json_method(self, method, *args):
        if method == "PendingReviews":
            return [
                {
                    "review_id": "rev_plan_123",
                    "review_kind": "plan",
                    "plan_id": "plan_123",
                    "plan_payload": {"schema_version": "v0.3"},
                    "step_reviews": [{"step_id": "step_clipboard_write", "action": "clipboard.write"}],
                }
            ]
        return super().call_json_method(method, *args)


class FakePlanHttpClient(FakeHttpClient):
    def request_payload(self, payload):
        self.payloads.append(payload)
        return {
            "status": "review_required",
            "intent": {
                "action": "clipboard.write",
                "target": {"text": "hello"},
                "reason": "task step step_clipboard_write",
                "requires_confirmation": False,
            },
            "result": {
                "analysis": {"type": "mixed", "chat_response": "explain clipboard permissions"},
                "plan": {"schema_version": "v0.3"},
                "validation": {"ok": True},
                "plan_review": {"status": "review_required", "review_id": "rev_plan_456"},
            },
            "review_id": "rev_plan_456",
            "message": "explicit approval is required",
            "review": {
                "risk_level": "L2",
                "review_required": True,
                "allowed": True,
                "reason": "Stored task plan requires approval before execution.",
                "effects": ["May execute one or more reviewed task plan steps."],
                "reversible": False,
            },
        }

    def get_json(self, path):
        if path == "/v1/reviews/pending":
            return {
                "reviews": [
                    {
                        "review_id": "rev_plan_456",
                        "review_kind": "plan",
                        "plan_id": "plan_456",
                        "plan_payload": {"schema_version": "v0.3"},
                        "step_reviews": [{"step_id": "step_clipboard_write", "action": "clipboard.write"}],
                    }
                ]
            }
        return super().get_json(path)


class FakePlanApproveDaemonClient(FakeDaemonClient):
    def request_payload(self, payload):
        self.payloads.append(payload)
        return {
            "status": "executed",
            "intent": {
                "action": "app.open",
                "target": {"name": "browser"},
                "reason": "task step open_browser",
                "requires_confirmation": False,
            },
            "result": {
                "plan_id": "plan_approved",
                "status": "succeeded",
                "step_results": [
                    {
                        "step_id": "open_browser",
                        "layer": "adapter_execute",
                        "status": "succeeded",
                        "adapter": "apps.registry",
                        "capability_id": "app.open",
                        "attempt": 1,
                        "duration_ms": 5,
                        "adapter_status": "succeeded",
                        "diagnostics": {"selected_target": "firefox.desktop"},
                        "error_code": None,
                        "result": {"status": "opened", "desktop_id": "firefox.desktop"},
                        "error": None,
                        "audit_id": "aud_approve_1",
                    }
                ],
                "error": None,
            },
            "selected_target": "firefox.desktop",
            "review_id": "rev_plan_approved",
            "message": "stored task plan executed",
            "review": {
                "risk_level": "L2",
                "review_required": True,
                "allowed": True,
                "reason": "Stored task plan requires approval before execution.",
                "effects": ["May execute one or more reviewed task plan steps."],
                "reversible": False,
            },
        }


class FakePlanApproveHttpClient(FakeHttpClient):
    def request_payload(self, payload):
        self.payloads.append(payload)
        return {
            "status": "executed",
            "intent": {
                "action": "app.open",
                "target": {"name": "browser"},
                "reason": "task step open_browser",
                "requires_confirmation": False,
            },
            "result": {
                "plan_id": "plan_approved_http",
                "status": "succeeded",
                "step_results": [
                    {
                        "step_id": "open_browser",
                        "layer": "adapter_execute",
                        "status": "succeeded",
                        "adapter": "apps.registry",
                        "capability_id": "app.open",
                        "attempt": 1,
                        "duration_ms": 5,
                        "adapter_status": "succeeded",
                        "diagnostics": {"selected_target": "firefox.desktop"},
                        "error_code": None,
                        "result": {"status": "opened", "desktop_id": "firefox.desktop"},
                        "error": None,
                        "audit_id": "aud_approve_http_1",
                    }
                ],
                "error": None,
            },
            "selected_target": "firefox.desktop",
            "review_id": "rev_plan_approved_http",
            "message": "stored task plan executed",
            "review": {
                "risk_level": "L2",
                "review_required": True,
                "allowed": True,
                "reason": "Stored task plan requires approval before execution.",
                "effects": ["May execute one or more reviewed task plan steps."],
                "reversible": False,
            },
        }


def test_dbus_client_applies_vibeos_timeout_to_gdbus(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def run(command, **kwargs):
        captured["command"] = command
        captured["kwargs"] = kwargs
        return subprocess.CompletedProcess(command, 0, stdout="('{}',)\n", stderr="")

    monkeypatch.setenv("VIBEOS_TRANSPORT_TIMEOUT_SECONDS", "67")
    monkeypatch.setattr("vibeos.runtime.shutil.which", lambda name: "/usr/bin/gdbus" if name == "gdbus" else None)
    monkeypatch.setattr("vibeos.runtime.subprocess.run", run)

    assert DBusDaemonClient().call_json_method("Status") == {}
    command = captured["command"]
    kwargs = captured["kwargs"]
    assert isinstance(command, list)
    assert command[command.index("--timeout") + 1] == "67"
    assert isinstance(kwargs, dict)
    assert kwargs["timeout"] == 72
    assert kwargs["env"]["LC_ALL"] == "C"


def test_build_runtime_uses_dbus_daemon_when_available(monkeypatch) -> None:
    monkeypatch.delenv("VIBEOS_RUNTIME", raising=False)
    monkeypatch.setattr("vibeos.runtime.DBusDaemonClient.is_available", lambda self: True)
    monkeypatch.setattr("vibeos.runtime.HTTPDaemonClient.is_available", lambda self: False)

    runtime = build_runtime()

    assert isinstance(runtime, DBusDaemonRuntime)


def test_build_runtime_falls_back_to_local_broker(monkeypatch) -> None:
    monkeypatch.delenv("VIBEOS_RUNTIME", raising=False)
    monkeypatch.setattr("vibeos.runtime.DBusDaemonClient.is_available", lambda self: False)
    monkeypatch.setattr("vibeos.runtime.HTTPDaemonClient.is_available", lambda self: False)

    runtime = build_runtime()

    assert isinstance(runtime, LocalRuntime)


def test_build_runtime_uses_http_when_dbus_unavailable(monkeypatch) -> None:
    monkeypatch.delenv("VIBEOS_RUNTIME", raising=False)
    monkeypatch.setattr("vibeos.runtime.DBusDaemonClient.is_available", lambda self: False)
    monkeypatch.setattr("vibeos.runtime.HTTPDaemonClient.is_available", lambda self: True)

    runtime = build_runtime()

    assert isinstance(runtime, HTTPDaemonRuntime)


def test_build_runtime_honors_explicit_local_mode(monkeypatch) -> None:
    monkeypatch.setenv("VIBEOS_RUNTIME", "local")

    runtime = build_runtime()

    assert isinstance(runtime, LocalRuntime)


def test_build_runtime_requires_daemon_when_requested(monkeypatch) -> None:
    monkeypatch.delenv("VIBEOS_RUNTIME", raising=False)
    monkeypatch.setenv("VIBEOS_REQUIRE_DAEMON", "1")
    monkeypatch.delenv("VIBEOS_PREFER_LOCAL_BROKER", raising=False)
    monkeypatch.setattr("vibeos.runtime.DBusDaemonClient.is_available", lambda self: False)
    monkeypatch.setattr("vibeos.runtime.HTTPDaemonClient.is_available", lambda self: False)

    with pytest.raises(RuntimeSelectionError) as exc:
        build_runtime()

    assert "daemon transport is required" in str(exc.value)


def test_build_runtime_rejects_unavailable_explicit_dbus(monkeypatch) -> None:
    monkeypatch.setenv("VIBEOS_RUNTIME", "dbus")
    monkeypatch.delenv("VIBEOS_REQUIRE_DAEMON", raising=False)
    monkeypatch.setattr("vibeos.runtime.DBusDaemonClient.is_available", lambda self: False)

    with pytest.raises(RuntimeSelectionError) as exc:
        build_runtime()

    assert "D-Bus daemon transport is unavailable" in str(exc.value)


def test_detect_runtime_entry_reports_local_fallback(monkeypatch) -> None:
    monkeypatch.delenv("VIBEOS_RUNTIME", raising=False)
    monkeypatch.delenv("VIBEOS_REQUIRE_DAEMON", raising=False)
    monkeypatch.delenv("VIBEOS_PREFER_LOCAL_BROKER", raising=False)
    monkeypatch.setattr("vibeos.runtime.DBusDaemonClient.is_available", lambda self: False)
    monkeypatch.setattr("vibeos.runtime.HTTPDaemonClient.is_available", lambda self: False)

    transport, status, detail = detect_runtime_entry()

    assert transport == "local"
    assert status == "warn"
    assert detail["mode"] == "auto"


def test_detect_runtime_entry_reports_http_when_dbus_missing(monkeypatch) -> None:
    monkeypatch.delenv("VIBEOS_RUNTIME", raising=False)
    monkeypatch.delenv("VIBEOS_REQUIRE_DAEMON", raising=False)
    monkeypatch.setattr("vibeos.runtime.DBusDaemonClient.is_available", lambda self: False)
    monkeypatch.setattr("vibeos.runtime.HTTPDaemonClient.is_available", lambda self: True)

    transport, status, detail = detect_runtime_entry()

    assert transport == "http"
    assert status == "ok"
    assert detail["mode"] == "auto"


def test_detect_runtime_entry_fails_when_daemon_required_and_unavailable(monkeypatch) -> None:
    monkeypatch.delenv("VIBEOS_RUNTIME", raising=False)
    monkeypatch.setenv("VIBEOS_REQUIRE_DAEMON", "1")
    monkeypatch.delenv("VIBEOS_PREFER_LOCAL_BROKER", raising=False)
    monkeypatch.setattr("vibeos.runtime.DBusDaemonClient.is_available", lambda self: False)
    monkeypatch.setattr("vibeos.runtime.HTTPDaemonClient.is_available", lambda self: False)

    transport, status, detail = detect_runtime_entry()

    assert transport == "local"
    assert status == "fail"
    assert detail["require_daemon"] is True


def test_dbus_runtime_serializes_full_command_request() -> None:
    client = FakeDaemonClient()
    runtime = DBusDaemonRuntime(client, audit=AuditLog(make_audit_path("dbus-serialize")))

    result = runtime.handle(CommandRequest("open browser", dry_run=True, debug=True))

    assert client.payloads == [
        {
            "schema_version": "v1",
            "utterance": "open browser",
            "mode": "auto_low_risk",
            "dry_run": True,
            "approve": False,
            "review_id": None,
            "debug": True,
        }
    ]
    assert result.status == "dry_run"
    assert result.intent.action == "app.open"
    assert result.selected_target == "firefox.desktop"
    assert result.transport == "dbus"


def test_http_runtime_uses_http_contract() -> None:
    client = FakeHttpClient()
    runtime = HTTPDaemonRuntime(client, audit=AuditLog(make_audit_path("http-contract")))

    result = runtime.handle(CommandRequest("list windows"))

    assert client.payloads == [
        {
            "schema_version": "v1",
            "utterance": "list windows",
            "mode": "auto_low_risk",
            "dry_run": False,
            "approve": False,
            "review_id": None,
        }
    ]
    assert result.status == "executed"
    assert result.intent.action == "window.list"
    assert result.transport == "http"
    assert runtime.list_apps() == [{"desktop_id": "firefox.desktop"}]
    assert runtime.list_windows() == [{"window_id": "1"}]
    assert runtime.audit_tail(7) == [{"audit_id": "aud_456", "transport": "http"}]


def test_dbus_runtime_exposes_audit_tail() -> None:
    client = FakeDaemonClient()
    runtime = DBusDaemonRuntime(client, audit=AuditLog(make_audit_path("dbus-audit-tail")))

    assert runtime.audit_tail(5) == [{"audit_id": "aud_123", "transport": "dbus"}]


def test_dbus_runtime_returns_structured_transport_timeout(monkeypatch) -> None:
    monkeypatch.setenv("VIBEOS_RUNTIME", "dbus")
    runtime = DBusDaemonRuntime(FailingDaemonClient(), audit=AuditLog(make_audit_path("dbus-transport-timeout")))

    result = runtime.handle(CommandRequest("open browser"))

    assert result.status == "failed"
    assert result.transport == "dbus"
    assert result.result["error"] == "transport_timeout"
    assert result.result["run"]["attempt_ids"]
    assert result.result["attempts"][0]["failure"]["failure_class"] == "transport_timeout"
    assert runtime.audit_tail(5)[-1]["audit_id"] == result.audit_id


def test_dbus_runtime_falls_back_to_http_in_auto_mode(monkeypatch) -> None:
    monkeypatch.delenv("VIBEOS_RUNTIME", raising=False)
    runtime = DBusDaemonRuntime(
        FailingDaemonClient(),
        audit=AuditLog(make_audit_path("dbus-http-fallback")),
        http_fallback_client=FakeHttpClient(),
    )

    result = runtime.handle(CommandRequest("list windows"))

    assert result.status == "executed"
    assert result.transport == "http"
    assert result.intent.action == "window.list"


def test_dbus_runtime_does_not_fall_back_when_dbus_explicit(monkeypatch) -> None:
    monkeypatch.setenv("VIBEOS_RUNTIME", "dbus")
    runtime = DBusDaemonRuntime(
        FailingDaemonClient(),
        audit=AuditLog(make_audit_path("dbus-no-http-fallback")),
        http_fallback_client=FakeHttpClient(),
    )

    result = runtime.handle(CommandRequest("open browser"))

    assert result.status == "failed"
    assert result.transport == "dbus"
    assert result.result["error"] == "transport_timeout"


def test_http_runtime_returns_structured_transport_failure() -> None:
    runtime = HTTPDaemonRuntime(FailingHttpClient(), audit=AuditLog(make_audit_path("http-transport-failure")))

    result = runtime.handle(CommandRequest("open browser"))

    assert result.status == "failed"
    assert result.transport == "http"
    assert result.result["error"] == "transport_unavailable"
    assert result.result["attempts"][0]["failure"]["failure_class"] == "environment_unreachable"
    assert runtime.audit_tail(5)[-1]["audit_id"] == result.audit_id


def test_dbus_runtime_preserves_plan_review_metadata() -> None:
    client = FakePlanDaemonClient()
    runtime = DBusDaemonRuntime(client, audit=AuditLog(make_audit_path("dbus-plan-review")))

    result = runtime.handle(CommandRequest("explain clipboard permissions and then copy hello to clipboard"))

    assert result.status == "review_required"
    assert result.transport == "dbus"
    assert result.review_id == "rev_plan_123"
    assert result.result["analysis"]["type"] == "mixed"
    assert result.result["plan"]["schema_version"] == "v0.3"
    assert result.result["plan_review"]["status"] == "review_required"


def test_http_runtime_preserves_plan_review_metadata() -> None:
    client = FakePlanHttpClient()
    runtime = HTTPDaemonRuntime(client, audit=AuditLog(make_audit_path("http-plan-review")))

    result = runtime.handle(CommandRequest("explain clipboard permissions and then copy hello to clipboard"))

    assert result.status == "review_required"
    assert result.transport == "http"
    assert result.review_id == "rev_plan_456"
    assert result.result["analysis"]["type"] == "mixed"
    assert result.result["plan"]["schema_version"] == "v0.3"
    assert result.result["plan_review"]["status"] == "review_required"


def test_dbus_runtime_pending_reviews_preserves_plan_fields() -> None:
    client = FakePlanDaemonClient()
    runtime = DBusDaemonRuntime(client, audit=AuditLog(make_audit_path("dbus-pending-reviews")))

    reviews = runtime.pending_reviews()

    assert reviews[0]["review_kind"] == "plan"
    assert reviews[0]["plan_id"] == "plan_123"
    assert reviews[0]["plan_payload"]["schema_version"] == "v0.3"


def test_http_runtime_pending_reviews_preserves_plan_fields() -> None:
    client = FakePlanHttpClient()
    runtime = HTTPDaemonRuntime(client, audit=AuditLog(make_audit_path("http-pending-reviews")))

    reviews = runtime.pending_reviews()

    assert reviews[0]["review_kind"] == "plan"
    assert reviews[0]["plan_id"] == "plan_456"
    assert reviews[0]["plan_payload"]["schema_version"] == "v0.3"


def test_dbus_runtime_approve_review_preserves_plan_execution_metadata() -> None:
    client = FakePlanApproveDaemonClient()
    runtime = DBusDaemonRuntime(client, audit=AuditLog(make_audit_path("dbus-approve-review")))

    result = runtime.handle(CommandRequest("", review_id="rev_plan_approved", approve=True))

    assert result.status == "executed"
    assert result.transport == "dbus"
    assert result.selected_target == "firefox.desktop"
    assert result.result["plan_id"] == "plan_approved"
    assert result.result["step_results"][0]["layer"] == "adapter_execute"
    assert result.result["step_results"][0]["adapter"] == "apps.registry"
    assert result.result["step_results"][0]["diagnostics"]["selected_target"] == "firefox.desktop"


def test_http_runtime_approve_review_preserves_plan_execution_metadata() -> None:
    client = FakePlanApproveHttpClient()
    runtime = HTTPDaemonRuntime(client, audit=AuditLog(make_audit_path("http-approve-review")))

    result = runtime.handle(CommandRequest("", review_id="rev_plan_approved_http", approve=True))

    assert result.status == "executed"
    assert result.transport == "http"
    assert result.selected_target == "firefox.desktop"
    assert result.result["plan_id"] == "plan_approved_http"
    assert result.result["step_results"][0]["layer"] == "adapter_execute"
    assert result.result["step_results"][0]["adapter"] == "apps.registry"
    assert result.result["step_results"][0]["diagnostics"]["selected_target"] == "firefox.desktop"


def test_dbus_runtime_forwards_supplemental_input() -> None:
    client = FakeDaemonClient()
    runtime = DBusDaemonRuntime(client, audit=AuditLog(make_audit_path("dbus-supplemental-input")))

    runtime.handle(CommandRequest("", review_id="rev_user_input", supplemental_input="browser"))

    assert client.payloads[-1]["review_id"] == "rev_user_input"
    assert client.payloads[-1]["supplemental_input"] == "browser"


def test_http_runtime_forwards_supplemental_input() -> None:
    client = FakeHttpClient()
    runtime = HTTPDaemonRuntime(client, audit=AuditLog(make_audit_path("http-supplemental-input")))

    runtime.handle(CommandRequest("", review_id="rev_user_input", supplemental_input="browser"))

    assert client.payloads[-1]["review_id"] == "rev_user_input"
    assert client.payloads[-1]["supplemental_input"] == "browser"


def make_audit_path(name: str):
    from pathlib import Path
    from uuid import uuid4

    return Path(".vibeos") / f"audit-{name}-{uuid4().hex}.jsonl"
