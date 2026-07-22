from __future__ import annotations

import json
import os
import shutil
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, replace
from hashlib import sha256
from ipaddress import ip_address
from typing import Any

from .audit import AuditLog
from .broker import CapabilityBroker
from .config import transport_timeout_seconds
from .models import CommandRequest, CommandResult, Intent, PermissionReview, utc_now_iso
from .task_models import AgentRun, FailureClassification, PlanAttempt, ReplanDecision
from .windows import unwrap_gdbus_string

TRUTHY_ENV_VALUES = {"1", "true", "yes", "on"}


class RuntimeSelectionError(RuntimeError):
    def __init__(self, message: str, *, mode: str, configured_transport: str, require_daemon: bool, detail: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.mode = mode
        self.configured_transport = configured_transport
        self.require_daemon = require_daemon
        self.detail = detail or {}

    def to_payload(self) -> dict[str, Any]:
        return {
            "status": "failed",
            "error": "runtime_unavailable",
            "message": str(self),
            "detail": {
                "mode": self.mode,
                "configured_transport": self.configured_transport,
                "require_daemon": self.require_daemon,
                **self.detail,
            },
        }


class LocalRuntime:
    transport_name = "local"

    def __init__(self, broker: CapabilityBroker | None = None) -> None:
        self.broker = broker or CapabilityBroker()

    def handle(self, request: CommandRequest) -> CommandResult:
        return self.broker.handle(replace(request, transport=self.transport_name))

    def list_apps(self) -> list[dict[str, Any]]:
        return [dict(item) for item in self.broker.list_apps(transport=self.transport_name)]

    def list_windows(self) -> list[dict[str, Any]]:
        return [dict(item) for item in self.broker.list_windows(transport=self.transport_name)]

    def capabilities(self) -> dict[str, Any]:
        return self.broker.capabilities()

    def pending_reviews(self) -> list[dict[str, Any]]:
        return self.broker.pending_reviews()

    def reject_review(self, review_id: str) -> CommandResult:
        return self.broker.reject_review(review_id, transport=self.transport_name)

    def audit_tail(self, count: int = 20) -> list[dict[str, Any]]:
        return self.broker.audit.tail(count)

    def tasks(self, *, status: str | None = None, limit: int = 100) -> list[dict[str, object]]:
        return self.broker.tasks(status=status, limit=limit)

    def task(self, task_id: str) -> dict[str, object] | None:
        return self.broker.task(task_id)

    def control_task(self, task_id: str, operation: str, *, expected_revision: int, owner: str | None = None, reason: str = "") -> dict[str, object]:
        try:
            return self.broker.control_task(task_id, operation, expected_revision=expected_revision, owner=owner, reason=reason)
        except (KeyError, ValueError, RuntimeError) as exc:
            return {"error": type(exc).__name__, "message": str(exc)}


class DBusDaemonRuntime:
    transport_name = "dbus"

    def __init__(
        self,
        client: "DBusDaemonClient",
        audit: AuditLog | None = None,
        http_fallback_client: "HTTPDaemonClient | None" = None,
    ) -> None:
        self.client = client
        self.audit = audit or AuditLog()
        self.http_fallback_client = http_fallback_client

    def handle(self, request: CommandRequest) -> CommandResult:
        try:
            response = self.client.request_payload(command_request_payload(request))
        except RuntimeError as exc:
            return self._record_transport_failure(request, str(exc))
        return with_transport(command_result_from_payload(response), self.transport_name)

    def list_apps(self) -> list[dict[str, Any]]:
        return self.client.call_json_method("AppsList")

    def list_windows(self) -> list[dict[str, Any]]:
        return self.client.call_json_method("WindowsList")

    def capabilities(self) -> dict[str, Any]:
        return self.client.call_json_method("Capabilities")

    def pending_reviews(self) -> list[dict[str, Any]]:
        return self.client.call_json_method("PendingReviews")

    def reject_review(self, review_id: str) -> CommandResult:
        request = CommandRequest("", review_id=review_id, transport=self.transport_name)
        try:
            response = self.client.request_payload({"schema_version": "v1", "review_id": review_id, "reject": True})
        except RuntimeError as exc:
            return self._record_transport_failure(request, str(exc))
        return with_transport(command_result_from_payload(response), self.transport_name)

    def audit_tail(self, count: int = 20) -> list[dict[str, Any]]:
        local_entries = self.audit.tail(count)
        try:
            remote_entries = self.client.call_json_method("AuditTail", str(count))
        except RuntimeError:
            remote_entries = []
        return [*remote_entries, *local_entries][-count:]

    def tasks(self, *, status: str | None = None, limit: int = 100) -> list[dict[str, object]]:
        payload = {"schema_version": "v1", "status": status, "limit": limit}
        return self.client.call_json_method("TasksList", json.dumps(payload, separators=(",", ":")))

    def task(self, task_id: str) -> dict[str, object] | None:
        return self.client.call_json_method("TaskShow", task_id)

    def control_task(self, task_id: str, operation: str, *, expected_revision: int, owner: str | None = None, reason: str = "") -> dict[str, object]:
        payload = {
            "schema_version": "v1",
            "task_id": task_id,
            "operation": operation,
            "expected_revision": expected_revision,
            "owner": owner,
            "reason": reason,
        }
        return self.client.call_json_method("TaskControl", json.dumps(payload, separators=(",", ":")))

    def _record_transport_failure(self, request: CommandRequest, message: str) -> CommandResult:
        result = transport_error_result(self.transport_name, message, request=request)
        audit_id = self.audit.record(
            request=replace(request, transport=self.transport_name),
            intent=result.intent,
            status=result.status,
            result=result.result,
            selected_target=result.selected_target,
            message=result.message,
            execution_status=result.execution_status,
            acceptance_status=result.acceptance_status,
            overall_status=result.overall_status,
        )
        return replace(result, audit_id=audit_id)


class HTTPDaemonRuntime:
    transport_name = "http"

    def __init__(self, client: "HTTPDaemonClient", audit: AuditLog | None = None) -> None:
        self.client = client
        self.audit = audit or AuditLog()

    def handle(self, request: CommandRequest) -> CommandResult:
        try:
            response = self.client.request_payload(command_request_payload(request))
        except RuntimeError as exc:
            return self._record_transport_failure(request, str(exc))
        return with_transport(command_result_from_payload(response), self.transport_name)

    def list_apps(self) -> list[dict[str, Any]]:
        payload = self.client.get_json("/v1/apps")
        return payload.get("apps", []) if isinstance(payload, dict) else []

    def list_windows(self) -> list[dict[str, Any]]:
        payload = self.client.get_json("/v1/windows")
        return payload.get("windows", []) if isinstance(payload, dict) else []

    def capabilities(self) -> dict[str, Any]:
        payload = self.client.get_json("/v1/capabilities")
        return payload if isinstance(payload, dict) else {}

    def pending_reviews(self) -> list[dict[str, Any]]:
        payload = self.client.get_json("/v1/reviews/pending")
        return payload.get("reviews", []) if isinstance(payload, dict) else []

    def reject_review(self, review_id: str) -> CommandResult:
        request = CommandRequest("", review_id=review_id, transport=self.transport_name)
        try:
            response = self.client.request_payload({"schema_version": "v1", "review_id": review_id, "reject": True})
        except RuntimeError as exc:
            return self._record_transport_failure(request, str(exc))
        return with_transport(command_result_from_payload(response), self.transport_name)

    def audit_tail(self, count: int = 20) -> list[dict[str, Any]]:
        local_entries = self.audit.tail(count)
        try:
            payload = self.client.get_json(f"/v1/audit/tail?n={count}")
            remote_entries = payload.get("entries", []) if isinstance(payload, dict) else []
        except RuntimeError:
            remote_entries = []
        return [*remote_entries, *local_entries][-count:]

    def tasks(self, *, status: str | None = None, limit: int = 100) -> list[dict[str, object]]:
        query = urllib.parse.urlencode({"status": status, "limit": limit} if status is not None else {"limit": limit})
        payload = self.client.get_json(f"/v1/tasks?{query}")
        return payload.get("tasks", []) if isinstance(payload, dict) else []

    def task(self, task_id: str) -> dict[str, object] | None:
        payload = self.client.get_json(f"/v1/tasks/{urllib.parse.quote(task_id, safe='')}")
        return payload if isinstance(payload, dict) else None

    def control_task(
        self,
        task_id: str,
        operation: str,
        *,
        expected_revision: int,
        owner: str | None = None,
        reason: str = "",
    ) -> dict[str, object]:
        payload = self.client.post_json(
            f"/v1/tasks/{urllib.parse.quote(task_id, safe='')}/control",
            {
                "schema_version": "v1",
                "operation": operation,
                "expected_revision": expected_revision,
                "owner": owner,
                "reason": reason,
            },
        )
        return payload if isinstance(payload, dict) else {"error": "invalid_response"}

    def _record_transport_failure(self, request: CommandRequest, message: str) -> CommandResult:
        result = transport_error_result(self.transport_name, message, request=request)
        audit_id = self.audit.record(
            request=replace(request, transport=self.transport_name),
            intent=result.intent,
            status=result.status,
            result=result.result,
            selected_target=result.selected_target,
            message=result.message,
            execution_status=result.execution_status,
            acceptance_status=result.acceptance_status,
            overall_status=result.overall_status,
        )
        return replace(result, audit_id=audit_id)


class DBusDaemonClient:
    BUS_NAME = "org.vibeos.Agent"
    OBJECT_PATH = "/org/vibeos/Agent"
    INTERFACE = "org.vibeos.Agent"

    def is_available(self) -> bool:
        if os.environ.get("VIBEOS_PREFER_LOCAL_BROKER") == "1":
            return False
        try:
            self.call_json_method("Capabilities")
        except RuntimeError:
            return False
        return True

    def request_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self.call_json_method("CommandRequest", json.dumps(payload, ensure_ascii=False))

    def call_json_method(self, method: str, *args: str) -> Any:
        raw = self._call(method, *args)
        try:
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"invalid JSON from {method}: {raw}") from exc

    def _call(self, method: str, *args: str) -> str:
        gdbus = shutil.which("gdbus")
        if not gdbus or os.name != "posix":
            raise RuntimeError("gdbus is unavailable")
        timeout = transport_timeout_seconds()
        command = [
            gdbus,
            "call",
            "--session",
            "--timeout",
            str(timeout),
            "--dest",
            self.BUS_NAME,
            "--object-path",
            self.OBJECT_PATH,
            "--method",
            f"{self.INTERFACE}.{method}",
            *args,
        ]
        try:
            completed = subprocess.run(command, check=False, capture_output=True, text=True, timeout=timeout + 5, env={**os.environ, "LC_ALL": "C"})
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(f"{method} timed out after {timeout} seconds") from exc
        if completed.returncode != 0:
            raise RuntimeError(completed.stderr.strip() or completed.stdout.strip() or f"{method} failed")
        return unwrap_gdbus_string(completed.stdout.strip())


