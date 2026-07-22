"""Create the Goal 01 authoritative foundation schema.

Revision ID: 0001_core_foundation
Revises: None
Create Date: 2026-07-15
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect, text


revision = "0001_core_foundation"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    legacy = _goal01_metadata()
    legacy.create_all(bind=bind, checkfirst=True)
    event_columns = {column["name"] for column in inspect(bind).get_columns("review_events")}
    for name, declaration in (("review_id", "TEXT"), ("event_type", "TEXT"), ("created_at", "TEXT")):
        if name not in event_columns:
            op.execute(text(f"ALTER TABLE review_events ADD COLUMN {name} {declaration}"))
    indexes = {index["name"] for index in inspect(bind).get_indexes("review_events")}
    if "idx_review_events_review_id_event_id" not in indexes:
        op.create_index("idx_review_events_review_id_event_id", "review_events", ["review_id", "event_id"])
    op.execute(text("INSERT OR IGNORE INTO schema_migrations (version, applied_at) VALUES (1, :applied_at)").bindparams(applied_at="2026-07-15T00:00:00.000Z"))


def downgrade() -> None:
    bind = op.get_bind()
    existing = set(inspect(bind).get_table_names())
    for table_name in ("outbox", "domain_events", "current_state"):
        if table_name in existing:
            op.drop_table(table_name)


def _goal01_metadata() -> sa.MetaData:
    metadata = sa.MetaData()
    sa.Table(
        "schema_migrations",
        metadata,
        sa.Column("version", sa.Integer, primary_key=True),
        sa.Column("applied_at", sa.Text, nullable=False),
    )
    reviews = sa.Table(
        "reviews",
        metadata,
        sa.Column("review_id", sa.Text, primary_key=True),
        sa.Column("status", sa.Text, nullable=False),
        sa.Column("review_kind", sa.Text, nullable=False),
        sa.Column("plan_id", sa.Text),
        sa.Column("step_id", sa.Text),
        sa.Column("utterance", sa.Text, nullable=False),
        sa.Column("intent_payload", sa.Text, nullable=False),
        sa.Column("review_payload", sa.Text, nullable=False),
        sa.Column("plan_payload", sa.Text),
        sa.Column("snapshot_payload", sa.Text),
        sa.Column("supplemental_input", sa.Text),
        sa.Column("pending_reason", sa.Text),
        sa.Column("created_at", sa.Text, nullable=False),
        sa.Column("expires_at", sa.Text),
        sa.Column("version", sa.Integer, nullable=False, server_default="0"),
        sa.Column("payload", sa.Text, nullable=False),
    )
    sa.Index("idx_reviews_status_created_at", reviews.c.status, reviews.c.created_at)
    sa.Index("idx_reviews_lookup_kind_step", reviews.c.review_kind, reviews.c.plan_id, reviews.c.step_id)
    review_events = sa.Table(
        "review_events",
        metadata,
        sa.Column("event_id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("review_id", sa.Text),
        sa.Column("event_type", sa.Text),
        sa.Column("created_at", sa.Text),
        sa.Column("payload", sa.Text, nullable=False),
        sqlite_autoincrement=True,
    )
    sa.Index("idx_review_events_review_id_event_id", review_events.c.review_id, review_events.c.event_id)
    current_state = sa.Table(
        "current_state",
        metadata,
        sa.Column("state_key", sa.String(300), primary_key=True),
        sa.Column("aggregate_type", sa.String(80), nullable=False),
        sa.Column("aggregate_id", sa.String(240), nullable=False),
        sa.Column("state_version", sa.Integer, nullable=False),
        sa.Column("status", sa.String(40), nullable=False),
        sa.Column("schema_version", sa.String(20), nullable=False),
        sa.Column("payload_json", sa.Text, nullable=False),
        sa.Column("updated_at", sa.String(40), nullable=False),
        sa.CheckConstraint("state_version >= 1", name="ck_current_state_version_positive"),
    )
    sa.Index("idx_current_state_aggregate", current_state.c.aggregate_type, current_state.c.aggregate_id)
    domain_events = sa.Table(
        "domain_events",
        metadata,
        sa.Column("event_sequence", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("event_id", sa.String(240), nullable=False, unique=True),
        sa.Column("state_key", sa.String(300), sa.ForeignKey("current_state.state_key", ondelete="CASCADE"), nullable=False),
        sa.Column("aggregate_type", sa.String(80), nullable=False),
        sa.Column("aggregate_id", sa.String(240), nullable=False),
        sa.Column("event_type", sa.String(120), nullable=False),
        sa.Column("schema_version", sa.String(20), nullable=False),
        sa.Column("occurred_at", sa.String(40), nullable=False),
        sa.Column("payload_json", sa.Text, nullable=False),
        sqlite_autoincrement=True,
    )
    sa.Index("idx_domain_events_aggregate_sequence", domain_events.c.aggregate_type, domain_events.c.aggregate_id, domain_events.c.event_sequence)
    outbox = sa.Table(
        "outbox",
        metadata,
        sa.Column("message_id", sa.String(240), primary_key=True),
        sa.Column("state_key", sa.String(300), sa.ForeignKey("current_state.state_key", ondelete="CASCADE"), nullable=False),
        sa.Column("aggregate_id", sa.String(240), nullable=False),
        sa.Column("topic", sa.String(200), nullable=False),
        sa.Column("schema_version", sa.String(20), nullable=False),
        sa.Column("occurred_at", sa.String(40), nullable=False),
        sa.Column("payload_json", sa.Text, nullable=False),
        sa.Column("published_at", sa.String(40)),
        sa.Column("attempts", sa.Integer, nullable=False, server_default="0"),
        sa.CheckConstraint("attempts >= 0", name="ck_outbox_attempts_nonnegative"),
    )
    sa.Index("idx_outbox_unpublished", outbox.c.published_at, outbox.c.occurred_at)
    return metadata
