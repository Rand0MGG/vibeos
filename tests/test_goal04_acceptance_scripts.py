from __future__ import annotations

import json
import subprocess
from typing import Any

from scripts.collect_vm_evidence import capabilities_ok, target_policy_command, target_policy_ok
from scripts.verify_foundation_dbus import _command_task, _find_receipt
from scripts.verify_wsl_real_actions import _find_receipt as find_wsl_receipt


def _task_payload() -> dict[str, Any]:
    return {
        "schema_version": "v2",
        "task_id": "task-acceptance",
        "receipts": [
            {
                "receipt_id": "receipt-acceptance",
                "status": "succeeded",
                "result": {
                    "schema_version": "v2",
                    "capability_id": "system.status",
                    "adapter_status": "succeeded",
                },
            }
        ],
    }


def test_foundation_dbus_uses_task_show_and_canonical_receipt() -> None:
    task = _task_payload()

    class Client:
        def call_json_method(self, method: str, task_id: str) -> dict[str, Any]:
            assert (method, task_id) == ("TaskShow", "task-acceptance")
            return task

    shown = _command_task(Client(), {"status": "executed", "result": {"task_id": "task-acceptance"}})  # type: ignore[arg-type]
    receipt = _find_receipt(shown, "system.status")
    assert receipt["receipt_id"] == "receipt-acceptance"
    assert receipt["adapter_status"] == "succeeded"
    assert find_wsl_receipt(shown, "system.status") == receipt


def test_vm_capability_validator_requires_complete_v2_effect_policy() -> None:
    payload = {
        "schema_version": "v2",
        "capability_details": [{"id": "system.status"}],
        "effect_policy": {level: level for level in ("E0", "E1", "E2", "E3", "E4")},
    }
    assert capabilities_ok(payload)
    assert not capabilities_ok({**payload, "schema_version": "v1"})
    assert not capabilities_ok({**payload, "effect_policy": {"E0": "observe"}})


def test_vm_target_policy_probe_executes_current_effect_contract() -> None:
    completed = subprocess.run(target_policy_command(), check=False, capture_output=True, text=True, timeout=10)
    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    assert target_policy_ok(payload)
    assert payload["effect_level"] == "E4"