class HTTPDaemonClient:
    def __init__(self, base_url: str | None = None) -> None:
        self.base_url = (base_url or os.environ.get("VIBEOS_DAEMON_URL") or "http://127.0.0.1:8765").rstrip("/")
        _require_loopback_http_url(self.base_url)

    def is_available(self) -> bool:
        if os.environ.get("VIBEOS_PREFER_LOCAL_BROKER") == "1":
            return False
        try:
            payload = self.get_json("/v1/status")
        except RuntimeError:
            return False
        return isinstance(payload, dict) and payload.get("status") == "ok"

    def request_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        response = self.post_json("/v1/command", payload)
        if not isinstance(response, dict):
            raise RuntimeError(f"unexpected command response: {response!r}")
        return response

    def get_json(self, path: str) -> Any:
        return self._request_json("GET", path)

    def post_json(self, path: str, payload: dict[str, Any]) -> Any:
        return self._request_json("POST", path, payload)

    def _request_json(self, method: str, path: str, payload: dict[str, Any] | None = None) -> Any:
        data = None
        headers = {}
        if payload is not None:
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json; charset=utf-8"
        request = urllib.request.Request(f"{self.base_url}{path}", data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=transport_timeout_seconds()) as response:
                body = response.read().decode("utf-8")
        except (urllib.error.URLError, TimeoutError) as exc:
            raise RuntimeError(f"{method} {path} failed") from exc
        try:
            return json.loads(body)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"invalid JSON from {method} {path}: {body}") from exc


