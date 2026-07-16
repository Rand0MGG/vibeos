"""repair durable task semantics

Revision ID: 0003_repair_durable_task_semantics
Revises: 0002_durable_task_engine
"""

from __future__ import annotations

import json
from hashlib import sha256
from typing import Any

import sqlalchemy as sa
from alembic import op


revision = "0003_repair_durable_task_semantics"
down_revision = "0002_durable_task_engine"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    tables = {name: _table(bind, name) for name in ("task_runs", "plan_revisions", "task_steps")}
    bind.execute(sa.text("CREATE INDEX IF NOT EXISTS idx_task_runs_deadline ON task_runs(json_extract(payload_json, '$.deadline_at'))"))
    bind.execute(sa.text("CREATE INDEX IF NOT EXISTS idx_wait_conditions_event ON wait_conditions(status, event_key)"))
    task_runs = tables["task_runs"]
    plan_revisions = tables["plan_revisions"]
    rows = bind.execute(
        sa.select(task_runs.c.task_id, task_runs.c.status, task_runs.c.payload_json, plan_revisions.c.plan_revision_id, plan_revisions.c.payload_json)
        .select_from(task_runs.outerjoin(plan_revisions, task_runs.c.task_id == plan_revisions.c.task_id))
        .where(sa.func.json_extract(task_runs.c.payload_json, "$.last_event") == "legacy_interaction_migrated")
    ).mappings()
    for row in rows:
        _repair_legacy_task(bind, dict(row), tables)


def downgrade() -> None:
    bind = op.get_bind()
    bind.execute(sa.text("DROP INDEX IF EXISTS idx_wait_conditions_event"))
    bind.execute(sa.text("DROP INDEX IF EXISTS idx_task_runs_deadline"))


def _repair_legacy_task(bind: Any, row: dict[str, Any], tables: dict[str, sa.Table]) -> None:
    task_id = str(row["task_id"])
    state = json.loads(str(row["payload_json"]))
    plan_revision_id = row.get("plan_revision_id")
    plan_payload_raw = row.get("payload_json_1")
    plan = _normalized_plan(plan_payload_raw)
    if plan_revision_id and plan is not None:
        current_step_id = _ensure_steps(bind, tables["task_steps"], task_id, str(plan_revision_id), plan, state)
        state["active_plan_revision_id"] = str(plan_revision_id)
        state["current_step_id"] = current_step_id
        _update_state(bind, tables["task_runs"], task_id, state)
        return
    if str(row["status"]) == "awaiting_review":
        state.update(
            {
                "status": "paused",
                "pending_interaction_id": None,
                "pending_reason": "legacy approval lacked a restorable plan; manual safe disposition is required",
                "suspended_status": "awaiting_review",
            }
        )
        _update_state(bind, tables["task_runs"], task_id, state)


def _normalized_plan(raw: object) -> dict[str, Any] | None:
    if not raw:
        return None
    try:
        payload = json.loads(str(raw))
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    plan = payload.get("plan") if isinstance(payload.get("plan"), dict) else payload
    steps = plan.get("steps") if isinstance(plan, dict) else None
    if not (isinstance(plan, dict) and plan.get("schema_version") and plan.get("plan_id") and plan.get("utterance") and isinstance(steps, list)):
        return None
    return plan


def _ensure_steps(
    bind: Any,
    table: sa.Table,
    task_id: str,
    plan_revision_id: str,
    plan: dict[str, Any],
    state: dict[str, Any],
) -> str | None:
    created_at = str(state["created_at"])
    step_ids: list[str] = []
    for ordinal, step in enumerate(plan.get("steps", [])):
        if not isinstance(step, dict) or not step.get("id") or not step.get("action"):
            continue
        step_id = str(step["id"])
        step_ids.append(step_id)
        action = str(step["action"])
        bind.execute(
            table.insert()
            .prefix_with("OR IGNORE")
            .values(
                step_key=f"{task_id}:{plan_revision_id}:{step_id}",
                step_id=step_id,
                task_id=task_id,
                plan_revision_id=plan_revision_id,
                ordinal=ordinal,
                action=action,
                capability_id=str(step.get("capability_id") or action),
                status="pending",
                idempotency_key=f"idem_{_digest(task_id + plan_revision_id + step_id + action)}",
                schema_version="v1",
                payload_json=json.dumps(step, ensure_ascii=False, separators=(",", ":")),
                created_at=created_at,
                updated_at=created_at,
            )
        )
    previous = state.get("current_step_id")
    return str(previous) if previous and str(previous) in step_ids else (step_ids[0] if step_ids else None)


def _update_state(bind: Any, table: sa.Table, task_id: str, state: dict[str, Any]) -> None:
    bind.execute(
        table.update()
        .where(table.c.task_id == task_id)
        .values(
            status=str(state["status"]),
            active_plan_revision_id=state.get("active_plan_revision_id"),
            current_step_id=state.get("current_step_id"),
            pending_interaction_id=state.get("pending_interaction_id"),
            payload_json=json.dumps(state, ensure_ascii=False, separators=(",", ":")),
        )
    )


def _digest(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()[:32]


def _table(bind: Any, name: str) -> sa.Table:
    """Reflect the schema installed by 0002 without importing runtime metadata."""
    return sa.Table(name, sa.MetaData(), autoload_with=bind)
