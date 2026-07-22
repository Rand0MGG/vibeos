"""migrate resumable task payloads to the Goal 04 effect contract

Revision ID: 0006_effect_contract_v2
Revises: 0005_persist_dry_run_intent
"""

from __future__ import annotations

import json
from typing import Any

import sqlalchemy as sa
from alembic import op

revision = "0006_effect_contract_v2"
down_revision = "0005_persist_dry_run_intent"
branch_labels = None
depends_on = None

_TERMINAL = frozenset({"dry_run", "succeeded", "failed", "cancelled", "blocked"})
_ACTION_EFFECT = {
    "app.list": "E0",
    "window.list": "E0",
    "system.status": "E0",
    "app.open": "E1",
    "window.focus": "E1",
    "window.minimize": "E1",
    "window.maximize": "E1",
    "notification.send": "E1",
    "portal.open_uri": "E1",
    "clipboard.write": "E1",
    "browser.open_url": "E1",
    "browser.search_web": "E1",
    "browser.open_named_target": "E1",
    "browser.open_site_search": "E1",
    "media.search": "E1",
    "media.play": "E1",
    "media.pause": "E1",
    "app.search_history": "E1",
    "window.close": "E3",
    "unknown": "E4",
}
_OBSERVATION = {"L0": "O0", "L1": "O1", "L2": "O2"}
_SCHEMA_TABLES = (
    "task_runs",
    "goal_contracts",
    "plan_revisions",
    "task_steps",
    "task_attempts",
    "wait_conditions",
    "action_proposals",
    "task_action_receipts",
    "evidence_bundles",
)
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
        ambiguous: list[str] = []
        for table, identity_column, json_column in _JSON_COLUMNS:
            rows = bind.execute(
                sa.text(f"SELECT rowid AS migration_rowid, {json_column} FROM {table} WHERE {identity_column} = :task_id"),
                {"task_id": task_id},
            ).mappings()
            for row in rows:
                payload = _json_object(row[json_column], table, json_column)
                migrated = _transform(payload, ambiguous, path=f"{table}.{json_column}")
                bind.execute(
                    sa.text(f"UPDATE {table} SET {json_column} = :payload WHERE rowid = :rowid"),
                    {"rowid": row["migration_rowid"], "payload": _dump(migrated)},
                )
        for table in _SCHEMA_TABLES:
            bind.execute(sa.text(f"UPDATE {table} SET schema_version = 'v2' WHERE task_id = :task_id"), {"task_id": task_id})
        bind.execute(
            sa.text("UPDATE current_state SET schema_version = 'v2' WHERE aggregate_type = 'task' AND aggregate_id = :task_id"),
            {"task_id": task_id},
        )
        bind.execute(
            sa.text("UPDATE domain_events SET schema_version = 'v2' WHERE aggregate_type = 'task' AND aggregate_id = :task_id"),
            {"task_id": task_id},
        )
        bind.execute(sa.text("UPDATE outbox SET schema_version = 'v2' WHERE aggregate_id = :task_id"), {"task_id": task_id})
        _migrate_envelope_payloads(bind, task_id, ambiguous)
        if ambiguous:
            _pause_for_disposition(bind, task_id, ambiguous)


def downgrade() -> None:
    # v1 live execution was intentionally removed. Rollback is the paired
    # pre-upgrade artifact plus CoreDatabase's pre-migration database backup.
    pass


def _migrate_envelope_payloads(bind: Any, task_id: str, ambiguous: list[str]) -> None:
    for table, where in (
        ("current_state", "aggregate_type = 'task' AND aggregate_id = :task_id"),
        ("domain_events", "aggregate_type = 'task' AND aggregate_id = :task_id"),
        ("outbox", "aggregate_id = :task_id"),
    ):
        rows = bind.execute(sa.text(f"SELECT rowid AS migration_rowid, payload_json FROM {table} WHERE {where}"), {"task_id": task_id}).mappings()
        for row in rows:
            payload = _json_object(row["payload_json"], table, "payload_json")
            migrated = _transform(payload, ambiguous, path=f"{table}.payload_json")
            bind.execute(
                sa.text(f"UPDATE {table} SET payload_json = :payload WHERE rowid = :rowid"),
                {"rowid": row["migration_rowid"], "payload": _dump(migrated)},
            )


def _transform(value: Any, ambiguous: list[str], *, path: str, parent: dict[str, Any] | None = None) -> Any:
    if isinstance(value, list):
        return [_transform(item, ambiguous, path=f"{path}[]", parent=parent) for item in value]
    if not isinstance(value, dict):
        return value
    action = str(value.get("action") or value.get("capability_id") or "")
    migrated: dict[str, Any] = {}
    for key, item in value.items():
        new_key = {"risk_level": "effect_level", "max_risk_level": "max_effect_level", "permission_policy": "effect_policy"}.get(key, key)
        if key in {"risk_level", "max_risk_level"} and isinstance(item, str):
            effect = _ACTION_EFFECT.get(action)
            if effect is None:
                effect = "E0" if item == "L0" else "E1" if item == "L1" else "E4"
                if item in {"L2", "L3"}:
                    ambiguous.append(f"{path}.{key}:{action or 'unbound'}={item}")
            migrated[new_key] = effect
        elif key in {"observation_level", "default_observation_level"} and item in _OBSERVATION:
            migrated[new_key] = _OBSERVATION[str(item)]
        elif key == "level" and "observation_id" in value and item in _OBSERVATION:
            migrated[new_key] = _OBSERVATION[str(item)]
        elif key == "permission_policy" and isinstance(item, dict):
            migrated[new_key] = {str(policy_key).replace("L", "E", 1): policy_value for policy_key, policy_value in item.items()}
        elif key == "schema_version":
            migrated[new_key] = "v2"
        else:
            migrated[new_key] = _transform(item, ambiguous, path=f"{path}.{new_key}", parent=value)
    if "schema_version" in value:
        migrated["schema_version"] = "v2"
    if action and "id" in value:
        migrated["schema_version"] = "v2"
    return migrated


def _pause_for_disposition(bind: Any, task_id: str, reasons: list[str]) -> None:
    row = bind.execute(sa.text("SELECT payload_json FROM task_runs WHERE task_id = :task_id"), {"task_id": task_id}).scalar_one()
    payload = _json_object(row, "task_runs", "payload_json")
    payload["status"] = "paused"
    payload["pending_reason"] = "Goal04 migration requires manual effect disposition: " + "; ".join(sorted(set(reasons))[:8])
    payload["last_event"] = "migration_disposition_required"
    payload["schema_version"] = "v2"
    bind.execute(
        sa.text("UPDATE task_runs SET status = 'paused', payload_json = :payload WHERE task_id = :task_id"),
        {"task_id": task_id, "payload": _dump(payload)},
    )
    bind.execute(
        sa.text("UPDATE current_state SET status = 'paused', payload_json = :payload WHERE aggregate_type = 'task' AND aggregate_id = :task_id"),
        {"task_id": task_id, "payload": _dump(payload)},
    )


def _json_object(raw: Any, table: str, column: str) -> dict[str, Any]:
    payload = json.loads(str(raw))
    if not isinstance(payload, dict):
        raise ValueError(f"{table}.{column} must contain a JSON object")
    return payload


def _dump(value: dict[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
