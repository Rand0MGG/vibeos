from __future__ import annotations

import argparse
import asyncio
import json
import signal
import sys
import traceback
from dataclasses import asdict
from typing import Any, Callable
from urllib.parse import parse_qs, urlparse

from pydantic import ValidationError

from .broker import CapabilityBroker
from .core.adapters.contracts import TransportCommandRequestV1
from .core.adapters.http import AsyncHttpServer, HttpRequest, HttpResponse
from .core.adapters.lifecycle import DatabaseLifecycleComponent
from .core.application import AsyncSupervisor, SupervisorHealth, SupervisorNotReady, SupervisorStartError
from .dbus_service import DBusServiceComponent
from .intent import RuleIntentBroker
from .models import CommandRequest, CommandResult, Intent


def safe_http_command_result(builder: Callable[[], CommandResult], *, request: CommandRequest) -> CommandResult:
    """Turn an unexpected broker exception into the HTTP result contract."""

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
        return _json_response(404, {"error": "not_found"})

    async def _handle_get(self, path: str, query_string: str) -> HttpResponse:
        try:
            if path == "/v1/apps":
                payload = await self._supervisor.submit(lambda: {"apps": [asdict(app) for app in self._broker.apps.list_apps()]})
            elif path == "/v1/windows":
                payload = await self._supervisor.submit(lambda: {"windows": [asdict(window) for window in self._broker.windows.list_windows()]})
            elif path == "/v1/capabilities":
                payload = await self._supervisor.submit(self._broker.capabilities)
            elif path == "/v1/reviews/pending":
                payload = await self._supervisor.submit(lambda: {"reviews": self._broker.pending_reviews()})
            elif path == "/v1/audit/tail":
                query = parse_qs(query_string)
                raw_count = query.get("n", ["20"])[0]
                try:
                    count = max(0, int(raw_count))
                except ValueError:
                    count = 20
                payload = await self._supervisor.submit(lambda: {"entries": self._broker.audit.tail(count)})
            else:
                return _json_response(404, {"error": "not_found"})
        except SupervisorNotReady as exc:
            return _json_response(503, {"error": "daemon_not_ready", "message": str(exc)})
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
                    lambda: safe_http_command_result(lambda: self._broker.reject_review(contract.review_id or "", transport="http"), request=command)
                )
            else:
                result = await self._supervisor.submit(lambda: safe_http_command_result(lambda: self._broker.handle(command), request=command))
        except SupervisorNotReady as exc:
            return _json_response(503, {"error": "daemon_not_ready", "message": str(exc)})
        return _json_response(200, dataclass_to_jsonable(result))


def dataclass_to_jsonable(value: Any) -> Any:
    if hasattr(value, "__dataclass_fields__"):
        return {key: dataclass_to_jsonable(getattr(value, key)) for key in value.__dataclass_fields__}
    if isinstance(value, (list, tuple)):
        return [dataclass_to_jsonable(item) for item in value]
    if isinstance(value, dict):
        return {key: dataclass_to_jsonable(item) for key, item in value.items()}
    return value


def build_status_payload(
    transports: list[str],
    host: str | None = None,
    port: int | None = None,
    *,
    health: SupervisorHealth | None = None,
) -> dict[str, object]:
    lifecycle = health.state.value if health is not None else "starting"
    payload: dict[str, object] = {
        "status": "ok" if health is not None and health.ready else lifecycle,
        "service": "vibed",
        "transports": transports,
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
    host: str,
    port: int,
    dbus_enabled: bool,
    stop_event: asyncio.Event | None = None,
) -> int:
    supervisor = AsyncSupervisor()
    transports = ["http", "dbus"] if dbus_enabled else ["http"]
    http_server: AsyncHttpServer

    def status_payload() -> dict[str, object]:
        return build_status_payload(transports, host=host, port=http_server.port, health=supervisor.health())

    router = DaemonHttpRouter(broker=broker, supervisor=supervisor, status_payload=status_payload)
    http_server = AsyncHttpServer(host, port, router.handle)
    supervisor.add_component(
        DatabaseLifecycleComponent(
            broker.database,
            after_ready=broker.reviews.reconnect_after_database_ready,
        )
    )
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
    print(f"vibed ready on {', '.join(transports)}; HTTP compatibility endpoint {host}:{http_server.port}")
    try:
        await resolved_stop_event.wait()
        await supervisor.drain()
        return 0
    finally:
        await supervisor.stop()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="vibed", description="VibeOS daemon")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--dbus", action="store_true", help="serve D-Bus and the thin HTTP compatibility adapter")
    parser.add_argument("--offline", action="store_true", help="use the deterministic local intent broker without model calls")
    args = parser.parse_args(argv)
    try:
        broker = CapabilityBroker(intent_broker=RuleIntentBroker() if args.offline else None)
        return asyncio.run(run_daemon(broker, host=args.host, port=args.port, dbus_enabled=args.dbus))
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
    return HttpResponse(status=status, body=json.dumps(payload, ensure_ascii=False).encode("utf-8"))


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
