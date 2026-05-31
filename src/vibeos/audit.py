from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any
from dataclasses import asdict

from .models import CommandRequest, Intent, PermissionReview, utc_now_iso


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
    ) -> str:
        timestamp = utc_now_iso()
        audit_id = f"{timestamp}-{os.getpid()}"
        entry = {
            "audit_id": audit_id,
            "timestamp": timestamp,
            "utterance": request.utterance,
            "mode": request.mode,
            "dry_run": request.dry_run,
            "approved": request.approve,
            "review_id": review_id or request.review_id,
            "intent": {
                "action": intent.action,
                "target": intent.target,
                "reason": intent.reason,
                "requires_confirmation": intent.requires_confirmation,
            },
            "review": asdict(review) if review else None,
            "status": status,
            "selected_target": selected_target,
            "result": result,
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
