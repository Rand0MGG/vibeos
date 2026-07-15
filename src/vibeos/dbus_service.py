from __future__ import annotations

import json
import traceback
from dataclasses import asdict
from typing import Callable

from pydantic import ValidationError

from .broker import CapabilityBroker
from .core.adapters.contracts import TransportCommandRequestV1
from .core.application import AsyncSupervisor, SupervisorNotReady
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
        return serialize_command_result(_daemon_failure(exc, transport="dbus", request=request))


class DBusServiceComponent:
    name = "dbus"

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
        self._bus: object | None = None
        self._status = "stopped"
        self._message = "D-Bus adapter is stopped"

    async def start(self) -> None:
        try:
            from dbus_next.aio import MessageBus
            from dbus_next.service import ServiceInterface, method
        except ImportError as exc:
            self._status = "failed"
            self._message = "dbus-next is required for D-Bus service mode"
            raise RuntimeError(self._message) from exc

        owner = self

        class AgentInterface(ServiceInterface):
            def __init__(self) -> None:
                super().__init__("org.vibeos.Agent")

            @method()
            async def Command(self, text: "s") -> "s":
                request = CommandRequest(text, transport="dbus")
                return await owner._execute(request)

            @method()
            async def CommandRequest(self, payload_json: "s") -> "s":
                try:
                    raw = json.loads(payload_json)
                    contract = TransportCommandRequestV1.model_validate(raw, strict=True)
                except (json.JSONDecodeError, ValidationError, TypeError) as exc:
                    return serialize_command_result(_invalid_contract("dbus", exc))
                request = _request_from_contract(contract, transport="dbus")
                if contract.reject and contract.review_id is not None:
                    return await owner._reject(contract.review_id, request)
                return await owner._execute(request)

            @method()
            async def AppsList(self) -> "s":
                return await owner._read(lambda: json.dumps([asdict(app) for app in owner._broker.apps.list_apps()], ensure_ascii=False))

            @method()
            async def WindowsList(self) -> "s":
                return await owner._read(lambda: json.dumps([asdict(window) for window in owner._broker.windows.list_windows()], ensure_ascii=False))

            @method()
            async def ApproveReview(self, review_id: "s") -> "s":
                request = CommandRequest("", review_id=review_id, approve=True, transport="dbus")
                return await owner._execute(request)

            @method()
            async def RejectReview(self, review_id: "s") -> "s":
                request = CommandRequest("", review_id=review_id, transport="dbus")
                return await owner._reject(review_id, request)

            @method()
            async def Capabilities(self) -> "s":
                return await owner._read(lambda: json.dumps(owner._broker.capabilities(), ensure_ascii=False))

            @method()
            async def PendingReviews(self) -> "s":
                return await owner._read(lambda: json.dumps(owner._broker.pending_reviews(), ensure_ascii=False))

            @method()
            async def AuditTail(self, count: "i") -> "s":
                return await owner._read(lambda: json.dumps(owner._broker.audit.tail(max(0, int(count))), ensure_ascii=False))

            @method()
            def Status(self) -> "s":
                return json.dumps(owner._status_payload(), ensure_ascii=False)

        bus = await MessageBus().connect()
        await bus.request_name("org.vibeos.Agent")
        bus.export("/org/vibeos/Agent", AgentInterface())
        self._bus = bus
        self._status = "ready"
        self._message = "org.vibeos.Agent exported on the supervisor event loop"

    async def stop(self) -> None:
        bus, self._bus = self._bus, None
        if bus is not None:
            disconnect = getattr(bus, "disconnect", None)
            if callable(disconnect):
                disconnect()
        self._status = "stopped"
        self._message = "D-Bus adapter is stopped"

    def health_status(self) -> tuple[str, str]:
        return self._status, self._message

    async def _execute(self, request: CommandRequest) -> str:
        try:
            return await self._supervisor.submit(lambda: safe_command_result(lambda: self._broker.handle(request), request=request))
        except SupervisorNotReady as exc:
            return serialize_command_result(_not_ready("dbus", request, exc))

    async def _reject(self, review_id: str, request: CommandRequest) -> str:
        try:
            return await self._supervisor.submit(lambda: safe_command_result(lambda: self._broker.reject_review(review_id, transport="dbus"), request=request))
        except SupervisorNotReady as exc:
            return serialize_command_result(_not_ready("dbus", request, exc))

    async def _read(self, builder: Callable[[], str]) -> str:
        try:
            return await self._supervisor.submit(builder)
        except SupervisorNotReady:
            return json.dumps({"error": "daemon_not_ready"}, ensure_ascii=False)


def _request_from_contract(contract: TransportCommandRequestV1, *, transport: str) -> CommandRequest:
    return CommandRequest(
        utterance=contract.utterance.strip(),
        mode=contract.mode,
        dry_run=contract.dry_run,
        approve=contract.approve,
        review_id=contract.review_id,
        supplemental_input=contract.supplemental_input,
        transport=transport,
        debug=contract.debug,
    )


def _invalid_contract(transport: str, exc: Exception) -> CommandResult:
    return CommandResult(
        status="failed",
        intent=Intent.unknown("transport request failed strict validation"),
        result={"error": "invalid_contract", "transport": transport, "exception_type": type(exc).__name__},
        transport=transport,
        message="transport request failed strict validation",
        execution_status="not_started",
        acceptance_status="skipped",
        overall_status="failed",
    )


def _not_ready(transport: str, request: CommandRequest, exc: Exception) -> CommandResult:
    return CommandResult(
        status="failed",
        intent=Intent.unknown("daemon is not ready"),
        result={"error": "daemon_not_ready", "transport": transport, "review_id": request.review_id},
        transport=transport,
        message=str(exc),
        execution_status="not_started",
        acceptance_status="skipped",
        overall_status="blocked",
    )


def _daemon_failure(exc: Exception, *, transport: str, request: CommandRequest | None) -> CommandResult:
    detail: dict[str, object] = {
        "error": "daemon_internal_error",
        "transport": transport,
        "exception_type": type(exc).__name__,
    }
    if request is not None:
        if request.utterance:
            detail["utterance"] = request.utterance
        if request.review_id is not None:
            detail["review_id"] = request.review_id
    return CommandResult(
        status="failed",
        intent=Intent.unknown("daemon command failed"),
        result=detail,
        transport=transport,
        message=f"daemon command failed: {exc}",
        execution_status="failed",
        acceptance_status="skipped",
        overall_status="failed",
    )
