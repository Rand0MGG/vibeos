from __future__ import annotations

import argparse
import asyncio
import json
import signal
import sys
import traceback
from dataclasses import asdict
from typing import Any, Callable
from urllib.parse import parse_qs, unquote, urlparse

from pydantic import ValidationError

from .broker import CapabilityBroker
from .core.adapters.contracts import TransportCommandRequestV1
from .core.adapters.http import AsyncHttpServer, HttpRequest, HttpResponse
from .core.adapters.lifecycle import DatabaseLifecycleComponent
from .core.adapters.outbox_repository import OutboxRecord, SqliteOutboxRepository
from .core.application import (
    AsyncSupervisor,
    OutboxDispatcherComponent,
    SupervisorHealth,
    SupervisorNotReady,
    SupervisorStartError,
    TaskSchedulerComponent,
)
from .dbus_service import DBusServiceComponent
from .durable_task_support import after_seconds, now_iso
from .intent import RuleIntentBroker
from .models import CommandRequest, CommandResult, Intent
from .provider_client import model_request_budget


HTTP_DEPRECATION = {
    "Deprecation": "true",
    "Warning": '299 VibeOS "HTTP compatibility is deprecated; use session D-Bus"',
}


def safe_http_command_result(builder: Callable[[], CommandResult], *, request: CommandRequest) -> CommandResult:
    """Project an unexpected application-service failure into the public contract."""
    try:
        return builder()
    except Exception as exc:
        traceback.print_exc()
        detail: dict[str, object] = {
            "error": "daemon_internal_error",
            "transport": "http",
            "exception_type": type(exc).__name__,
        }
        if request.utterance:
            detail["utterance"] = request.utterance
        if request.review_id is not None:
            detail["review_id"] = request.review_id
        return CommandResult(
            status="failed",
            intent=Intent.unknown("daemon command failed"),
            result=detail,
            transport="http",
            message=f"daemon command failed: {exc}",
            execution_status="failed",
            acceptance_status="skipped",
            overall_status="failed",
        )


