from __future__ import annotations

from dataclasses import asdict

from .goal_loop import loop_state_from_payload
from .loop_models import LoopState


LOOP_SNAPSHOT_VERSION = 1


class LoopSnapshotError(ValueError):
    """A persisted GoalLoop state cannot safely resume."""


def encode_loop_snapshot(state: LoopState) -> dict[str, object]:
    return {"snapshot_version": LOOP_SNAPSHOT_VERSION, **asdict(state)}


def decode_loop_snapshot(payload: dict[str, object] | None) -> LoopState:
    if not isinstance(payload, dict):
        raise LoopSnapshotError("loop snapshot is missing")
    version = payload.get("snapshot_version", 0)
    if version not in {0, LOOP_SNAPSHOT_VERSION}:
        raise LoopSnapshotError(f"unsupported loop snapshot version: {version!r}")
    required = ("loop_snapshot_id", "trace_run_id", "goal_id", "stage")
    if any(not isinstance(payload.get(field), str) or not str(payload[field]) for field in required):
        raise LoopSnapshotError("loop snapshot is missing required identity fields")
    try:
        return loop_state_from_payload(payload)
    except (KeyError, TypeError, ValueError) as exc:
        raise LoopSnapshotError("loop snapshot is malformed") from exc
