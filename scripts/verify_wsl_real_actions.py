from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any


NOTIFICATION_BUS_NAME = "org.freedesktop.Notifications"


def main() -> int:
    required = ("dunst", "dunstctl", "notify-send", "dbus-monitor", "gdbus", "vibe")
    missing = [name for name in required if shutil.which(name) is None]
    if missing:
        print(json.dumps({"ok": False, "error": "missing_commands", "commands": missing}, sort_keys=True))
        return 2
    if not os.environ.get("DISPLAY") and not os.environ.get("WAYLAND_DISPLAY"):
        print(json.dumps({"ok": False, "error": "missing_wslg_display"}, sort_keys=True))
        return 2

    daemon: subprocess.Popen[str] | None = None
    monitor: subprocess.Popen[str] | None = None
    try:
        if not _notification_service_owned():
            daemon = subprocess.Popen(
                ["dunst"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True,
            )
            _wait_for_notification_service(daemon)

        server = _run_jsonless(
            [
                "gdbus",
                "call",
                "--session",
                "--dest",
                NOTIFICATION_BUS_NAME,
                "--object-path",
                "/org/freedesktop/Notifications",
                "--method",
                "org.freedesktop.Notifications.GetServerInformation",
            ]
        )
        monitor = subprocess.Popen(
            [
                "dbus-monitor",
                "--session",
                "type='method_call',interface='org.freedesktop.Notifications',member='Notify'",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        time.sleep(0.2)

        status_payload = _run_vibe("status")
        notification_payload = _run_vibe("notify WSL real action verified")
        foundation_dbus = _run_json_command([sys.executable, str(Path(__file__).with_name("verify_foundation_dbus.py"))], timeout=40)
        time.sleep(0.3)
        displayed = _run_jsonless(["dunstctl", "count", "displayed"])
        monitor_output = _stop_process(monitor)
        monitor = None

        status_receipt = _find_receipt(status_payload, "system.status")
        notification_receipt = _find_receipt(notification_payload, "notification.send")
        notification_evidence = _find_mapping(notification_payload, "observation_evidence", "notification.send")
        dbus_notify_count = monitor_output.count("member=Notify")
        foundation_e1 = foundation_dbus.get("e1")
        if not isinstance(foundation_e1, dict):
            raise TypeError("foundation D-Bus verifier did not return an E1 result")
        displayed_count = int(displayed.strip())
        result = {
            "ok": (
                status_payload.get("status") == "executed"
                and notification_payload.get("status") == "executed"
                and status_receipt.get("status") == "succeeded"
                and notification_receipt.get("status") == "succeeded"
                and notification_receipt.get("adapter_status") == "succeeded"
                and foundation_dbus.get("ok") is True
                and foundation_e1.get("receipt_status") == "succeeded"
                and foundation_e1.get("adapter_status") == "succeeded"
                and dbus_notify_count >= 2
                and displayed_count >= 1
            ),
            "environment": {
                "display": os.environ.get("DISPLAY"),
                "wayland_display": os.environ.get("WAYLAND_DISPLAY"),
                "notification_server": server.strip(),
            },
            "e0": {
                "agent_status": status_payload.get("status"),
                "receipt_status": status_receipt.get("status"),
                "adapter_status": status_receipt.get("adapter_status"),
            },
            "e1": {
                "agent_status": notification_payload.get("status"),
                "receipt_status": notification_receipt.get("status"),
                "adapter_status": notification_receipt.get("adapter_status"),
                "delivery_adapter": notification_evidence.get("delivery_adapter"),
                "dbus_notify_count": dbus_notify_count,
                "dunst_displayed_count": displayed_count,
            },
            "daemon_dbus": {
                "lifecycle": foundation_dbus.get("lifecycle"),
                "transport": foundation_dbus.get("transport"),
                "e1_receipt_status": foundation_e1.get("receipt_status"),
                "e1_adapter_status": foundation_e1.get("adapter_status"),
            },
        }
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        return 0 if result["ok"] else 1
    except Exception as exc:
        print(
            json.dumps(
                {"ok": False, "error": type(exc).__name__, "message": str(exc)},
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        return 1
    finally:
        if monitor is not None:
            _stop_process(monitor)
        if daemon is not None:
            _stop_process(daemon)


def _notification_service_owned() -> bool:
    completed = subprocess.run(
        [
            "gdbus",
            "call",
            "--session",
            "--dest",
            "org.freedesktop.DBus",
            "--object-path",
            "/org/freedesktop/DBus",
            "--method",
            "org.freedesktop.DBus.NameHasOwner",
            NOTIFICATION_BUS_NAME,
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=5,
    )
    return "true" in completed.stdout.lower()


def _wait_for_notification_service(daemon: subprocess.Popen[str]) -> None:
    for _ in range(50):
        if daemon.poll() is not None:
            stderr = daemon.stderr.read() if daemon.stderr is not None else ""
            raise RuntimeError(f"dunst exited before claiming the notification bus: {stderr.strip()}")
        if _notification_service_owned():
            return
        time.sleep(0.1)
    raise TimeoutError("dunst did not claim org.freedesktop.Notifications")


def _run_vibe(utterance: str) -> dict[str, Any]:
    vibe = shutil.which("vibe")
    assert vibe is not None
    completed = subprocess.run(
        [vibe, "ask", utterance, "--json", "--offline"],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"vibe returned invalid JSON: {completed.stderr.strip()}") from exc
    if completed.returncode != 0:
        message = payload.get("message") if isinstance(payload, dict) else completed.stderr.strip()
        raise RuntimeError(f"vibe failed for {utterance!r}: {message}")
    if not isinstance(payload, dict):
        raise TypeError("vibe JSON result must be an object")
    return payload


def _find_receipt(value: object, capability_id: str) -> dict[str, Any]:
    return _find_mapping(value, "action_receipt", capability_id)


def _find_mapping(value: object, key: str, capability_id: str) -> dict[str, Any]:
    if isinstance(value, dict):
        candidate = value.get(key)
        if isinstance(candidate, dict) and candidate.get("capability_id") == capability_id:
            return candidate
        for item in value.values():
            try:
                return _find_mapping(item, key, capability_id)
            except KeyError:
                continue
    elif isinstance(value, list):
        for item in value:
            try:
                return _find_mapping(item, key, capability_id)
            except KeyError:
                continue
    raise KeyError(f"{key} for {capability_id} not found")


def _run_jsonless(command: list[str]) -> str:
    completed = subprocess.run(command, check=True, capture_output=True, text=True, timeout=10)
    return completed.stdout


def _run_json_command(command: list[str], *, timeout: int) -> dict[str, Any]:
    completed = subprocess.run(command, check=False, capture_output=True, text=True, timeout=timeout)
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"command returned invalid JSON: {completed.stderr.strip()}") from exc
    if completed.returncode != 0 or not isinstance(payload, dict):
        raise RuntimeError(f"command failed with exit {completed.returncode}: {completed.stderr.strip()}")
    return payload


def _stop_process(process: subprocess.Popen[str]) -> str:
    process.terminate()
    try:
        stdout, _stderr = process.communicate(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        stdout, _stderr = process.communicate(timeout=5)
    return stdout or ""


if __name__ == "__main__":
    raise SystemExit(main())
