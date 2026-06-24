from __future__ import annotations

import json
import os
import shutil
import subprocess
import urllib.error
import urllib.request
from dataclasses import asdict, replace
from hashlib import sha256
from typing import Any

from .audit import AuditLog
from .broker import CapabilityBroker
from .config import transport_timeout_seconds
from .models import CommandRequest, CommandResult, Intent, PermissionReview, utc_now_iso
from .task_models import AgentRun, FailureClassification, PlanAttempt, ReplanDecision
from .windows import unwrap_gdbus_string

TRUTHY_ENV_VALUES = {"1", "true", "yes", "on"}


class RuntimeSelectionError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        mode: str,
        configured_transport: str,
        require_daemon: bool,
        detail: dict[str, Any] | None = None,
    ) -> None:
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
        return [asdict(app) for app in self.broker.apps.list_apps()]

    def list_windows(self) -> list[dict[str, Any]]:
        return [asdict(window) for window in self.broker.windows.list_windows()]

    def capabilities(self) -> dict[str, Any]:
        return self.broker.capabilities()

    def pending_reviews(self) -> list[dict[str, Any]]:
        return self.broker.pending_reviews()

    def reject_review(self, review_id: str) -> CommandResult:
        return self.broker.reject_review(review_id, transport=self.transport_name)

    def audit_tail(self, count: int = 20) -> list[dict[str, Any]]:
        return self.broker.audit.tail(count)