class DaemonHttpRouter:
    """Deprecated loopback transport over the same durable application service."""

    def __init__(
        self,
        *,
        broker: CapabilityBroker,
        supervisor: AsyncSupervisor,
        status_payload: Callable[[], dict[str, object]],
    ) -> None:
        self._broker = broker
        self._supervisor = supervisor
        self._status_payload = status_payload

    async def handle(self, request: HttpRequest) -> HttpResponse:
        parsed = urlparse(request.target)
        path = parsed.path
        if request.method == "GET" and path == "/v1/status":
            return _json_response(200, self._status_payload())
        if request.method == "GET":
            return await self._handle_get(path, parsed.query)
        if request.method == "POST" and path == "/v1/command":
            return await self._handle_command(request.body)
        if request.method == "POST" and path.startswith("/v1/tasks/") and path.endswith("/control"):
            return await self._handle_task_control(path, request.body)
        return _json_response(404, {"error": "not_found"})

    async def _handle_get(self, path: str, query_string: str) -> HttpResponse:
        try:
            if path == "/v1/apps":
                payload = await self._supervisor.submit(lambda: {"apps": self._broker.list_apps(transport="http")})
            elif path == "/v1/windows":
                payload = await self._supervisor.submit(lambda: {"windows": self._broker.list_windows(transport="http")})
            elif path == "/v1/capabilities":
                payload = await self._supervisor.submit(self._broker.capabilities)
            elif path == "/v1/reviews/pending":
                payload = await self._supervisor.submit(lambda: {"reviews": self._broker.pending_reviews()})
            elif path == "/v1/audit/tail":
                query = parse_qs(query_string)
                try:
                    count = max(0, int(query.get("n", ["20"])[0]))
                except ValueError:
                    count = 20
                payload = await self._supervisor.submit(lambda: {"entries": self._broker.audit.tail(count)})
            elif path == "/v1/tasks":
                query = parse_qs(query_string)
                raw_status = query.get("status", [None])[0]
                try:
                    limit = min(500, max(1, int(query.get("limit", ["100"])[0])))
                except ValueError:
                    limit = 100
                payload = await self._supervisor.submit(lambda: {"tasks": self._broker.tasks(status=raw_status, limit=limit)})
            elif path.startswith("/v1/tasks/"):
                task_id = unquote(path.removeprefix("/v1/tasks/"))
                task = await self._supervisor.submit(lambda: self._broker.task(task_id))
                if task is None:
                    return _json_response(404, {"error": "task_not_found", "task_id": task_id})
                payload = task
            else:
                return _json_response(404, {"error": "not_found"})
        except SupervisorNotReady as exc:
            return _json_response(503, {"error": "daemon_not_ready", "message": str(exc)})
        except ValueError as exc:
            return _json_response(400, {"error": "invalid_request", "message": str(exc)})
        return _json_response(200, payload)

    async def _handle_command(self, body: bytes) -> HttpResponse:
        try:
            raw = json.loads(body.decode("utf-8"))
            contract = TransportCommandRequestV1.model_validate(raw, strict=True)
        except (UnicodeDecodeError, json.JSONDecodeError, ValidationError, TypeError) as exc:
            return _json_response(400, {"error": "invalid_contract", "exception_type": type(exc).__name__})
        command = CommandRequest(
            utterance=contract.utterance.strip(),
            mode=contract.mode,
            dry_run=contract.dry_run,
            approve=contract.approve,
            review_id=contract.review_id,
            supplemental_input=contract.supplemental_input,
            debug=contract.debug,
            transport="http",
        )
        try:
            if contract.reject and contract.review_id is not None:
                result = await self._supervisor.submit(
                    lambda: safe_http_command_result(
                        lambda: self._broker.reject_review(contract.review_id or "", transport="http"),
                        request=command,
                    )
                )
            else:
                result = await self._supervisor.submit(lambda: safe_http_command_result(lambda: self._broker.handle(command), request=command))
        except SupervisorNotReady as exc:
            return _json_response(503, {"error": "daemon_not_ready", "message": str(exc)})
        return _json_response(200, dataclass_to_jsonable(result))

    async def _handle_task_control(self, path: str, body: bytes) -> HttpResponse:
        task_id = unquote(path.removeprefix("/v1/tasks/").removesuffix("/control"))
        try:
            payload = json.loads(body.decode("utf-8"))
            if not isinstance(payload, dict):
                raise TypeError("control payload must be an object")
            allowed = {"schema_version", "operation", "expected_revision", "owner", "reason"}
            if set(payload) - allowed or payload.get("schema_version") != "v1":
                raise ValueError("invalid task control contract")
            operation = str(payload["operation"])
            expected_revision = int(payload["expected_revision"])
            owner = str(payload["owner"]) if payload.get("owner") is not None else None
            reason = str(payload.get("reason") or "")
        except (UnicodeDecodeError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            return _json_response(400, {"error": "invalid_contract", "exception_type": type(exc).__name__})
        try:
            result = await self._supervisor.submit(
                lambda: self._broker.control_task(
                    task_id,
                    operation,
                    expected_revision=expected_revision,
                    owner=owner,
                    reason=reason,
                )
            )
        except SupervisorNotReady as exc:
            return _json_response(503, {"error": "daemon_not_ready", "message": str(exc)})
        except (KeyError, ValueError, RuntimeError) as exc:
            return _json_response(400, {"error": type(exc).__name__, "message": str(exc)})
        return _json_response(200, result)


def dataclass_to_jsonable(value: Any) -> Any:
    if hasattr(value, "__dataclass_fields__"):
        return {key: dataclass_to_jsonable(getattr(value, key)) for key in value.__dataclass_fields__}
    if isinstance(value, (list, tuple)):
        return [dataclass_to_jsonable(item) for item in value]
    if isinstance(value, dict):
        return {key: dataclass_to_jsonable(item) for key, item in value.items()}
    return value


def build_status_payload(
    transports: list[str] | None = None,
    host: str | None = None,
    port: int | None = None,
    *,
    health: SupervisorHealth | None = None,
) -> dict[str, object]:
    lifecycle = health.state.value if health is not None else "starting"
    resolved_transports = transports or ["dbus", "http"]
    payload: dict[str, object] = {
        "status": "ok" if health is not None and health.ready else lifecycle,
        "service": "vibed",
        "transports": resolved_transports,
        "primary_transport": "dbus" if "dbus" in resolved_transports else resolved_transports[0],
        "http_compatibility": "deprecated" if "http" in resolved_transports else "disabled",
        "lifecycle": lifecycle,
        "ready": bool(health.ready) if health is not None else False,
        "accepting_requests": bool(health.accepting_requests) if health is not None else False,
        "active_requests": health.active_requests if health is not None else 0,
        "components": [asdict(component) for component in health.components] if health is not None else [],
    }
    if host is not None:
        payload["host"] = host
    if port is not None:
        payload["port"] = port
    return payload


async def run_daemon(
    broker: CapabilityBroker,
    *,
    host: str = "127.0.0.1",
    port: int = 8765,
    dbus_enabled: bool = True,
    stop_event: asyncio.Event | None = None,
) -> int:
    supervisor = AsyncSupervisor()
    outbox = SqliteOutboxRepository(broker.database)
    outbox_owner = f"vibed:{id(supervisor)}"
    transports = ["dbus", "http"] if dbus_enabled else ["http"]
    http_server: AsyncHttpServer

    def status_payload() -> dict[str, object]:
        return build_status_payload(transports, host=host, port=http_server.port, health=supervisor.health())

    def scan_tasks() -> tuple[str, ...]:
        return broker.task_repository.recoverable(now_iso())

    def resume_task(task_id: str) -> None:
        with model_request_budget():
            broker.task_engine.resume_task(task_id)

    def claim_outbox() -> tuple[object, ...]:
        return outbox.claim(owner=outbox_owner, now=now_iso(), expires_at=after_seconds(30))

    def consume_outbox(record: object) -> None:
        if not isinstance(record, OutboxRecord):
            raise TypeError("outbox dispatcher received an invalid record")
        if record.topic in {
            "task.effect.dispatch_action",
            "task.effect.verify",
            "task.effect.reconcile",
            "task.effect.plan",
            "task.effect.schedule_timer",
            "task.effect.cancel_action",
        }:
            resume_task(record.aggregate_id)
        outbox.delivered(record.message_id, "vibed-core", now_iso(), json.dumps({"topic": record.topic}, separators=(",", ":")))

    supervisor.add_component(DatabaseLifecycleComponent(broker.database))
    supervisor.add_component(TaskSchedulerComponent(scan=scan_tasks, resume=resume_task))
    supervisor.add_component(OutboxDispatcherComponent(claim=claim_outbox, consume=consume_outbox))
    router = DaemonHttpRouter(broker=broker, supervisor=supervisor, status_payload=status_payload)
    http_server = AsyncHttpServer(host, port, router.handle)
    supervisor.add_component(http_server)
    if dbus_enabled:
        supervisor.add_component(DBusServiceComponent(broker=broker, supervisor=supervisor, status_payload=status_payload))
    resolved_stop_event = stop_event or asyncio.Event()
    _install_signal_handlers(resolved_stop_event)
    try:
        await supervisor.start()
    except SupervisorStartError as exc:
        print(f"vibed startup failed: {exc}", file=sys.stderr)
        return 2
    primary = "D-Bus" if dbus_enabled else "HTTP compatibility"
    print(f"vibed ready; primary control plane: {primary}; deprecated HTTP endpoint {host}:{http_server.port}")
    try:
        await resolved_stop_event.wait()
        await supervisor.drain()
        return 0
    finally:
        await supervisor.stop()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="vibed", description="VibeOS durable task daemon")
    parser.add_argument("--host", default="127.0.0.1", help="loopback host for the deprecated HTTP compatibility adapter")
    parser.add_argument("--port", type=int, default=8765, help="port for the deprecated HTTP compatibility adapter")
    parser.add_argument("--dbus", action="store_true", help="deprecated no-op; D-Bus is enabled by default")
    parser.add_argument("--no-dbus", action="store_true", help="disable D-Bus for isolated compatibility testing")
    parser.add_argument("--offline", action="store_true", help="use the deterministic local intent broker without model calls")
    args = parser.parse_args(argv)
    try:
        broker = CapabilityBroker(intent_broker=RuleIntentBroker() if args.offline else None)
        return asyncio.run(run_daemon(broker, host=args.host, port=args.port, dbus_enabled=not args.no_dbus))
    except KeyboardInterrupt:
        return 130
    except Exception as exc:
        traceback.print_exc()
        print(f"vibed failed before readiness: {exc}", file=sys.stderr)
        return 2


def _install_signal_handlers(stop_event: asyncio.Event) -> None:
    loop = asyncio.get_running_loop()
    for signum in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(signum, stop_event.set)
        except (NotImplementedError, RuntimeError):
            signal.signal(signum, lambda _signum, _frame: loop.call_soon_threadsafe(stop_event.set))


def _json_response(status: int, payload: object) -> HttpResponse:
    return HttpResponse(
        status=status,
        body=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers=HTTP_DEPRECATION,
    )


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
