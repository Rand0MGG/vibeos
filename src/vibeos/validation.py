from __future__ import annotations

import json
from typing import Any

from .models import ALLOWED_ACTIONS, Intent

DANGEROUS_KEYS = {
    "command",
    "shell",
    "script",
    "bash",
    "python",
    "dbus_path",
    "dbus_method",
    "raw_api",
    "exec",
}


class IntentValidationError(ValueError):
    pass


def parse_intent_json(raw: str) -> Intent:
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise IntentValidationError(f"model output is not valid JSON: {exc}") from exc
    return validate_intent_payload(data)


def validate_intent_payload(data: dict[str, Any]) -> Intent:
    if not isinstance(data, dict):
        raise IntentValidationError("intent payload must be an object")

    bad_keys = DANGEROUS_KEYS.intersection(data.keys())
    if bad_keys:
        raise IntentValidationError(f"intent payload contains forbidden keys: {sorted(bad_keys)}")

    action = data.get("action") or data.get("intent") or data.get("capability")
    if action not in ALLOWED_ACTIONS:
        raise IntentValidationError(f"unsupported action: {action!r}")

    target = data.get("target") or {}
    if not isinstance(target, dict):
        raise IntentValidationError("target must be an object")
    target_bad_keys = find_forbidden_keys(target)
    if target_bad_keys:
        raise IntentValidationError(f"target contains forbidden keys: {sorted(target_bad_keys)}")

    reason = data.get("reason") or ""
    if not isinstance(reason, str):
        raise IntentValidationError("reason must be a string")

    requires_confirmation = data.get("requires_confirmation", False)
    if not isinstance(requires_confirmation, bool):
        raise IntentValidationError("requires_confirmation must be a boolean")

    return Intent(
        action=action,
        target=target,
        reason=reason,
        requires_confirmation=requires_confirmation,
    )


def find_forbidden_keys(value: Any) -> set[str]:
    if isinstance(value, dict):
        bad_keys = DANGEROUS_KEYS.intersection(value.keys())
        for nested in value.values():
            bad_keys.update(find_forbidden_keys(nested))
        return bad_keys
    if isinstance(value, list):
        bad_keys: set[str] = set()
        for item in value:
            bad_keys.update(find_forbidden_keys(item))
        return bad_keys
    return set()
