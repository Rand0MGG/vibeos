from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any
from dataclasses import asdict

from .models import CommandRequest, Intent, PermissionReview, utc_now_iso

_CONTENT_BEARING_ACTIONS = {"clipboard.write", "notification.send"}
_SENSITIVE_PAYLOAD_KEYS = {"body", "content", "message", "supplemental_input", "text"}
_SECRET_KEY_PARTS = {"api_key", "authorization", "credential", "password", "secret", "token"}


def default_audit_path() -> Path:
    base = os.environ.get("VIBEOS_STATE_DIR")
    if base:
        return Path(base) / "audit.jsonl"
    if os.name == "posix":
        xdg_state = os.environ.get("XDG_STATE_HOME")
        if xdg_state:
            return Path(xdg_state) / "vibeos" / "audit.jsonl"
        return Path.home() / ".local" / "state" / "vibeos" / "audit.jsonl"
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        return Path(local_app_data) / "VibeOS" / "audit.jsonl"
    return Path.cwd() / ".vibeos" / "audit.jsonl"


class AuditLog:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or default_audit_path()

    def record(
        self,
        request: CommandRequest,
        intent: Intent,
        status: str,
        result: Any = None,
        selected_target: str | None = None,
        message: str = "",
        review: PermissionReview | None = None,
        review_id: str | None = None,
        plan_id: str | None = None,
        step_id: str | None = None,
        step_safety_review_id: str | None = None,
        layer: str | None = None,
        execution_status: str | None = None,
        acceptance_status: str | None = None,
        overall_status: str | None = None,
        trace_run_id: str | None = None,
        understanding_id: str | None = None,
        candidate_set_id: str | None = None,
        selected_route_decision_id: str | None = None,
        selected_strategy_decision_id: str | None = None,
        semantic_acceptance_decision_id: str | None = None,
        loop_snapshot_id: str | None = None,
    ) -> str:
        timestamp = utc_now_iso()
        audit_id = f"{timestamp}-{os.getpid()}"
        content_bearing = intent.action in _CONTENT_BEARING_ACTIONS
        entry = {
            "audit_id": audit_id,
            "timestamp": timestamp,
            "utterance": "[REDACTED USER CONTENT]" if content_bearing else request.utterance,
            "mode": request.mode,
            "transport": request.transport,
            "dry_run": request.dry_run,
            "approved": request.approve,
            "review_id": review_id or request.review_id,
            "intent": {
                "action": intent.action,
                "target": _redact_payload(intent.target) if content_bearing else intent.target,
                "reason": intent.reason,
                "requires_confirmation": intent.requires_confirmation,
            },
            "review": asdict(review) if review else None,
            "status": status,
            "execution_status": execution_status,
            "acceptance_status": acceptance_status,
            "overall_status": overall_status,
            "trace_run_id": trace_run_id,
            "understanding_id": understanding_id,
            "candidate_set_id": candidate_set_id,
            "selected_route_decision_id": selected_route_decision_id,
            "selected_strategy_decision_id": selected_strategy_decision_id,
            "semantic_acceptance_decision_id": semantic_acceptance_decision_id,
            "loop_snapshot_id": loop_snapshot_id,
            "plan_id": plan_id,
            "step_id": step_id,
            "step_safety_review_id": step_safety_review_id,
            "layer": layer,
            "selected_target": selected_target,
            "result": _redact_payload(result) if content_bearing else result,
            "message": message,
        }
        self._append(entry)
        return audit_id

    def tail(self, count: int = 20) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        lines = self.path.read_text(encoding="utf-8").splitlines()
        return [json.loads(line) for line in lines[-count:] if line.strip()]

    def _append(self, entry: dict[str, Any]) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except OSError:
            fallback = Path.cwd() / ".vibeos" / "audit.jsonl"
            fallback.parent.mkdir(parents=True, exist_ok=True)
            with fallback.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
            self.path = fallback
            print(f"vibeos: audit log fell back to {fallback}", file=sys.stderr)


def _redact_payload(value: Any) -> Any:
    if isinstance(value, dict):
        sanitized: dict[str, Any] = {}
        for key, item in value.items():
            normalized = str(key).lower()
            if normalized in _SENSITIVE_PAYLOAD_KEYS or any(part in normalized for part in _SECRET_KEY_PARTS):
                sanitized[str(key)] = "[REDACTED]"
            else:
                sanitized[str(key)] = _redact_payload(item)
        return sanitized
    if isinstance(value, (list, tuple)):
        return [_redact_payload(item) for item in value]
    return value
