"""durable task engine

Revision ID: 0002_durable_task_engine
Revises: 0001_core_foundation
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from hashlib import sha256
from typing import Any

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "0002_durable_task_engine"
down_revision = "0001_core_foundation"
branch_labels = None
depends_on = None

# Frozen schema snapshot for this revision. Historical migrations must not
# import mutable application metadata: later runtime schema changes must be
# expressed by a new Alembic revision instead.
metadata = sa.MetaData()
sa.Table("outbox", metadata, sa.Column("message_id", sa.String(240), primary_key=True))

goal_contracts = sa.Table(
    "goal_contracts",
    metadata,
    sa.Column("contract_id", sa.String(240), primary_key=True),
    sa.Column("task_id", sa.String(240), nullable=False),
    sa.Column("version", sa.Integer, nullable=False),
    sa.Column("schema_version", sa.String(20), nullable=False),
    sa.Column("payload_json", sa.Text, nullable=False),
    sa.Column("created_at", sa.String(40), nullable=False),
    sa.CheckConstraint("version >= 1", name="ck_goal_contract_version_positive"),
)
sa.Index("uq_goal_contracts_task_version", goal_contracts.c.task_id, goal_contracts.c.version, unique=True)
sa.Index("idx_goal_contracts_task_created", goal_contracts.c.task_id, goal_contracts.c.created_at)

task_runs = sa.Table(
    "task_runs",
    metadata,
    sa.Column("task_id", sa.String(240), primary_key=True),
    sa.Column("contract_id", sa.String(240), sa.ForeignKey("goal_contracts.contract_id", ondelete="RESTRICT"), nullable=False),
    sa.Column("status", sa.String(40), nullable=False),
    sa.Column("revision", sa.Integer, nullable=False),
    sa.Column("active_plan_revision_id", sa.String(240)),
    sa.Column("current_step_id", sa.String(240)),
    sa.Column("pending_interaction_id", sa.String(240), unique=True),
    sa.Column("next_wake_at", sa.String(40)),
    sa.Column("schema_version", sa.String(20), nullable=False),
    sa.Column("payload_json", sa.Text, nullable=False),
    sa.Column("created_at", sa.String(40), nullable=False),
    sa.Column("updated_at", sa.String(40), nullable=False),
    sa.CheckConstraint("revision >= 0", name="ck_task_runs_revision_nonnegative"),
)
sa.Index("idx_task_runs_status_wake", task_runs.c.status, task_runs.c.next_wake_at)
sa.Index("idx_task_runs_updated", task_runs.c.updated_at)

plan_revisions = sa.Table(
    "plan_revisions",
    metadata,
    sa.Column("plan_revision_id", sa.String(240), primary_key=True),
    sa.Column("task_id", sa.String(240), sa.ForeignKey("task_runs.task_id", ondelete="CASCADE"), nullable=False),
    sa.Column("revision", sa.Integer, nullable=False),
    sa.Column("plan_id", sa.String(240), nullable=False),
    sa.Column("schema_version", sa.String(20), nullable=False),
    sa.Column("payload_json", sa.Text, nullable=False),
    sa.Column("created_at", sa.String(40), nullable=False),
    sa.Column("reason", sa.Text, nullable=False),
    sa.CheckConstraint("revision >= 1", name="ck_plan_revision_positive"),
)
sa.Index("uq_plan_revisions_task_revision", plan_revisions.c.task_id, plan_revisions.c.revision, unique=True)

task_steps = sa.Table(
    "task_steps",
    metadata,
    sa.Column("step_key", sa.String(500), primary_key=True),
    sa.Column("step_id", sa.String(240), nullable=False),
    sa.Column("task_id", sa.String(240), sa.ForeignKey("task_runs.task_id", ondelete="CASCADE"), nullable=False),
    sa.Column("plan_revision_id", sa.String(240), sa.ForeignKey("plan_revisions.plan_revision_id", ondelete="CASCADE"), nullable=False),
    sa.Column("ordinal", sa.Integer, nullable=False),
    sa.Column("action", sa.String(120), nullable=False),
    sa.Column("capability_id", sa.String(120), nullable=False),
    sa.Column("status", sa.String(40), nullable=False),
    sa.Column("idempotency_key", sa.String(320), nullable=False, unique=True),
    sa.Column("schema_version", sa.String(20), nullable=False),
    sa.Column("payload_json", sa.Text, nullable=False),
    sa.Column("created_at", sa.String(40), nullable=False),
    sa.Column("updated_at", sa.String(40), nullable=False),
    sa.CheckConstraint("ordinal >= 0", name="ck_task_steps_ordinal_nonnegative"),
)
sa.Index("idx_task_steps_task_ordinal", task_steps.c.task_id, task_steps.c.ordinal)

task_attempts = sa.Table(
    "task_attempts",
    metadata,
    sa.Column("attempt_id", sa.String(240), primary_key=True),
    sa.Column("task_id", sa.String(240), sa.ForeignKey("task_runs.task_id", ondelete="CASCADE"), nullable=False),
    sa.Column("step_id", sa.String(240)),
    sa.Column("attempt_number", sa.Integer, nullable=False),
    sa.Column("classification", sa.String(80), nullable=False),
    sa.Column("status", sa.String(40), nullable=False),
    sa.Column("schema_version", sa.String(20), nullable=False),
    sa.Column("detail_json", sa.Text, nullable=False),
    sa.Column("started_at", sa.String(40), nullable=False),
    sa.Column("finished_at", sa.String(40)),
    sa.CheckConstraint("attempt_number >= 1", name="ck_task_attempt_number_positive"),
)
sa.Index("idx_task_attempts_task_step", task_attempts.c.task_id, task_attempts.c.step_id, task_attempts.c.attempt_number)

wait_conditions = sa.Table(
    "wait_conditions",
    metadata,
    sa.Column("wait_id", sa.String(240), primary_key=True),
    sa.Column("task_id", sa.String(240), sa.ForeignKey("task_runs.task_id", ondelete="CASCADE"), nullable=False),
    sa.Column("kind", sa.String(80), nullable=False),
    sa.Column("due_at", sa.String(40)),
    sa.Column("event_key", sa.String(320)),
    sa.Column("status", sa.String(40), nullable=False),
    sa.Column("schema_version", sa.String(20), nullable=False),
    sa.Column("created_at", sa.String(40), nullable=False),
    sa.Column("satisfied_at", sa.String(40)),
)
sa.Index("idx_wait_conditions_due", wait_conditions.c.status, wait_conditions.c.due_at)
sa.Index("idx_wait_conditions_event", wait_conditions.c.status, wait_conditions.c.event_key)

action_proposals = sa.Table(
    "action_proposals",
    metadata,
    sa.Column("proposal_id", sa.String(240), primary_key=True),
    sa.Column("task_id", sa.String(240), sa.ForeignKey("task_runs.task_id", ondelete="CASCADE"), nullable=False),
    sa.Column("step_id", sa.String(240), nullable=False),
    sa.Column("attempt_id", sa.String(240), sa.ForeignKey("task_attempts.attempt_id", ondelete="CASCADE"), nullable=False),
    sa.Column("idempotency_key", sa.String(320), nullable=False, unique=True),
    sa.Column("action", sa.String(120), nullable=False),
    sa.Column("capability_id", sa.String(120), nullable=False),
    sa.Column("status", sa.String(40), nullable=False),
    sa.Column("schema_version", sa.String(20), nullable=False),
    sa.Column("request_json", sa.Text, nullable=False),
    sa.Column("created_at", sa.String(40), nullable=False),
    sa.Column("updated_at", sa.String(40), nullable=False),
)
sa.Index("idx_action_proposals_reconcile", action_proposals.c.status, action_proposals.c.updated_at)

task_action_receipts = sa.Table(
    "task_action_receipts",
    metadata,
    sa.Column("receipt_id", sa.String(240), primary_key=True),
    sa.Column("task_id", sa.String(240), sa.ForeignKey("task_runs.task_id", ondelete="CASCADE"), nullable=False),
    sa.Column("step_id", sa.String(240), nullable=False),
    sa.Column("proposal_id", sa.String(240), sa.ForeignKey("action_proposals.proposal_id", ondelete="RESTRICT"), nullable=False, unique=True),
    sa.Column("idempotency_key", sa.String(320), nullable=False, unique=True),
    sa.Column("status", sa.String(40), nullable=False),
    sa.Column("adapter", sa.String(240)),
    sa.Column("external_reference", sa.String(500)),
    sa.Column("schema_version", sa.String(20), nullable=False),
    sa.Column("result_json", sa.Text, nullable=False),
    sa.Column("occurred_at", sa.String(40), nullable=False),
)

evidence_bundles = sa.Table(
    "evidence_bundles",
    metadata,
    sa.Column("evidence_id", sa.String(240), primary_key=True),
    sa.Column("task_id", sa.String(240), sa.ForeignKey("task_runs.task_id", ondelete="CASCADE"), nullable=False),
    sa.Column("step_id", sa.String(240)),
    sa.Column("receipt_id", sa.String(240), sa.ForeignKey("task_action_receipts.receipt_id", ondelete="SET NULL")),
    sa.Column("status", sa.String(40), nullable=False),
    sa.Column("summary", sa.Text, nullable=False),
    sa.Column("schema_version", sa.String(20), nullable=False),
    sa.Column("payload_json", sa.Text, nullable=False),
    sa.Column("observed_at", sa.String(40), nullable=False),
)
sa.Index("idx_evidence_task_observed", evidence_bundles.c.task_id, evidence_bundles.c.observed_at)

terminal_outcomes = sa.Table(
    "terminal_outcomes",
    metadata,
    sa.Column("task_id", sa.String(240), sa.ForeignKey("task_runs.task_id", ondelete="CASCADE"), primary_key=True),
    sa.Column("status", sa.String(40), nullable=False),
    sa.Column("reason", sa.Text, nullable=False),
    sa.Column("schema_version", sa.String(20), nullable=False),
    sa.Column("evidence_ids_json", sa.Text, nullable=False),
    sa.Column("finished_at", sa.String(40), nullable=False),
)

task_leases = sa.Table(
    "task_leases",
    metadata,
    sa.Column("task_id", sa.String(240), sa.ForeignKey("task_runs.task_id", ondelete="CASCADE"), primary_key=True),
    sa.Column("owner", sa.String(240)),
    sa.Column("expires_at", sa.String(40)),
    sa.Column("fencing_token", sa.Integer, nullable=False, server_default="0"),
    sa.Column("updated_at", sa.String(40), nullable=False),
    sa.CheckConstraint("fencing_token >= 0", name="ck_task_lease_fencing_nonnegative"),
)
sa.Index("idx_task_leases_expiry", task_leases.c.expires_at)

outbox_deliveries = sa.Table(
    "outbox_deliveries",
    metadata,
    sa.Column("message_id", sa.String(240), sa.ForeignKey("outbox.message_id", ondelete="CASCADE"), primary_key=True),
    sa.Column("consumer", sa.String(120), primary_key=True),
    sa.Column("delivered_at", sa.String(40), nullable=False),
    sa.Column("result_json", sa.Text, nullable=False),
)

_TASK_TABLES = (
    "goal_contracts",
    "task_runs",
    "plan_revisions",
    "task_steps",
    "task_attempts",
    "wait_conditions",
    "action_proposals",
    "task_action_receipts",
    "evidence_bundles",
    "terminal_outcomes",
    "task_leases",
    "outbox_deliveries",
)


def upgrade() -> None:
    bind = op.get_bind()
    columns = {column["name"] for column in inspect(bind).get_columns("outbox")}
    additions = (
        ("idempotency_key", sa.String(length=320)),
        ("available_at", sa.String(length=40)),
        ("lease_owner", sa.String(length=240)),
        ("lease_expires_at", sa.String(length=40)),
    )
    for name, type_ in additions:
        if name not in columns:
            op.add_column("outbox", sa.Column(name, type_, nullable=True))
    op.create_index("uq_outbox_idempotency_key", "outbox", ["idempotency_key"], unique=True)
    op.create_index("idx_outbox_available", "outbox", ["published_at", "available_at", "lease_expires_at"], unique=False)
    for name in _TASK_TABLES:
        metadata.tables[name].create(bind, checkfirst=True)
    _migrate_legacy_interactions(bind)
    tables = set(inspect(bind).get_table_names())
    if "review_events" in tables:
        op.drop_table("review_events")
    if "reviews" in tables:
        op.drop_table("reviews")


def downgrade() -> None:
    bind = op.get_bind()
    _create_legacy_review_tables(bind)
    for name in reversed(_TASK_TABLES):
        metadata.tables[name].drop(bind, checkfirst=True)
    op.drop_index("idx_outbox_available", table_name="outbox")
    op.drop_index("uq_outbox_idempotency_key", table_name="outbox")
    with op.batch_alter_table("outbox") as batch:
        batch.drop_column("lease_expires_at")
        batch.drop_column("lease_owner")
        batch.drop_column("available_at")
        batch.drop_column("idempotency_key")


def _migrate_legacy_interactions(bind: Any) -> None:
    if "reviews" not in set(inspect(bind).get_table_names()):
        return
    rows = bind.execute(sa.text("SELECT * FROM reviews ORDER BY created_at, review_id")).mappings()
    for row in rows:
        if str(row["status"]) not in {"pending", "approved", "executing", "provided"}:
            continue
        _insert_migrated_task(bind, dict(row))


def _insert_migrated_task(bind: Any, row: dict[str, Any]) -> None:
    review_id = str(row["review_id"])
    task_id = f"task_migrated_{_digest(review_id)}"
    contract_id = f"contract_{_digest(task_id)}"
    created_at = str(row.get("created_at") or _now())
    review_kind = str(row.get("review_kind") or "intent")
    legacy_status = str(row.get("status") or "pending")
    safe_pending = legacy_status == "pending"
    if safe_pending:
        status = "awaiting_clarification" if review_kind == "user_input" else "awaiting_review"
        reason = str(row.get("pending_reason") or "migrated pending interaction")
    else:
        status = "paused"
        reason = f"legacy interaction was {legacy_status}; manual safe disposition is required"
    goal = str(row.get("utterance") or "migrated task")
    terminal_outcome = None
    state_payload = {
        "schema_version": "v1",
        "task_id": task_id,
        "contract_id": contract_id,
        "status": status,
        "revision": 1,
        "created_at": created_at,
        "updated_at": created_at,
        "active_plan_revision_id": None,
        "current_step_id": str(row["step_id"]) if row.get("step_id") else None,
        "completed_step_ids": [],
        "pending_interaction_id": review_id if safe_pending else None,
        "pending_reason": reason,
        "next_wake_at": None,
        "suspended_status": "awaiting_clarification" if review_kind == "user_input" else "awaiting_review" if not safe_pending else None,
        "cancel_requested": False,
        "takeover_owner": None,
        "last_event": "legacy_interaction_migrated",
        "terminal_outcome": terminal_outcome,
    }
    contract_payload = {
        "schema_version": "v1",
        "contract_id": contract_id,
        "task_id": task_id,
        "goal": goal,
        "scope": [],
        "completion_conditions": [],
        "allowed_effects": [],
        "reality_boundaries": ["legacy interaction binding"],
        "version": 1,
        "created_at": created_at,
    }
    bind.execute(
        metadata.tables["goal_contracts"]
        .insert()
        .values(
            contract_id=contract_id,
            task_id=task_id,
            version=1,
            schema_version="v1",
            payload_json=json.dumps(contract_payload, ensure_ascii=False, separators=(",", ":")),
            created_at=created_at,
        )
    )
    bind.execute(
        metadata.tables["task_runs"]
        .insert()
        .values(
            task_id=task_id,
            contract_id=contract_id,
            status=status,
            revision=1,
            pending_interaction_id=review_id if safe_pending else None,
            schema_version="v1",
            payload_json=json.dumps(state_payload, ensure_ascii=False, separators=(",", ":")),
            created_at=created_at,
            updated_at=created_at,
        )
    )
    bind.execute(metadata.tables["task_leases"].insert().values(task_id=task_id, fencing_token=0, updated_at=created_at))
    _migrate_plan_payload(bind, row, task_id, created_at)


def _migrate_plan_payload(bind: Any, row: dict[str, Any], task_id: str, created_at: str) -> None:
    payload = _planning_snapshot(row.get("snapshot_payload"), row.get("plan_payload"))
    plan = payload.get("plan") if payload is not None else None
    if not _valid_plan(plan):
        if str(row.get("review_kind") or "intent") != "user_input":
            _pause_unrestorable_review(bind, task_id)
        return
    assert isinstance(payload, dict)
    assert isinstance(plan, dict)
    plan_id = str(plan.get("plan_id") or row.get("plan_id") or f"legacy_{_digest(task_id)}")
    plan_revision_id = f"planrev_{_digest(task_id + plan_id)}"
    bind.execute(
        metadata.tables["plan_revisions"]
        .insert()
        .values(
            plan_revision_id=plan_revision_id,
            task_id=task_id,
            revision=1,
            plan_id=plan_id,
            schema_version="v1",
            payload_json=json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
            created_at=created_at,
            reason="migrated from legacy pending interaction",
        )
    )
    bind.execute(
        metadata.tables["task_runs"]
        .update()
        .where(metadata.tables["task_runs"].c.task_id == task_id)
        .values(
            active_plan_revision_id=plan_revision_id,
            current_step_id=_migrate_steps(bind, task_id, plan_revision_id, plan, row.get("step_id"), created_at),
            payload_json=_updated_state_payload(bind, task_id, plan_revision_id, plan, row.get("step_id")),
        )
    )


def _planning_snapshot(snapshot_raw: object, plan_raw: object) -> dict[str, Any] | None:
    for raw in (snapshot_raw, plan_raw):
        if not raw:
            continue
        try:
            payload = json.loads(str(raw))
        except json.JSONDecodeError:
            continue
        if not isinstance(payload, dict):
            continue
        if isinstance(payload.get("plan"), dict):
            return payload
        if _valid_plan(payload):
            return {"snapshot_version": 0, "plan": payload}
    return None


def _valid_plan(plan: object) -> bool:
    if not isinstance(plan, dict):
        return False
    steps = plan.get("steps")
    return (
        bool(plan.get("schema_version") and plan.get("plan_id") and plan.get("utterance"))
        and isinstance(steps, list)
        and all(isinstance(step, dict) and step.get("id") and step.get("action") for step in steps)
    )


def _migrate_steps(
    bind: Any,
    task_id: str,
    plan_revision_id: str,
    plan: dict[str, Any],
    legacy_step_id: object,
    created_at: str,
) -> str | None:
    steps = plan.get("steps", [])
    step_ids = tuple(str(step["id"]) for step in steps)
    current_step_id = str(legacy_step_id) if legacy_step_id and str(legacy_step_id) in step_ids else (step_ids[0] if step_ids else None)
    for ordinal, step in enumerate(steps):
        step_id = str(step["id"])
        action = str(step["action"])
        bind.execute(
            metadata.tables["task_steps"]
            .insert()
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
    return current_step_id


def _updated_state_payload(bind: Any, task_id: str, plan_revision_id: str, plan: dict[str, Any], legacy_step_id: object) -> str:
    raw = bind.execute(sa.select(metadata.tables["task_runs"].c.payload_json).where(metadata.tables["task_runs"].c.task_id == task_id)).scalar_one()
    state = json.loads(str(raw))
    step_ids = tuple(str(step["id"]) for step in plan.get("steps", []))
    current = str(legacy_step_id) if legacy_step_id and str(legacy_step_id) in step_ids else (step_ids[0] if step_ids else None)
    state["active_plan_revision_id"] = plan_revision_id
    state["current_step_id"] = current
    return json.dumps(state, ensure_ascii=False, separators=(",", ":"))


def _pause_unrestorable_review(bind: Any, task_id: str) -> None:
    table = metadata.tables["task_runs"]
    raw = bind.execute(sa.select(table.c.payload_json).where(table.c.task_id == task_id)).scalar_one()
    state = json.loads(str(raw))
    state.update(
        {
            "status": "paused",
            "pending_interaction_id": None,
            "pending_reason": "legacy approval lacked a restorable plan; manual safe disposition is required",
            "suspended_status": "awaiting_review",
        }
    )
    bind.execute(
        table.update()
        .where(table.c.task_id == task_id)
        .values(
            status="paused",
            pending_interaction_id=None,
            payload_json=json.dumps(state, ensure_ascii=False, separators=(",", ":")),
        )
    )


def _create_legacy_review_tables(bind: Any) -> None:
    tables = set(inspect(bind).get_table_names())
    if "reviews" not in tables:
        op.create_table(
            "reviews",
            sa.Column("review_id", sa.Text(), primary_key=True),
            sa.Column("status", sa.Text(), nullable=False),
            sa.Column("review_kind", sa.Text(), nullable=False),
            sa.Column("plan_id", sa.Text()),
            sa.Column("step_id", sa.Text()),
            sa.Column("utterance", sa.Text(), nullable=False),
            sa.Column("intent_payload", sa.Text(), nullable=False),
            sa.Column("review_payload", sa.Text(), nullable=False),
            sa.Column("plan_payload", sa.Text()),
            sa.Column("snapshot_payload", sa.Text()),
            sa.Column("supplemental_input", sa.Text()),
            sa.Column("pending_reason", sa.Text()),
            sa.Column("created_at", sa.Text(), nullable=False),
            sa.Column("expires_at", sa.Text()),
            sa.Column("version", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("payload", sa.Text(), nullable=False),
        )
        op.create_index("idx_reviews_status_created_at", "reviews", ["status", "created_at"])
        op.create_index("idx_reviews_lookup_kind_step", "reviews", ["review_kind", "plan_id", "step_id"])
    if "review_events" not in tables:
        op.create_table(
            "review_events",
            sa.Column("event_id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("review_id", sa.Text()),
            sa.Column("event_type", sa.Text()),
            sa.Column("created_at", sa.Text()),
            sa.Column("payload", sa.Text(), nullable=False),
            sqlite_autoincrement=True,
        )
        op.create_index("idx_review_events_review_id_event_id", "review_events", ["review_id", "event_id"])


def _digest(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()[:20]


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")
