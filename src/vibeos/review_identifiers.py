from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from typing import Any

from .models import Intent


def make_review_id(utterance: str, intent: Intent, created_at: str) -> str:
    digest = hashlib.sha256(
        json.dumps(
            {"utterance": utterance, "intent": asdict(intent), "created_at": created_at},
            ensure_ascii=False,
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()[:12]
    return f"rev_{digest}"


def make_plan_review_id(plan_payload: dict[str, Any], created_at: str) -> str:
    digest = hashlib.sha256(
        json.dumps(
            {"plan": plan_payload, "created_at": created_at},
            ensure_ascii=False,
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()[:12]
    return f"rev_{digest}"


def canonical_payload_hash(payload: object) -> str | None:
    if payload is None:
        return None
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