class DBusDaemonRuntime:
    transport_name = "dbus"

    def __init__(self, client: "DBusDaemonClient", audit: AuditLog | None = None) -> None:
        self.client = client
        self.audit = audit or AuditLog()

    def handle(self, request: CommandRequest) -> CommandResult:
        payload = {
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
        try:
            response = self.client.request_payload(payload)
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
        try:
            response = self.client.request_payload({"review_id": review_id, "reject": True})
        except RuntimeError as exc:
            return self._record_transport_failure(CommandRequest("", review_id=review_id, transport=self.transport_name), str(exc))
        return with_transport(command_result_from_payload(response), self.transport_name)

    def audit_tail(self, count: int = 20) -> list[dict[str, Any]]:
        local_entries = self.audit.tail(count)
        try:
            remote_entries = self.client.call_json_method("AuditTail", str(count))
        except RuntimeError:
            remote_entries = []
        merged = [*remote_entries, *local_entries]
        return merged[-count:]

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
        payload = {
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
        try:
            response = self.client.request_payload(payload)
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
        try:
            response = self.client.request_payload({"review_id": review_id, "reject": True})
        except RuntimeError as exc:
            return self._record_transport_failure(CommandRequest("", review_id=review_id, transport=self.transport_name), str(exc))
        return with_transport(command_result_from_payload(response), self.transport_name)

    def audit_tail(self, count: int = 20) -> list[dict[str, Any]]:
        local_entries = self.audit.tail(count)
        try:
            payload = self.client.get_json(f"/v1/audit/tail?n={count}")
            remote_entries = payload.get("entries", []) if isinstance(payload, dict) else []
        except RuntimeError:
            remote_entries = []
        merged = [*remote_entries, *local_entries]
        return merged[-count:]

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
        command = [
            gdbus,
            "call",
            "--session",
            "--dest",
            self.BUS_NAME,
            "--object-path",
            self.OBJECT_PATH,
            "--method",
            f"{self.INTERFACE}.{method}",
            *args,
        ]
        try:
            completed = subprocess.run(
                command,
                check=False,
                capture_output=True,
                text=True,
                timeout=transport_timeout_seconds(),
            )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(f"{method} timed out") from exc
        if completed.returncode != 0:
            raise RuntimeError(completed.stderr.strip() or completed.stdout.strip() or f"{method} failed")
        return unwrap_gdbus_string(completed.stdout.strip())


class HTTPDaemonClient:
    def __init__(self, base_url: str | None = None) -> None:
        self.base_url = (base_url or os.environ.get("VIBEOS_DAEMON_URL") or "http://127.0.0.1:8765").rstrip("/")

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


def build_runtime() -> LocalRuntime | DBusDaemonRuntime | HTTPDaemonRuntime:
    mode = runtime_mode()
    require_daemon = daemon_required()
    if mode == "local" or env_flag("VIBEOS_PREFER_LOCAL_BROKER"):
        if require_daemon:
            raise RuntimeSelectionError(
                "daemon transport is required, but the local broker was forced",
                mode=mode,
                configured_transport="local",
                require_daemon=True,
                detail={"reason": "local broker override conflicts with daemon-required mode"},
            )
        return LocalRuntime()
    if mode == "dbus":
        client = DBusDaemonClient()
        if not client.is_available():
            raise RuntimeSelectionError(
                "configured D-Bus daemon transport is unavailable",
                mode=mode,
                configured_transport="dbus",
                require_daemon=require_daemon,
                detail={"reason": "gdbus call to org.vibeos.Agent did not succeed"},
            )
        return DBusDaemonRuntime(client)
    if mode == "http":
        client = HTTPDaemonClient()
        if not client.is_available():
            raise RuntimeSelectionError(
                "configured HTTP daemon transport is unavailable",
                mode=mode,
                configured_transport="http",
                require_daemon=require_daemon,
                detail={"base_url": client.base_url, "reason": "HTTP daemon status check did not succeed"},
            )
        return HTTPDaemonRuntime(client)
    if mode != "auto":
        raise RuntimeSelectionError(
            f"unknown runtime mode: {mode}",
            mode=mode,
            configured_transport="unknown",
            require_daemon=require_daemon,
        )

    client = DBusDaemonClient()
    if client.is_available():
        return DBusDaemonRuntime(client)
    http_client = HTTPDaemonClient()
    if http_client.is_available():
        return HTTPDaemonRuntime(http_client)
    if require_daemon:
        raise RuntimeSelectionError(
            "daemon transport is required, but neither D-Bus nor HTTP is available",
            mode=mode,
            configured_transport="daemon",
            require_daemon=True,
            detail={"base_url": http_client.base_url, "reason": "no daemon transport passed availability checks"},
        )
    return LocalRuntime()


def detect_runtime_entry() -> tuple[str, str, dict[str, Any]]:
    mode = runtime_mode()
    require_daemon = daemon_required()
    if mode == "local" or env_flag("VIBEOS_PREFER_LOCAL_BROKER"):
        if require_daemon:
            return (
                "local",
                "fail",
                {
                    "mode": mode,
                    "require_daemon": True,
                    "reason": "local broker override conflicts with daemon-required mode",
                },
            )
        return ("local", "warn", {"mode": mode, "reason": "forced local broker"})
    if mode == "dbus":
        client = DBusDaemonClient()
        if client.is_available():
            return ("dbus", "ok", {"mode": mode, "require_daemon": require_daemon})
        return ("dbus", "fail", {"mode": mode, "require_daemon": require_daemon, "reason": "configured dbus transport is unavailable"})
    if mode == "http":
        client = HTTPDaemonClient()
        if client.is_available():
            return ("http", "ok", {"mode": mode, "base_url": client.base_url, "require_daemon": require_daemon})
        return (
            "http",
            "fail",
            {
                "mode": mode,
                "base_url": client.base_url,
                "require_daemon": require_daemon,
                "reason": "configured http transport is unavailable",
            },
        )
    if mode != "auto":
        return ("local", "fail", {"mode": mode, "require_daemon": require_daemon, "reason": "unknown runtime mode"})

    dbus_client = DBusDaemonClient()
    if dbus_client.is_available():
        return ("dbus", "ok", {"mode": "auto", "require_daemon": require_daemon})
    http_client = HTTPDaemonClient()
    if http_client.is_available():
        return (
            "http",
            "ok",
            {
                "mode": "auto",
                "base_url": http_client.base_url,
                "require_daemon": require_daemon,
                "reason": "dbus unavailable; using http daemon",
            },
        )
    if require_daemon:
        return (
            "local",
            "fail",
            {
                "mode": "auto",
                "base_url": http_client.base_url,
                "require_daemon": True,
                "reason": "no daemon transport available",
            },
        )
    return ("local", "warn", {"mode": "auto", "require_daemon": False, "reason": "no daemon transport available; falling back to local broker"})


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
        selected_target=str(payload["selected_target"]) if payload.get("selected_target") is not None else None,
        trace_run_id=str(payload["trace_run_id"]) if payload.get("trace_run_id") is not None else None,
        audit_id=str(payload["audit_id"]) if payload.get("audit_id") is not None else None,
        review_id=str(payload["review_id"]) if payload.get("review_id") is not None else None,
        transport=str(payload["transport"]) if payload.get("transport") is not None else None,
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
    if result.transport == transport:
        return result
    return replace(result, transport=transport)


def transport_error_result(transport: str, message: str, request: CommandRequest | None = None) -> CommandResult:
    lowered = message.lower()
    error_code = "transport_timeout" if "timed out" in lowered or "timeout" in lowered else "transport_unavailable"
    utterance = request.utterance if request is not None else ""
    run_id = _make_transport_run_id(utterance, transport)
    attempt_id = _make_transport_attempt_id(run_id, transport)
    failure = FailureClassification(
        failure_class="transport_timeout" if error_code == "transport_timeout" else "environment_unreachable",
        message=message,
        retryable=error_code == "transport_timeout",
        details={"transport": transport, "error": error_code},
    )
    payload = {
        "error": error_code,
        "transport": transport,
        "message": message,
        "run": asdict(
            AgentRun(
                run_id=run_id,
                goal_id="transport_goal_unresolved",
                utterance=utterance,
                status="failed",
                selected_transport=transport,
                attempt_ids=(attempt_id,),
                final_outcome="failed",
            )
        ),
        "attempts": [
            asdict(
                PlanAttempt(
                    attempt_id=attempt_id,
                    run_id=run_id,
                    attempt_index=1,
                    trigger="transport_request",
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


def _make_transport_run_id(utterance: str, transport: str) -> str:
    digest = sha256(f"transport:{utc_now_iso()}:{transport}:{utterance}:{len(utterance)}".encode("utf-8")).hexdigest()[:12]
    return f"run_{digest}"


def _make_transport_attempt_id(run_id: str, transport: str) -> str:
    digest = sha256(f"{run_id}:{transport}:1".encode("utf-8")).hexdigest()[:10]
    return f"attempt_1_{digest}"
