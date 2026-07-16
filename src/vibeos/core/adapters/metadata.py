from __future__ import annotations

from sqlalchemy import (
    CheckConstraint,
    Column,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    String,
    Table,
    Text,
)

metadata = MetaData()

schema_migrations = Table(
    "schema_migrations",
    metadata,
    Column("version", Integer, primary_key=True),
    Column("applied_at", Text, nullable=False),
)

current_state = Table(
    "current_state",
    metadata,
    Column("state_key", String(300), primary_key=True),
    Column("aggregate_type", String(80), nullable=False),
    Column("aggregate_id", String(240), nullable=False),
    Column("state_version", Integer, nullable=False),
    Column("status", String(40), nullable=False),
    Column("schema_version", String(20), nullable=False),
    Column("payload_json", Text, nullable=False),
    Column("updated_at", String(40), nullable=False),
    CheckConstraint("state_version >= 1", name="ck_current_state_version_positive"),
)
Index("idx_current_state_aggregate", current_state.c.aggregate_type, current_state.c.aggregate_id)

domain_events = Table(
    "domain_events",
    metadata,
    Column("event_sequence", Integer, primary_key=True, autoincrement=True),
    Column("event_id", String(240), nullable=False, unique=True),
    Column("state_key", String(300), ForeignKey("current_state.state_key", ondelete="CASCADE"), nullable=False),
    Column("aggregate_type", String(80), nullable=False),
    Column("aggregate_id", String(240), nullable=False),
    Column("event_type", String(120), nullable=False),
    Column("schema_version", String(20), nullable=False),
    Column("occurred_at", String(40), nullable=False),
    Column("payload_json", Text, nullable=False),
    sqlite_autoincrement=True,
)
Index("idx_domain_events_aggregate_sequence", domain_events.c.aggregate_type, domain_events.c.aggregate_id, domain_events.c.event_sequence)

outbox = Table(
    "outbox",
    metadata,
    Column("message_id", String(240), primary_key=True),
    Column("state_key", String(300), ForeignKey("current_state.state_key", ondelete="CASCADE"), nullable=False),
    Column("aggregate_id", String(240), nullable=False),
    Column("topic", String(200), nullable=False),
    Column("schema_version", String(20), nullable=False),
    Column("occurred_at", String(40), nullable=False),
    Column("payload_json", Text, nullable=False),
    Column("published_at", String(40)),
    Column("attempts", Integer, nullable=False, server_default="0"),
    Column("idempotency_key", String(320)),
    Column("available_at", String(40)),
    Column("lease_owner", String(240)),
    Column("lease_expires_at", String(40)),
    CheckConstraint("attempts >= 0", name="ck_outbox_attempts_nonnegative"),
)
Index("idx_outbox_unpublished", outbox.c.published_at, outbox.c.occurred_at)
Index("uq_outbox_idempotency_key", outbox.c.idempotency_key, unique=True)
Index("idx_outbox_available", outbox.c.published_at, outbox.c.available_at, outbox.c.lease_expires_at)

goal_contracts = Table(
    "goal_contracts",
    metadata,
    Column("contract_id", String(240), primary_key=True),
    Column("task_id", String(240), nullable=False),
    Column("version", Integer, nullable=False),
    Column("schema_version", String(20), nullable=False),
    Column("payload_json", Text, nullable=False),
    Column("created_at", String(40), nullable=False),
    CheckConstraint("version >= 1", name="ck_goal_contract_version_positive"),
)
Index("uq_goal_contracts_task_version", goal_contracts.c.task_id, goal_contracts.c.version, unique=True)
Index("idx_goal_contracts_task_created", goal_contracts.c.task_id, goal_contracts.c.created_at)