def runtime_mode() -> str:
    return (os.environ.get("VIBEOS_RUNTIME") or "auto").strip().lower()


def env_flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in TRUTHY_ENV_VALUES


def daemon_required() -> bool:
    return env_flag("VIBEOS_REQUIRE_DAEMON")


def command_request_payload(request: CommandRequest) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "schema_version": "v1",
        "utterance": request.utterance,
        "mode": request.mode,
        "dry_run": request.dry_run,
        "approve": request.approve,
        "review_id": request.review_id,
    }
    if request.supplemental_input is not None:
        payload["supplemental_input"] = request.supplemental_input
    if request.debug:
        payload["debug"] = True
    return payload


def build_runtime() -> LocalRuntime | DBusDaemonRuntime | HTTPDaemonRuntime:
    mode = runtime_mode()
    require_daemon = daemon_required()
    if mode == "local" or env_flag("VIBEOS_PREFER_LOCAL_BROKER"):
        if require_daemon:
            raise _selection_error("local broker conflicts with daemon-required mode", mode, "local", require_daemon)
        return LocalRuntime()
    if mode == "dbus":
        client = DBusDaemonClient()
        if client.is_available():
            return DBusDaemonRuntime(client)
        raise _selection_error("D-Bus daemon transport is unavailable", mode, "dbus", require_daemon)
    if mode == "http":
        client = _http_client(mode, require_daemon)
        if client.is_available():
            return HTTPDaemonRuntime(client)
        raise _selection_error("HTTP daemon transport is unavailable", mode, "http", require_daemon)
    if mode != "auto":
        raise _selection_error(f"unsupported runtime transport: {mode}", mode, mode, require_daemon)
    dbus_client = DBusDaemonClient()
    if dbus_client.is_available():
        return DBusDaemonRuntime(dbus_client)
    http_client = _http_client(mode, require_daemon)
    if http_client.is_available():
        return HTTPDaemonRuntime(http_client)
    if require_daemon:
        raise _selection_error(
            "daemon transport is required, but neither D-Bus nor HTTP is available",
            mode,
            "daemon",
            require_daemon,
        )
    return LocalRuntime()


