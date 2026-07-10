import asyncio
import json
import traceback
from dataclasses import asdict
from typing import Callable

from .broker import CapabilityBroker
from .models import CommandRequest, CommandResult, Intent


def serialize_command_result(result: CommandResult) -> str:
    return json.dumps(asdict(result), ensure_ascii=False)


def safe_command_result(
    builder: Callable[[], CommandResult],
    *,
    request: CommandRequest | None = None,
) -> str:
    try:
        return serialize_command_result(builder())
    except Exception as exc:
        traceback.print_exc()
        message = f"daemon command failed: {exc}"
        detail: dict[str, object] = {
            "error": "daemon_internal_error",
            "transport": "dbus",
            "exception_type": type(exc).__name__,
        }
        if request is not None:
            if request.utterance:
                detail["utterance"] = request.utterance
            if request.review_id is not None:
                detail["review_id"] = request.review_id
        return serialize_command_result(
            CommandResult(
                status="failed",
                intent=Intent.unknown("daemon command failed"),
                result=detail,
                transport="dbus",
                message=message,
                execution_status="failed",
                acceptance_status="skipped",
                overall_status="failed",
            )
        )


def run_dbus_service(
    broker: CapabilityBroker,
    status_payload: dict[str, object] | None = None,
    register_stop_callback: Callable[[Callable[[], None]], None] | None = None,
) -> int:
    try:
        from dbus_next.aio import MessageBus
        from dbus_next.service import ServiceInterface, method
    except ImportError:
        print("dbus-next is required for D-Bus service mode")
        return 2

    class AgentInterface(ServiceInterface):
        def __init__(self) -> None:
            super().__init__("org.vibeos.Agent")

        @method()
        def Command(self, text: "s") -> "s":
            request = CommandRequest(text, transport="dbus")
            return safe_command_result(lambda: broker.handle(request), request=request)

        @method()
        def CommandRequest(self, payload_json: "s") -> "s":
            try:
                payload = json.loads(payload_json)
            except json.JSONDecodeError:
                payload = {}
            utterance = str(payload.get("utterance", "")).strip()
            review_id = payload.get("review_id")
            supplemental_input = payload.get("supplemental_input")
            if review_id and payload.get("reject"):
                request = CommandRequest("", review_id=str(review_id), transport="dbus")
                return safe_command_result(lambda: broker.reject_review(str(review_id), transport="dbus"), request=request)
            else:
                request = CommandRequest(
                    utterance=utterance,
                    mode=str(payload.get("mode", "auto_low_risk")),
                    dry_run=bool(payload.get("dry_run", False)),
                    approve=bool(payload.get("approve", False)),
                    review_id=str(review_id) if review_id is not None else None,
                    supplemental_input=str(supplemental_input) if supplemental_input is not None else None,
                    debug=bool(payload.get("debug", False)),
                    transport="dbus",
                )
                return safe_command_result(lambda: broker.handle(request), request=request)

        @method()
        def AppsList(self) -> "s":
            return json.dumps([asdict(app) for app in broker.apps.list_apps()], ensure_ascii=False)

        @method()
        def WindowsList(self) -> "s":
            return json.dumps([asdict(window) for window in broker.windows.list_windows()], ensure_ascii=False)

        @method()
        def ApproveReview(self, review_id: "s") -> "s":
            request = CommandRequest("", review_id=review_id, approve=True, transport="dbus")
            return safe_command_result(lambda: broker.handle(request), request=request)

        @method()
        def RejectReview(self, review_id: "s") -> "s":
            request = CommandRequest("", review_id=review_id, transport="dbus")
            return safe_command_result(lambda: broker.reject_review(review_id, transport="dbus"), request=request)

        @method()
        def Capabilities(self) -> "s":
            return json.dumps(broker.capabilities(), ensure_ascii=False)

        @method()
        def PendingReviews(self) -> "s":
            return json.dumps(broker.pending_reviews(), ensure_ascii=False)

        @method()
        def AuditTail(self, count: "i") -> "s":
            return json.dumps(broker.audit.tail(max(0, int(count))), ensure_ascii=False)

        @method()
        def Status(self) -> "s":
            return json.dumps(status_payload or {"status": "ok", "service": "vibed", "transports": ["dbus"]}, ensure_ascii=False)

    async def serve(stop_event: asyncio.Event) -> None:
        bus = await MessageBus().connect()
        await bus.request_name("org.vibeos.Agent")
        bus.export("/org/vibeos/Agent", AgentInterface())
        print("vibed D-Bus service ready: org.vibeos.Agent")
        await stop_event.wait()

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    stop_event = asyncio.Event()
    if register_stop_callback is not None:
        register_stop_callback(lambda: loop.call_soon_threadsafe(stop_event.set))
    try:
        loop.run_until_complete(serve(stop_event))
    finally:
        loop.close()
    return 0
