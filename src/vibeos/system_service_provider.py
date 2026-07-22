from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import os
import re
import subprocess
from typing import Literal, Protocol

from .system_service_contracts import (
    FIXTURE_UNIT,
    ServiceFactsV2,
    ServiceJournalFactV2,
    ServiceProcessFactV2,
    SystemServiceActionSpecV2,
    SystemServiceAdapterResultV2,
)


SYSTEMCTL = "/usr/bin/systemctl"
JOURNALCTL = "/usr/bin/journalctl"
SYNTHETIC_FAILURE_MARKER = "VIBEOS_GOAL04_SYNTHETIC_FAILURE_V1"


class ServiceProviderError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class DbusServiceSnapshot:
    load_state: str
    active_state: str
    sub_state: str
    result: str
    restart_count: int
    main_pid: int
    exit_code: int | None
    exit_status: int | None


class SystemdDbusClientPort(Protocol):
    def observe(self) -> DbusServiceSnapshot: ...

    def dispatch(self, operation: str) -> str: ...


class CommandRunner(Protocol):
    def __call__(self, argv: list[str], timeout: float) -> subprocess.CompletedProcess[str]: ...


def _run_command(argv: list[str], timeout: float) -> subprocess.CompletedProcess[str]:
    return subprocess.run(argv, capture_output=True, text=True, timeout=timeout, check=False)


class SystemdUserDbusClient:
    """Narrow systemd user-manager D-Bus client for one compile-time unit."""

    def observe(self) -> DbusServiceSnapshot:
        return asyncio.run(self._observe())

    def dispatch(self, operation: str) -> str:
        if operation not in {"start", "restart"}:
            raise ServiceProviderError("unsupported_request", "only start or restart is allowed")
        return asyncio.run(self._dispatch(operation))

    async def _observe(self) -> DbusServiceSnapshot:
        try:
            from dbus_next import BusType, DBusError, Variant
            from dbus_next.aio import MessageBus
        except ImportError as exc:
            raise ServiceProviderError("dbus_unavailable", "dbus-next is unavailable") from exc
        del Variant
        try:
            bus = await MessageBus(bus_type=BusType.SESSION).connect()
            manager_intro = await bus.introspect("org.freedesktop.systemd1", "/org/freedesktop/systemd1")
            manager_obj = bus.get_proxy_object("org.freedesktop.systemd1", "/org/freedesktop/systemd1", manager_intro)
            manager = manager_obj.get_interface("org.freedesktop.systemd1.Manager")
            unit_path = await manager.call_get_unit(FIXTURE_UNIT)
            unit_intro = await bus.introspect("org.freedesktop.systemd1", unit_path)
            unit_obj = bus.get_proxy_object("org.freedesktop.systemd1", unit_path, unit_intro)
            properties = unit_obj.get_interface("org.freedesktop.DBus.Properties")
            unit_values = await properties.call_get_all("org.freedesktop.systemd1.Unit")
            service_values = await properties.call_get_all("org.freedesktop.systemd1.Service")
            return DbusServiceSnapshot(
                load_state=str(unit_values["LoadState"].value),
                active_state=str(unit_values["ActiveState"].value),
                sub_state=str(unit_values["SubState"].value),
                result=str(service_values.get("Result").value if service_values.get("Result") is not None else ""),
                restart_count=int(service_values.get("NRestarts").value if service_values.get("NRestarts") is not None else 0),
                main_pid=int(service_values.get("MainPID").value if service_values.get("MainPID") is not None else 0),
                exit_code=_optional_int(service_values, "ExecMainCode"),
                exit_status=_optional_int(service_values, "ExecMainStatus"),
            )
        except DBusError as exc:
            name = str(getattr(exc, "type", ""))
            if "NoSuchUnit" in name:
                return DbusServiceSnapshot("not-found", "inactive", "dead", "not-found", 0, 0, None, None)
            if "AccessDenied" in name:
                raise ServiceProviderError("permission_denied", "systemd user D-Bus denied access") from exc
            raise ServiceProviderError("dbus_unavailable", "systemd user D-Bus observation failed") from exc
        except (KeyError, OSError, RuntimeError) as exc:
            raise ServiceProviderError("dbus_unavailable", "systemd user D-Bus observation failed") from exc
        finally:
            if "bus" in locals():
                bus.disconnect()

    async def _dispatch(self, operation: str) -> str:
        try:
            from dbus_next import BusType, DBusError
            from dbus_next.aio import MessageBus
        except ImportError as exc:
            raise ServiceProviderError("dbus_unavailable", "dbus-next is unavailable") from exc
        try:
            bus = await MessageBus(bus_type=BusType.SESSION).connect()
            intro = await bus.introspect("org.freedesktop.systemd1", "/org/freedesktop/systemd1")
            obj = bus.get_proxy_object("org.freedesktop.systemd1", "/org/freedesktop/systemd1", intro)
            manager = obj.get_interface("org.freedesktop.systemd1.Manager")
            job_path = await (manager.call_start_unit(FIXTURE_UNIT, "replace") if operation == "start" else manager.call_restart_unit(FIXTURE_UNIT, "replace"))
            return str(job_path)
        except DBusError as exc:
            name = str(getattr(exc, "type", ""))
            code = "permission_denied" if "AccessDenied" in name else "unit_not_found" if "NoSuchUnit" in name else "dispatch_failed"
            raise ServiceProviderError(code, f"systemd user D-Bus {operation} failed") from exc
        except (OSError, RuntimeError) as exc:
            raise ServiceProviderError("dbus_unavailable", "systemd user D-Bus dispatch failed") from exc
        finally:
            if "bus" in locals():
                bus.disconnect()