def detect_runtime_entry() -> tuple[str, str, dict[str, Any]]:
    mode = runtime_mode()
    require_daemon = daemon_required()
    if mode == "local" or env_flag("VIBEOS_PREFER_LOCAL_BROKER"):
        status = "fail" if require_daemon else "warn"
        return "local", status, {"mode": mode, "require_daemon": require_daemon, "reason": "forced local broker"}
    if mode == "dbus":
        available = DBusDaemonClient().is_available()
        status = "ok" if available else "fail"
        reason = None if available else "configured D-Bus transport is unavailable"
        return "dbus", status, {"mode": mode, "require_daemon": require_daemon, "reason": reason}
    if mode == "http":
        try:
            client = HTTPDaemonClient()
        except ValueError as exc:
            return "http", "fail", {"mode": mode, "require_daemon": require_daemon, "reason": str(exc)}
        available = client.is_available()
        status = "ok" if available else "fail"
        reason = None if available else "configured HTTP transport is unavailable"
        return "http", status, {"mode": mode, "require_daemon": require_daemon, "base_url": client.base_url, "reason": reason}
    if mode != "auto":
        return mode, "fail", {"mode": mode, "require_daemon": require_daemon, "reason": "unsupported transport"}
    if DBusDaemonClient().is_available():
        return "dbus", "ok", {"mode": mode, "require_daemon": require_daemon}
    try:
        http_client = HTTPDaemonClient()
    except ValueError as exc:
        return "http", "fail", {"mode": mode, "require_daemon": require_daemon, "reason": str(exc)}
    if http_client.is_available():
        return (
            "http",
            "ok",
            {
                "mode": mode,
                "require_daemon": require_daemon,
                "base_url": http_client.base_url,
                "reason": "D-Bus unavailable; using deprecated HTTP compatibility",
            },
        )
    status = "fail" if require_daemon else "warn"
    return (
        "local",
        status,
        {
            "mode": mode,
            "require_daemon": require_daemon,
            "base_url": http_client.base_url,
            "reason": "no daemon transport available; using local broker" if not require_daemon else "no daemon transport available",
        },
    )


