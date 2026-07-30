from __future__ import annotations

import asyncio
from dataclasses import dataclass
import json
import os
from pathlib import Path
import subprocess
from typing import Any, Protocol

from .contracts import ProviderRoute, SecretRef


SECRET_TOOL = "/usr/bin/secret-tool"


class SecretStoreError(RuntimeError):
    pass


class SecretStoreLocked(SecretStoreError):
    pass


class SecretNotFound(SecretStoreError):
    pass


class SecretStore(Protocol):
    def store(self, ref: SecretRef, secret: str) -> None: ...

    def resolve(self, ref: SecretRef) -> str: ...

    def delete(self, ref: SecretRef) -> bool: ...


class SecretStatusReader(Protocol):
    def status(self, ref: SecretRef) -> "SecretStatus": ...


@dataclass(frozen=True)
class SecretStatus:
    secret_ref_uri: str
    status: str


class SecretServiceStatusStore:
    """Reads Secret Service item metadata without requesting secret values."""

    def status(self, ref: SecretRef) -> SecretStatus:
        try:
            return asyncio.run(self._status(ref))
        except SecretStoreError:
            raise
        except Exception as exc:
            raise SecretStoreError("Secret Service metadata query failed") from exc

    async def _status(self, ref: SecretRef) -> SecretStatus:
        try:
            from dbus_next import BusType, Message, MessageType
            from dbus_next.aio import MessageBus
        except ImportError as exc:
            raise SecretStoreError("Secret Service D-Bus client is unavailable") from exc

        bus: Any = None
        try:
            bus = await MessageBus(bus_type=BusType.SESSION).connect()
            reply = await bus.call(
                Message(
                    destination="org.freedesktop.secrets",
                    path="/org/freedesktop/secrets",
                    interface="org.freedesktop.Secret.Service",
                    member="SearchItems",
                    signature="a{ss}",
                    body=[{"application": "vibeos", "secret-ref": ref.uri}],
                )
            )
            if reply.message_type is MessageType.ERROR:
                raise SecretStoreError("Secret Service metadata query failed")
            if len(reply.body) != 2 or not all(isinstance(items, list) for items in reply.body):
                raise SecretStoreError("Secret Service returned an invalid metadata response")
            unlocked, locked = reply.body
            if unlocked:
                return SecretStatus(ref.uri, "available")
            if locked:
                return SecretStatus(ref.uri, "locked")
            return SecretStatus(ref.uri, "missing")
        finally:
            if bus is not None:
                bus.disconnect()


class SecretToolSecretStore:
    """Narrow Secret Service adapter. Secret values use stdin/stdout, never argv."""

    def __init__(self, executable: str = SECRET_TOOL, timeout_seconds: float = 15.0) -> None:
        if not os.path.isabs(executable):
            raise ValueError("secret-tool executable must be an absolute path")
        self.executable = executable
        self.timeout_seconds = timeout_seconds

    def store(self, ref: SecretRef, secret: str) -> None:
        if not secret:
            raise ValueError("secret must not be empty")
        result = self._run(
            [self.executable, "store", f"--label=VibeOS provider {ref.secret_id}", "application", "vibeos", "secret-ref", ref.uri],
            input_text=secret,
        )
        self._raise_for_result(result, missing_ok=False)

    def resolve(self, ref: SecretRef) -> str:
        result = self._run([self.executable, "lookup", "application", "vibeos", "secret-ref", ref.uri])
        self._raise_for_result(result, missing_ok=False)
        value = result.stdout.rstrip("\r\n")
        if not value:
            raise SecretNotFound("secret reference is not present")
        return value

    def delete(self, ref: SecretRef) -> bool:
        result = self._run([self.executable, "clear", "application", "vibeos", "secret-ref", ref.uri])
        if result.returncode == 0:
            return True
        message = (result.stderr or "").lower()
        if self._is_locked(message):
            raise SecretStoreLocked("Secret Service is locked")
        if "not found" in message or "no matching" in message:
            return False
        raise SecretStoreError("Secret Service operation failed")

    def _run(self, argv: list[str], *, input_text: str | None = None) -> subprocess.CompletedProcess[str]:
        try:
            return subprocess.run(
                argv,
                input=input_text,
                capture_output=True,
                text=True,
                timeout=self.timeout_seconds,
                check=False,
            )
        except FileNotFoundError as exc:
            raise SecretStoreError("freedesktop secret-tool is unavailable") from exc
        except subprocess.TimeoutExpired as exc:
            raise SecretStoreLocked("Secret Service did not respond; the session keyring may be locked") from exc

    def _raise_for_result(self, result: subprocess.CompletedProcess[str], *, missing_ok: bool) -> None:
        if result.returncode == 0:
            return
        message = (result.stderr or "").lower()
        if self._is_locked(message):
            raise SecretStoreLocked("Secret Service is locked")
        if missing_ok or "not found" in message or "no matching" in message:
            raise SecretNotFound("secret reference is not present")
        raise SecretStoreError("Secret Service operation failed")

    @staticmethod
    def _is_locked(message: str) -> bool:
        return any(fragment in message for fragment in ("locked", "is locked", "prompt dismissed", "no such secret collection"))


class ProviderRouteRepository:
    """Persists only provider metadata and opaque SecretRef contracts."""

    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path) if path is not None else _default_route_path()

    def save(self, route: ProviderRoute) -> None:
        routes = self._read()
        routes[route.route_id] = route.model_dump(mode="json")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps({"schema_version": "v1", "routes": routes}, indent=2, sort_keys=True), encoding="utf-8")
        try:
            os.chmod(temporary, 0o600)
        except OSError:
            pass
        temporary.replace(self.path)

    def get(self, route_id: str) -> ProviderRoute | None:
        payload = self._read().get(route_id)
        return ProviderRoute.model_validate(payload) if payload is not None else None

    def list_routes(self) -> tuple[ProviderRoute, ...]:
        return tuple(ProviderRoute.model_validate(payload) for _, payload in sorted(self._read().items()))

    def delete(self, route_id: str) -> bool:
        routes = self._read()
        removed = routes.pop(route_id, None) is not None
        if removed:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps({"schema_version": "v1", "routes": routes}, indent=2, sort_keys=True), encoding="utf-8")
        return removed

    def _read(self) -> dict[str, object]:
        if not self.path.exists():
            return {}
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict) or payload.get("schema_version") != "v1" or not isinstance(payload.get("routes"), dict):
            raise ValueError("provider route registry has an unsupported schema")
        return dict(payload["routes"])


def _default_route_path() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME")
    root = Path(base) if base else Path.home() / ".config"
    return root / "vibeos" / "provider-routes-v1.json"


def import_secret_from_environment(name: str) -> str:
    """Explicit one-shot migration helper. There is deliberately no read fallback."""

    if not name or name.startswith("VIBEOS_"):
        raise ValueError("a provider credential environment variable name is required")
    value = os.environ.pop(name, None)
    if not value:
        raise ValueError("the requested migration environment variable is empty or absent")
    return value