class SystemdUserServiceProvider:
    """Typed fact/action provider for the one Goal04 user-service fixture."""

    def __init__(
        self,
        *,
        dbus_client: SystemdDbusClientPort | None = None,
        command_runner: CommandRunner | None = None,
        allow_fixed_argv_fallback: bool = False,
    ) -> None:
        self.dbus = dbus_client or SystemdUserDbusClient()
        self.command_runner = command_runner or _run_command
        self.allow_fixed_argv_fallback = allow_fixed_argv_fallback

    def observe(self, *, include_journal: bool = True, journal_window_seconds: int = 120) -> ServiceFactsV2:
        captured = datetime.now(timezone.utc)
        source: Literal["systemd_user_dbus", "fixed_systemctl_argv"] = "systemd_user_dbus"
        try:
            snapshot = self.dbus.observe()
        except ServiceProviderError as exc:
            if not self.allow_fixed_argv_fallback or exc.code != "dbus_unavailable":
                raise
            snapshot = self._observe_fixed_argv()
            source = "fixed_systemctl_argv"
        journal = self._journal(captured, journal_window_seconds) if include_journal and snapshot.load_state == "loaded" else None
        return ServiceFactsV2(
            load_state=_load_state(snapshot.load_state),
            active_state=_active_state(snapshot.active_state),
            sub_state=snapshot.sub_state or "unknown",
            result=snapshot.result,
            restart_count=snapshot.restart_count,
            process=ServiceProcessFactV2(
                main_pid=snapshot.main_pid,
                running=snapshot.main_pid > 0 and snapshot.active_state == "active",
                exit_code=snapshot.exit_code,
                exit_status=snapshot.exit_status,
            ),
            journal=journal,
            source=source,
            captured_at=captured.isoformat(),
            ttl_seconds=30,
            sensitivity="D0",
            evidence_reference=f"systemd-user://{FIXTURE_UNIT}/{int(captured.timestamp())}",
        )

    def execute(self, spec: SystemServiceActionSpecV2) -> SystemServiceAdapterResultV2:
        try:
            job = self.dbus.dispatch(spec.operation)
            return SystemServiceAdapterResultV2(
                operation=spec.operation,
                status="succeeded",
                adapter="systemd_user_dbus",
                adapter_status="job-dispatched",
                external_reference=job,
            )
        except ServiceProviderError as exc:
            if self.allow_fixed_argv_fallback and exc.code == "dbus_unavailable":
                return self._execute_fixed_argv(spec)
            return SystemServiceAdapterResultV2(
                operation=spec.operation,
                status="failed",
                adapter="systemd_user_dbus",
                adapter_status="dispatch-failed",
                error_code=exc.code,
                error=str(exc),
            )

    def _observe_fixed_argv(self) -> DbusServiceSnapshot:
        argv = [
            SYSTEMCTL,
            "--user",
            "show",
            FIXTURE_UNIT,
            "--no-pager",
            "--property=LoadState,ActiveState,SubState,Result,NRestarts,MainPID,ExecMainCode,ExecMainStatus",
        ]
        result = self._command(argv, timeout=10)
        if result.returncode != 0:
            error = (result.stderr or result.stdout).lower()
            if "not found" in error or "could not be found" in error:
                return DbusServiceSnapshot("not-found", "inactive", "dead", "not-found", 0, 0, None, None)
            if "access denied" in error or "permission denied" in error:
                raise ServiceProviderError("permission_denied", "fixed systemctl observation was denied")
            raise ServiceProviderError("environment_unreachable", "fixed systemctl observation failed")
        fields = _parse_properties(result.stdout)
        return DbusServiceSnapshot(
            fields.get("LoadState", "error"),
            fields.get("ActiveState", "unknown"),
            fields.get("SubState", "unknown"),
            fields.get("Result", ""),
            _integer(fields.get("NRestarts")),
            _integer(fields.get("MainPID")),
            _optional_integer(fields.get("ExecMainCode")),
            _optional_integer(fields.get("ExecMainStatus")),
        )

    def _execute_fixed_argv(self, spec: SystemServiceActionSpecV2) -> SystemServiceAdapterResultV2:
        argv = [SYSTEMCTL, "--user", spec.operation, FIXTURE_UNIT]
        result = self._command(argv, timeout=float(spec.timeout_seconds))
        status: Literal["succeeded", "failed"] = "succeeded" if result.returncode == 0 else "failed"
        error = None if status == "succeeded" else "fixed systemctl action failed"
        return SystemServiceAdapterResultV2(
            operation=spec.operation,
            status=status,
            adapter="fixed_systemctl_argv",
            adapter_status=f"exit-{result.returncode}",
            error_code=None if status == "succeeded" else "dispatch_failed",
            error=error,
        )

    def _journal(self, captured: datetime, window_seconds: int) -> ServiceJournalFactV2 | None:
        since = captured - timedelta(seconds=max(1, min(window_seconds, 300)))
        argv = [
            JOURNALCTL,
            "--user",
            "-u",
            FIXTURE_UNIT,
            "--since",
            since.isoformat(),
            "--until",
            captured.isoformat(),
            "--no-pager",
            "-n",
            "40",
            "-o",
            "cat",
        ]
        try:
            result = self._command(argv, timeout=10)
        except ServiceProviderError:
            return None
        if result.returncode != 0:
            return None
        raw_lines = result.stdout.splitlines()
        lines = tuple(_redact_journal_line(line) for line in raw_lines[-40:] if line.strip())
        return ServiceJournalFactV2(
            since=since.isoformat(),
            until=captured.isoformat(),
            lines=lines,
            truncated=len(raw_lines) > 40,
            redacted=True,
        )

    def _command(self, argv: list[str], *, timeout: float) -> subprocess.CompletedProcess[str]:
        if not argv or argv[0] not in {SYSTEMCTL, JOURNALCTL} or not os.path.isabs(argv[0]):
            raise ServiceProviderError("unsupported_request", "only fixed absolute systemd helper argv is allowed")
        if FIXTURE_UNIT not in argv:
            raise ServiceProviderError("unsupported_request", "systemd helper argv must bind the fixed fixture")
        try:
            return self.command_runner(argv, timeout)
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError) as exc:
            raise ServiceProviderError("environment_unreachable", "fixed systemd helper failed") from exc