task_runs = Table(
    "task_runs",
    metadata,
    Column("task_id", String(240), primary_key=True),
    Column("contract_id", String(240), ForeignKey("goal_contracts.contract_id", ondelete="RESTRICT"), nullable=False),
    Column("status", String(40), nullable=False),
    Column("revision", Integer, nullable=False),
    Column("active_plan_revision_id", String(240)),
    Column("current_step_id", String(240)),
    Column("pending_interaction_id", String(240), unique=True),
    Column("next_wake_at", String(40)),
    Column("schema_version", String(20), nullable=False),
    Column("payload_json", Text, nullable=False),
    Column("created_at", String(40), nullable=False),
    Column("updated_at", String(40), nullable=False),
    CheckConstraint("revision >= 0", name="ck_task_runs_revision_nonnegative"),
)
Index("idx_task_runs_status_wake", task_runs.c.status, task_runs.c.next_wake_at)
Index("idx_task_runs_updated", task_runs.c.updated_at)

plan_revisions = Table(
    "plan_revisions",
    metadata,
    Column("plan_revision_id", String(240), primary_key=True),
    Column("task_id", String(240), ForeignKey("task_runs.task_id", ondelete="CASCADE"), nullable=False),
    Column("revision", Integer, nullable=False),
    Column("plan_id", String(240), nullable=False),
    Column("schema_version", String(20), nullable=False),
    Column("payload_json", Text, nullable=False),
    Column("created_at", String(40), nullable=False),
    Column("reason", Text, nullable=False),
    CheckConstraint("revision >= 1", name="ck_plan_revision_positive"),
)
Index("uq_plan_revisions_task_revision", plan_revisions.c.task_id, plan_revisions.c.revision, unique=True)

task_steps = Table(
    "task_steps",
    metadata,
    Column("step_key", String(500), primary_key=True),
    Column("step_id", String(240), nullable=False),
    Column("task_id", String(240), ForeignKey("task_runs.task_id", ondelete="CASCADE"), nullable=False),
    Column("plan_revision_id", String(240), ForeignKey("plan_revisions.plan_revision_id", ondelete="CASCADE"), nullable=False),
    Column("ordinal", Integer, nullable=False),
    Column("action", String(120), nullable=False),
    Column("capability_id", String(120), nullable=False),
    Column("status", String(40), nullable=False),
    Column("idempotency_key", String(320), nullable=False, unique=True),
    Column("schema_version", String(20), nullable=False),
    Column("payload_json", Text, nullable=False),
    Column("created_at", String(40), nullable=False),
    Column("updated_at", String(40), nullable=False),
    CheckConstraint("ordinal >= 0", name="ck_task_steps_ordinal_nonnegative"),
)
Index("idx_task_steps_task_ordinal", task_steps.c.task_id, task_steps.c.ordinal)

task_attempts = Table(
    "task_attempts",
    metadata,
    Column("attempt_id", String(240), primary_key=True),
    Column("task_id", String(240), ForeignKey("task_runs.task_id", ondelete="CASCADE"), nullable=False),
    Column("step_id", String(240)),
    Column("attempt_number", Integer, nullable=False),
    Column("classification", String(80), nullable=False),
    Column("status", String(40), nullable=False),
    Column("schema_version", String(20), nullable=False),
    Column("detail_json", Text, nullable=False),
    Column("started_at", String(40), nullable=False),
    Column("finished_at", String(40)),
    CheckConstraint("attempt_number >= 1", name="ck_task_attempt_number_positive"),
)
Index("idx_task_attempts_task_step", task_attempts.c.task_id, task_attempts.c.step_id, task_attempts.c.attempt_number)

wait_conditions = Table(
    "wait_conditions",
    metadata,
    Column("wait_id", String(240), primary_key=True),
    Column("task_id", String(240), ForeignKey("task_runs.task_id", ondelete="CASCADE"), nullable=False),
    Column("kind", String(80), nullable=False),
    Column("due_at", String(40)),
    Column("event_key", String(320)),
    Column("status", String(40), nullable=False),
    Column("schema_version", String(20), nullable=False),
    Column("created_at", String(40), nullable=False),
    Column("satisfied_at", String(40)),
)
Index("idx_wait_conditions_due", wait_conditions.c.status, wait_conditions.c.due_at)
Index("idx_wait_conditions_event", wait_conditions.c.status, wait_conditions.c.event_key)

