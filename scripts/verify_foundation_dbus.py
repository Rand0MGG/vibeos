#!/usr/bin/env python3
"""Exercise Goal 01 foundation slices through a real session D-Bus bus.

Run this with an existing user-session bus or under ``dbus-run-session``.  The
script deliberately distinguishes a real D-Bus/adapter invocation from proof
that a desktop notification was shown; the latter still requires the supported
GNOME VM matrix.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

from vibeos.runtime import DBusDaemonClient


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="vibeos-foundation-dbus-") as state_dir:
        environment = os.environ.copy()
        environment.update(
            {
                "VIBEOS_STATE_DIR": state_dir,
                "VIBEOS_RUNTIME": "dbus",
                "VIBEOS_REQUIRE_DAEMON": "1",
                "VIBEOS_ENABLE_MODEL_UNDERSTANDING": "0",
                "VIBEOS_ENABLE_MODEL_UNDERSTANDING_TRANSITION": "0",
                "VIBEOS_ENABLE_MODEL_GOAL_SYNTHESIS": "0",
                "VIBEOS_ENABLE_MODEL_ROUTE_SELECTION": "0",
                "VIBEOS_ENABLE_MODEL_CLARIFICATION": "0",
                "VIBEOS_ENABLE_MODEL_REPLANNING": "0",
                "VIBEOS_ENABLE_MODEL_SEMANTIC_ACCEPTANCE": "0",
                "VIBEOS_ENABLE_MODEL_STRATEGY_SELECTION": "0",
            }
        )
        daemon = subprocess.Popen(
            [sys.executable, "-m", "vibeos.daemon", "--dbus", "--offline", "--port", "0"],
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            client = DBusDaemonClient()
            status = _wait_until_ready(client, daemon)
            capabilities = client.call_json_method("Capabilities")
            if not isinstance(capabilities, dict) or len(capabilities.get("capabilities", [])) != 19:
                raise RuntimeError("D-Bus capability discovery did not return the 19-capability baseline")

            e0 = client.request_payload({"schema_version": "v1", "utterance": "status"})
            e0_receipt = _find_mapping(e0, "action_receipt")
            if e0.get("status") != "executed" or e0_receipt.get("capability_id") != "system.status":
                raise RuntimeError(f"E0 D-Bus execution failed: {e0!r}")

            e1 = client.request_payload({"schema_version": "v1", "utterance": "notify foundation verified"})
            e1_receipt = _find_mapping(e1, "action_receipt")
            if e1_receipt.get("capability_id") != "notification.send":
                raise RuntimeError(f"E1 request did not reach the foundation slice: {e1!r}")

            invalid = client.request_payload({"schema_version": "v1", "utterance": "status", "unknown": True})
            if invalid.get("status") != "failed" or _find_value(invalid, "error") != "invalid_contract":
                raise RuntimeError(f"D-Bus strict-contract rejection failed: {invalid!r}")

            database = Path(state_dir) / "reviews.sqlite3"
            payload = {
                "ok": True,
                "transport": "dbus-session",
                "lifecycle": status.get("lifecycle"),
                "capability_count": len(capabilities["capabilities"]),
                "e0": {
                    "status": e0.get("status"),
                    "receipt_status": e0_receipt.get("status"),
                    "adapter_status": e0_receipt.get("adapter_status"),
                },
                "e1": {
                    "status": e1.get("status"),
                    "receipt_status": e1_receipt.get("status"),
                    "adapter_status": e1_receipt.get("adapter_status"),
                    "desktop_effect_verified": False,
                },
                "strict_unknown_field": "rejected",
                "database_created": database.exists(),
            }
            print(json.dumps(payload, ensure_ascii=False, indent=2))
            return 0
        finally:
            daemon.terminate()
            try:
                daemon.communicate(timeout=10)
            except subprocess.TimeoutExpired:
                daemon.kill()
                daemon.communicate()


def _wait_until_ready(client: DBusDaemonClient, daemon: subprocess.Popen[str]) -> dict[str, Any]:
    deadline = time.monotonic() + 20
    last_error = "D-Bus service was not observed"
    while time.monotonic() < deadline:
        if daemon.poll() is not None:
            stdout, stderr = daemon.communicate()
            raise RuntimeError(f"vibed exited before readiness: {stdout}\n{stderr}")
        try:
            payload = client.call_json_method("Status")
            if isinstance(payload, dict) and payload.get("ready") is True:
                return payload
        except RuntimeError as exc:
            last_error = str(exc)
        time.sleep(0.1)
    raise RuntimeError(last_error)


def _find_mapping(value: Any, key: str) -> dict[str, Any]:
    candidate = _find_value(value, key)
    if not isinstance(candidate, dict):
        raise RuntimeError(f"{key} was not a mapping")
    return candidate


def _find_value(value: Any, key: str) -> Any:
    if isinstance(value, dict):
        if key in value:
            return value[key]
        for item in value.values():
            try:
                return _find_value(item, key)
            except KeyError:
                continue
    elif isinstance(value, (list, tuple)):
        for item in value:
            try:
                return _find_value(item, key)
            except KeyError:
                continue
    raise KeyError(key)


if __name__ == "__main__":
    raise SystemExit(main())