def _optional_int(values: dict[str, object], key: str) -> int | None:
    variant = values.get(key)
    return int(variant.value) if variant is not None else None  # type: ignore[attr-defined]


def _parse_properties(output: str) -> dict[str, str]:
    return {key: value for line in output.splitlines() if "=" in line for key, value in (line.split("=", 1),)}


def _load_state(value: str) -> Literal["loaded", "not-found", "error"]:
    if value == "loaded":
        return "loaded"
    if value == "not-found":
        return "not-found"
    return "error"


def _active_state(value: str) -> Literal["active", "inactive", "failed", "activating", "deactivating", "unknown"]:
    if value == "active":
        return "active"
    if value == "inactive":
        return "inactive"
    if value == "failed":
        return "failed"
    if value == "activating":
        return "activating"
    if value == "deactivating":
        return "deactivating"
    return "unknown"


def _integer(value: str | None) -> int:
    try:
        return int(value or 0)
    except ValueError:
        return 0


def _optional_integer(value: str | None) -> int | None:
    return _integer(value) if value not in {None, ""} else None


_SECRET_PATTERN = re.compile(r"(?i)(bearer\s+\S+|sk-[a-z0-9_-]{8,}|(?:api[_-]?key|token|password|secret)\s*[=:]\s*\S+)")
_HOME_PATTERN = re.compile(r"/home/[^/\s]+")


def _redact_journal_line(line: str) -> str:
    bounded = line[:500]
    bounded = _SECRET_PATTERN.sub("[REDACTED]", bounded)
    return _HOME_PATTERN.sub("/home/[USER]", bounded)