action_proposals = Table(
    "action_proposals",
    metadata,
    Column("proposal_id", String(240), primary_key=True),
    Column("task_id", String(240), ForeignKey("task_runs.task_id", ondelete="CASCADE"), nullable=False),
    Column("step_id", String(240), nullable=False),
    Column("attempt_id", String(240), ForeignKey("task_attempts.attempt_id", ondelete="CASCADE"), nullable=False),
    Column("idempotency_key", String(320), nullable=False, unique=True),
    Column("action", String(120), nullable=False),
    Column("capability_id", String(120), nullable=False),
    Column("status", String(40), nullable=False),
    Column("schema_version", String(20), nullable=False),
    Column("request_json", Text, nullable=False),
    Column("created_at", String(40), nullable=False),
    Column("updated_at", String(40), nullable=False),
)
Index("idx_action_proposals_reconcile", action_proposals.c.status, action_proposals.c.updated_at)

task_action_receipts = Table(
    "task_action_receipts",
    metadata,
    Column("receipt_id", String(240), primary_key=True),
    Column("task_id", String(240), ForeignKey("task_runs.task_id", ondelete="CASCADE"), nullable=False),
    Column("step_id", String(240), nullable=False),
    Column("proposal_id", String(240), ForeignKey("action_proposals.proposal_id", ondelete="RESTRICT"), nullable=False, unique=True),
    Column("idempotency_key", String(320), nullable=False, unique=True),
    Column("status", String(40), nullable=False),
    Column("adapter", String(240)),
    Column("external_reference", String(500)),
    Column("schema_version", String(20), nullable=False),
    Column("result_json", Text, nullable=False),
    Column("occurred_at", String(40), nullable=False),
)

evidence_bundles = Table(
    "evidence_bundles",
    metadata,
    Column("evidence_id", String(240), primary_key=True),
    Column("task_id", String(240), ForeignKey("task_runs.task_id", ondelete="CASCADE"), nullable=False),
    Column("step_id", String(240)),
    Column("receipt_id", String(240), ForeignKey("task_action_receipts.receipt_id", ondelete="SET NULL")),
    Column("status", String(40), nullable=False),
    Column("summary", Text, nullable=False),
    Column("schema_version", String(20), nullable=False),
    Column("payload_json", Text, nullable=False),
    Column("observed_at", String(40), nullable=False),
)
Index("idx_evidence_task_observed", evidence_bundles.c.task_id, evidence_bundles.c.observed_at)

terminal_outcomes = Table(
    "terminal_outcomes",
    metadata,
    Column("task_id", String(240), ForeignKey("task_runs.task_id", ondelete="CASCADE"), primary_key=True),
    Column("status", String(40), nullable=False),
    Column("reason", Text, nullable=False),
    Column("schema_version", String(20), nullable=False),
    Column("evidence_ids_json", Text, nullable=False),
    Column("finished_at", String(40), nullable=False),
)

task_leases = Table(
    "task_leases",
    metadata,
    Column("task_id", String(240), ForeignKey("task_runs.task_id", ondelete="CASCADE"), primary_key=True),
    Column("owner", String(240)),
    Column("expires_at", String(40)),
    Column("fencing_token", Integer, nullable=False, server_default="0"),
    Column("updated_at", String(40), nullable=False),
    CheckConstraint("fencing_token >= 0", name="ck_task_lease_fencing_nonnegative"),
)
Index("idx_task_leases_expiry", task_leases.c.expires_at)

outbox_deliveries = Table(
    "outbox_deliveries",
    metadata,
    Column("message_id", String(240), ForeignKey("outbox.message_id", ondelete="CASCADE"), primary_key=True),
    Column("consumer", String(120), primary_key=True),
    Column("delivered_at", String(40), nullable=False),
    Column("result_json", Text, nullable=False),
)
