"""repair effect-policy summaries produced by the original 0006 migration

Revision ID: 0007_repair_effect_policy_summary
Revises: 0006_effect_contract_v2
"""

from __future__ import annotations

import json
from typing import Any

import sqlalchemy as sa
from alembic import op

revision = "0007_repair_effect_policy_summary"
down_revision = "0006_effect_contract_v2"
branch_labels = None
depends_on = None

_EFFECT_POLICY = {
    "E0": "automatic observe-only",
    "E1": "automatic bounded local action with independent verification",
    "E2": "requires an independent reviewer and a complete rollback contract",
    "E3": "requires stored per-action user approval",
    "E4": "rejected",
}
_JSON_COLUMNS = (
    ("task_runs", "task_id", "payload_json"),
    ("goal_contracts", "task_id", "payload_json"),
    ("plan_revisions", "task_id", "payload_json"),
    ("task_steps", "task_id", "payload_json"),
    ("task_attempts", "task_id", "detail_json"),
    ("action_proposals", "task_id", "request_json"),
    ("task_action_receipts", "task_id", "result_json"),
    ("evidence_bundles", "task_id", "payload_json"),
)


def upgrade() -> None:
    bind = op.get_bind()
    has_task_runs = bool(bind.execute(sa.text("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'task_runs'")).scalars().one_or_none())
    if not has_task_runs:
        return
    live_ids = tuple(
        str(value)
        for value in bind.execute(sa.text("SELECT task_id FROM task_runs WHERE status NOT IN ('dry_run','succeeded','failed','cancelled','blocked')")).scalars()
    )
    for task_id in live_ids:
        for table, identity_column, json_column in _JSON_COLUMNS:
            rows = bind.execute(
                sa.text(f"SELECT rowid AS migration_rowid, {json_column} FROM {table} WHERE {identity_column} = :task_id"),
                {"task_id": task_id},
            ).mappings()
            for row in rows:
                payload = _json_object(row[json_column], table, json_column)
                repaired, changed = _repair(payload)
                if changed:
                    bind.execute(
                        sa.text(f"UPDATE {table} SET {json_column} = :payload WHERE rowid = :rowid"),
                        {"rowid": row["migration_rowid"], "payload": _dump(repaired)},
                    )
        _repair_envelopes(bind, task_id)


def downgrade() -> None:
    # The incorrect policy summary is not a supported representation.
    pass


def _repair_envelopes(bind: Any, task_id: str) -> None:
    for table, where in (
        ("current_state", "aggregate_type = 'task' AND aggregate_id = :task_id"),
        ("domain_events", "aggregate_type = 'task' AND aggregate_id = :task_id"),
        ("outbox", "aggregate_id = :task_id"),
    ):
        rows = bind.execute(sa.text(f"SELECT rowid AS migration_rowid, payload_json FROM {table} WHERE {where}"), {"task_id": task_id}).mappings()
        for row in rows:
            payload = _json_object(row["payload_json"], table, "payload_json")
            repaired, changed = _repair(payload)
            if changed:
                bind.execute(
                    sa.text(f"UPDATE {table} SET payload_json = :payload WHERE rowid = :rowid"),
                    {"rowid": row["migration_rowid"], "payload": _dump(repaired)},
                )


def _repair(value: Any) -> tuple[Any, bool]:
    if isinstance(value, list):
        changed = False
        repaired_items: list[Any] = []
        for item in value:
            repaired, item_changed = _repair(item)
            repaired_items.append(repaired)
            changed = changed or item_changed
        return repaired_items, changed
    if not isinstance(value, dict):
        return value, False
    changed = False
    repaired_dict: dict[str, Any] = {}
    for key, item in value.items():
        if key == "permission_policy" and isinstance(item, dict):
            repaired_dict["effect_policy"] = dict(_EFFECT_POLICY)
            changed = True
            continue
        if key == "effect_policy" and isinstance(item, dict) and _is_broken_policy(item):
            repaired_dict[key] = dict(_EFFECT_POLICY)
            changed = True
            continue
        repaired, item_changed = _repair(item)
        repaired_dict[key] = repaired
        changed = changed or item_changed
    return repaired_dict, changed


def _is_broken_policy(value: dict[Any, Any]) -> bool:
    keys = {str(key) for key in value}
    return any(key.startswith("L") for key in keys) or (keys == {"E0", "E1", "E2", "E3"} and "E4" not in keys)


def _json_object(raw: Any, table: str, column: str) -> dict[str, Any]:
    payload = json.loads(str(raw))
    if not isinstance(payload, dict):
        raise ValueError(f"{table}.{column} must contain a JSON object")
    return payload


def _dump(value: dict[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
