"""Create the Goal 01 authoritative foundation schema.

Revision ID: 0001_core_foundation
Revises: None
Create Date: 2026-07-15
"""

from __future__ import annotations

from alembic import op
from sqlalchemy import inspect, text

from vibeos.core.adapters.metadata import metadata

revision = "0001_core_foundation"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    metadata.create_all(bind=bind, checkfirst=True)
    inspector = inspect(bind)
    event_columns = {column["name"] for column in inspector.get_columns("review_events")}
    for name, declaration in (
        ("review_id", "TEXT"),
        ("event_type", "TEXT"),
        ("created_at", "TEXT"),
    ):
        if name not in event_columns:
            op.execute(text(f"ALTER TABLE review_events ADD COLUMN {name} {declaration}"))
    indexes = {index["name"] for index in inspect(bind).get_indexes("review_events")}
    if "idx_review_events_review_id_event_id" not in indexes:
        op.create_index("idx_review_events_review_id_event_id", "review_events", ["review_id", "event_id"])
    op.execute(text("INSERT OR IGNORE INTO schema_migrations (version, applied_at) VALUES (1, :applied_at)").bindparams(applied_at="2026-07-15T00:00:00.000Z"))


def downgrade() -> None:
    # Review tables predate Alembic and may contain live approvals. A safe
    # rollback never drops them or their legacy migration marker.
    bind = op.get_bind()
    existing = set(inspect(bind).get_table_names())
    for table_name in ("outbox", "domain_events", "current_state"):
        if table_name in existing:
            op.drop_table(table_name)
