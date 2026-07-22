from __future__ import annotations

import os
import platform
import shutil
import subprocess
from dataclasses import asdict, dataclass
from typing import Callable

from .apps import AppRegistry
from .config import load_dotenv
from .model_gateway.secrets import ProviderRouteRepository, SECRET_TOOL
from .portal import PortalAdapter
from .runtime import detect_runtime_entry
from .windows import call_vibeos_shell

CommandRunner = Callable[[list[str]], subprocess.CompletedProcess[str]]


@dataclass(frozen=True)
class DoctorCheck:
    name: str
    status: str
    message: str
    detail: dict[str, object] | None = None


class SessionDoctor:
    def __init__(
        self,
        runner: CommandRunner | None = None,
        apps: AppRegistry | None = None,
        portal: PortalAdapter | None = None,
    ) -> None:
        self.runner = runner or run_command
        self.apps = apps or AppRegistry()
        self.portal = portal or PortalAdapter()

    def run(self) -> dict[str, object]:
        load_dotenv()
        checks = [
            self.check_platform(),
            self.check_session_type(),
            self.check_gnome_shell(),
            self.check_dbus_tools(),
            self.check_portal(),
            self.check_systemd_user(),
            self.check_vibed_service(),
            self.check_runtime_entry(),
            self.check_gnome_extension_bridge(),
            self.check_app_registry(),
            self.check_action_helpers(),
            self.check_model_config(),
        ]
        return {
            "summary": summarize(checks),
            "checks": [asdict(check) for check in checks],
        }

    def check_platform(self) -> DoctorCheck:
        system = platform.system()
        if system == "Linux":
            return DoctorCheck("platform", "ok", "running on Linux", {"system": system})
        return DoctorCheck("platform", "warn", "not running on Linux; desktop capabilities cannot execute here", {"system": system})

    def check_session_type(self) -> DoctorCheck:
        session_type = os.environ.get("XDG_SESSION_TYPE", "")
        if session_type == "wayland":
            return DoctorCheck("session_type", "ok", "GNOME Wayland session detected", {"XDG_SESSION_TYPE": session_type})
        if session_type:
            return DoctorCheck("session_type", "warn", "VibeOS targets GNOME Wayland first", {"XDG_SESSION_TYPE": session_type})
        return DoctorCheck("session_type", "warn", "XDG_SESSION_TYPE is not set", {})

    def check_gnome_shell(self) -> DoctorCheck:
        if not shutil.which("gnome-shell"):
            return DoctorCheck("gnome_shell", "warn", "gnome-shell command not found", {})
        completed = self.runner(["gnome-shell", "--version"])
        if completed.returncode == 0:
            return DoctorCheck("gnome_shell", "ok", completed.stdout.strip(), {})
        return DoctorCheck("gnome_shell", "warn", completed.stderr.strip() or "failed to query GNOME Shell version", {})

    def check_dbus_tools(self) -> DoctorCheck:
        if platform.system() != "Linux":
            return DoctorCheck("gdbus", "warn", "gdbus is not available outside the target Linux session", {})
        if shutil.which("gdbus"):
            return DoctorCheck("gdbus", "ok", "gdbus is available", {})
        return DoctorCheck("gdbus", "fail", "gdbus is required for D-Bus and portal checks", {})

    def check_portal(self) -> DoctorCheck:
        status = self.portal.status()
        if status.get("available"):
            return DoctorCheck("xdg_desktop_portal", "ok", "xdg-desktop-portal is available", status)
        return DoctorCheck("xdg_desktop_portal", "warn", str(status.get("reason", "portal unavailable")), status)

    def check_systemd_user(self) -> DoctorCheck:
        if not shutil.which("systemctl"):
            return DoctorCheck("systemd_user", "warn", "systemctl not found", {})
        completed = self.runner(["systemctl", "--user", "is-system-running"])
        if completed.returncode == 0 or completed.stdout.strip() in {"running", "degraded"}:
            return DoctorCheck("systemd_user", "ok", completed.stdout.strip() or "systemd user manager available", {})
        return DoctorCheck("systemd_user", "warn", completed.stderr.strip() or completed.stdout.strip() or "systemd user manager unavailable", {})

    def check_vibed_service(self) -> DoctorCheck:
        if not shutil.which("systemctl"):
            return DoctorCheck("vibed_service", "warn", "systemctl not found", {})
        completed = self.runner(["systemctl", "--user", "is-active", "vibed.service"])
        if completed.stdout.strip() == "active":
            return DoctorCheck("vibed_service", "ok", "vibed.service is active", {})
        return DoctorCheck("vibed_service", "warn", "vibed.service is not active", {"systemctl": completed.stdout.strip() or completed.stderr.strip()})

    def check_runtime_entry(self) -> DoctorCheck:
        transport, status, detail = detect_runtime_entry()
        if transport == "local" and status == "fail":
            return DoctorCheck("runtime_entry", "fail", "CLI runtime requires daemon transport but would fall back to the local broker", detail)
        if transport == "dbus":
            if status == "fail":
                return DoctorCheck("runtime_entry", "fail", "CLI runtime is configured for the D-Bus daemon, but that transport is unavailable", detail)
            return DoctorCheck("runtime_entry", status, "CLI runtime will use the D-Bus daemon", detail)
        if transport == "http":
            if status == "fail":
                return DoctorCheck("runtime_entry", "fail", "CLI runtime is configured for the HTTP daemon, but that transport is unavailable", detail)
            return DoctorCheck("runtime_entry", status, "CLI runtime will use the HTTP daemon", detail)
        return DoctorCheck("runtime_entry", status, "CLI runtime will fall back to the local broker", detail)

    def check_gnome_extension_bridge(self) -> DoctorCheck:
        raw = call_vibeos_shell("ListWindows")
        if raw is None:
            return DoctorCheck("gnome_extension_bridge", "warn", "VibeOS GNOME Shell bridge is not responding", {})
        return DoctorCheck("gnome_extension_bridge", "ok", "VibeOS GNOME Shell bridge responded", {"raw": raw[:200]})

    def check_app_registry(self) -> DoctorCheck:
        apps = self.apps.list_apps()
        if apps:
            return DoctorCheck("app_registry", "ok", f"found {len(apps)} desktop applications", {"count": len(apps)})
        return DoctorCheck("app_registry", "warn", "no .desktop applications found", {"count": 0})

    def check_action_helpers(self) -> DoctorCheck:
        notify_send = bool(shutil.which("notify-send"))
        clipboard_tools = [tool for tool in ("wl-copy", "xclip", "xsel") if shutil.which(tool)]
        detail = {
            "notify-send": notify_send,
            "clipboard_tools": clipboard_tools,
        }
        missing = []
        if not notify_send:
            missing.append("notify-send")
        if not clipboard_tools:
            missing.append("wl-copy/xclip/xsel")
        if not missing:
            return DoctorCheck("action_helpers", "ok", "notification and clipboard helpers are available", detail)
        return DoctorCheck("action_helpers", "warn", f"missing helper tools: {', '.join(missing)}", detail)

    def check_model_config(self) -> DoctorCheck:
        try:
            routes = ProviderRouteRepository().list_routes()
        except (OSError, ValueError):
            return DoctorCheck("model_config", "warn", "Model Gateway route metadata is invalid", {"gateway_schema": "v1"})
        if routes and os.path.exists(SECRET_TOOL):
            return DoctorCheck(
                "model_config",
                "ok",
                "Model Gateway route metadata and Secret Service client are configured; use `vibe secrets status` to verify the keyring item",
                {"gateway_schema": "v1", "routes": [route.route_id for route in routes], "secret_transport": "secret-tool"},
            )
        return DoctorCheck(
            "model_config",
            "warn",
            "no Model Gateway SecretRef route configured; use `vibe secrets import` or use --offline",
            {"gateway_schema": "v1", "routes": [route.route_id for route in routes], "secret_tool": os.path.exists(SECRET_TOOL)},
        )


def run_command(command: list[str]) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(command, check=False, capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return subprocess.CompletedProcess(command, 1, "", str(exc))


def summarize(checks: list[DoctorCheck]) -> dict[str, int | str]:
    counts = {"ok": 0, "warn": 0, "fail": 0}
    for check in checks:
        counts[check.status] += 1
    overall = "ok"
    if counts["fail"]:
        overall = "fail"
    elif counts["warn"]:
        overall = "warn"
    return {"overall": overall, **counts}
