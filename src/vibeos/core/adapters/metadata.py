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

reviews = Table(
    "reviews",
    metadata,
    Column("review_id", Text, primary_key=True),
    Column("status", Text, nullable=False),
    Column("review_kind", Text, nullable=False),
    Column("plan_id", Text),
    Column("step_id", Text),
    Column("utterance", Text, nullable=False),
    Column("intent_payload", Text, nullable=False),
    Column("review_payload", Text, nullable=False),
    Column("plan_payload", Text),
    Column("snapshot_payload", Text),
    Column("supplemental_input", Text),
    Column("pending_reason", Text),
    Column("created_at", Text, nullable=False),
    Column("expires_at", Text),
    Column("version", Integer, nullable=False, server_default="0"),
    Column("payload", Text, nullable=False),
)
Index("idx_reviews_status_created_at", reviews.c.status, reviews.c.created_at)
Index("idx_reviews_lookup_kind_step", reviews.c.review_kind, reviews.c.plan_id, reviews.c.step_id)

review_events = Table(
    "review_events",
    metadata,
    Column("event_id", Integer, primary_key=True, autoincrement=True),
    Column("review_id", Text),
    Column("event_type", Text),
    Column("created_at", Text),
    Column("payload", Text, nullable=False),
    sqlite_autoincrement=True,
)
Index("idx_review_events_review_id_event_id", review_events.c.review_id, review_events.c.event_id)

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
    CheckConstraint("attempts >= 0", name="ck_outbox_attempts_nonnegative"),
)
Index("idx_outbox_unpublished", outbox.c.published_at, outbox.c.occurred_at)
