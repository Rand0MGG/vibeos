"""persist immutable dry-run execution intent

Revision ID: 0005_persist_dry_run_intent
Revises: 0004_goal_contract_version_index
"""

from __future__ import annotations

import json
from typing import Any

import sqlalchemy as sa
from alembic import op

revision = "0005_persist_dry_run_intent"
down_revision = "0004_goal_contract_version_index"
branch_labels = None
depends_on = None

_TERMINAL_STATUSES = frozenset({"dry_run", "succeeded", "failed", "cancelled", "blocked"})


def upgrade() -> None:
    bind = op.get_bind()
    has_task_runs = bool(bind.execute(sa.text("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'task_runs'")).scalars().one_or_none())
    query = (
        "SELECT goal_contracts.contract_id, goal_contracts.payload_json, task_runs.status "
        "FROM goal_contracts LEFT JOIN task_runs ON task_runs.task_id = goal_contracts.task_id "
        "ORDER BY goal_contracts.task_id, goal_contracts.version"
        if has_task_runs
        else "SELECT contract_id, payload_json, NULL AS status FROM goal_contracts ORDER BY task_id, version"
    )
    rows = bind.execute(sa.text(query)).mappings().all()
    for row in rows:
        payload = _payload(row["payload_json"])
        if "dry_run" in payload:
            continue
        status = str(row["status"] or "")
        payload["dry_run"] = True if status == "dry_run" else False if status in _TERMINAL_STATUSES else None
        bind.execute(
            sa.text("UPDATE goal_contracts SET payload_json = :payload WHERE contract_id = :contract_id"),
            {
                "contract_id": str(row["contract_id"]),
                "payload": json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
            },
        )


def downgrade() -> None:
    # Removing execution intent would recreate the unsafe recovery behavior.
    pass


def _payload(raw: Any) -> dict[str, Any]:
    payload = json.loads(str(raw))
    if not isinstance(payload, dict):
        raise ValueError("goal contract payload must be a JSON object")
    return payload