def command_result_from_payload(payload: dict[str, Any]) -> CommandResult:
    intent_payload = payload.get("intent") if isinstance(payload.get("intent"), dict) else {}
    review_payload = payload.get("review") if isinstance(payload.get("review"), dict) else None
    return CommandResult(
        status=str(payload.get("status", "failed")),
        intent=Intent(
            action=str(intent_payload.get("action", "unknown")),
            target=intent_payload.get("target", {}) if isinstance(intent_payload.get("target"), dict) else {},
            reason=str(intent_payload.get("reason", "")),
            requires_confirmation=bool(intent_payload.get("requires_confirmation", False)),
        ),
        result=payload.get("result"),
        selected_target=_optional_text(payload.get("selected_target")),
        trace_run_id=_optional_text(payload.get("trace_run_id")),
        audit_id=_optional_text(payload.get("audit_id")),
        review_id=_optional_text(payload.get("review_id")),
        transport=_optional_text(payload.get("transport")),
        message=str(payload.get("message", "")),
        review=permission_review_from_payload(review_payload),
        execution_status=str(payload.get("execution_status", "not_started")),
        acceptance_status=str(payload.get("acceptance_status", "skipped")),
        overall_status=str(payload.get("overall_status", "failed")),
    )


def permission_review_from_payload(payload: dict[str, Any] | None) -> PermissionReview | None:
    if not payload:
        return None
    return PermissionReview(
        risk_level=str(payload.get("risk_level", "L3")),
        review_required=bool(payload.get("review_required", False)),
        allowed=bool(payload.get("allowed", False)),
        reason=str(payload.get("reason", "")),
        effects=tuple(str(item) for item in payload.get("effects", ()) if item is not None),
        reversible=bool(payload.get("reversible", False)),
    )


def with_transport(result: CommandResult, transport: str) -> CommandResult:
    return result if result.transport == transport else replace(result, transport=transport)


def transport_error_result(transport: str, message: str, request: CommandRequest | None = None) -> CommandResult:
    error_code = "transport_timeout" if "timeout" in message.lower() or "timed out" in message.lower() else "transport_unavailable"
    utterance = request.utterance if request is not None else ""
    run_id = f"run_{sha256(f'{utc_now_iso()}:{transport}:{utterance}'.encode('utf-8')).hexdigest()[:12]}"
    attempt_id = f"attempt_{sha256(f'{run_id}:{transport}'.encode('utf-8')).hexdigest()[:10]}"
    failure = FailureClassification(
        failure_class="transport_timeout" if error_code == "transport_timeout" else "environment_unreachable",
        message=message,
        retryable=False,
        details={
            "transport": transport,
            "error": error_code,
            "delivery_outcome": "unknown" if error_code == "transport_timeout" else "not_delivered",
            "safe_to_retry": False if error_code == "transport_timeout" else True,
        },
    )
    payload = {
        "error": error_code,
        "transport": transport,
        "message": message,
        "delivery_outcome": "unknown" if error_code == "transport_timeout" else "not_delivered",
        "safe_to_retry": False if error_code == "transport_timeout" else True,
        "run": asdict(AgentRun(run_id, "transport_goal_unresolved", utterance, "failed", transport, (attempt_id,), "failed")),
        "attempts": [
            asdict(
                PlanAttempt(
                    attempt_id,
                    run_id,
                    1,
                    "transport_request",
                    selected_route_id="daemon_transport",
                    failure=failure,
                    replan_decision=ReplanDecision(action="stop", reason=message),
                )
            )
        ],
    }
    return CommandResult(
        status="failed",
        intent=Intent.unknown(f"{transport} transport request failed"),
        result=payload,
        transport=transport,
        message=message,
        execution_status="failed",
        acceptance_status="skipped",
        overall_status="failed",
    )


def _selection_error(message: str, mode: str, transport: str, required: bool) -> RuntimeSelectionError:
    return RuntimeSelectionError(message, mode=mode, configured_transport=transport, require_daemon=required)


def _http_client(mode: str, required: bool) -> HTTPDaemonClient:
    try:
        return HTTPDaemonClient()
    except ValueError as exc:
        raise _selection_error(str(exc), mode, "http", required) from exc


def _require_loopback_http_url(base_url: str) -> None:
    parsed = urllib.parse.urlparse(base_url)
    if parsed.scheme != "http" or parsed.username is not None or parsed.password is not None or parsed.hostname is None:
        raise ValueError("HTTP compatibility URL must be an unauthenticated loopback http:// URL")
    if parsed.hostname.lower() == "localhost":
        return
    try:
        address = ip_address(parsed.hostname)
    except ValueError as exc:
        raise ValueError("HTTP compatibility URL host must be loopback") from exc
    if not address.is_loopback:
        raise ValueError("HTTP compatibility transport is loopback-only")


def _optional_text(value: object) -> str | None:
    return str(value) if value is not None else None
