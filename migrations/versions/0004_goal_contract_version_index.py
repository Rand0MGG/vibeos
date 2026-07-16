"""repair legacy goal contract uniqueness

Revision ID: 0004_goal_contract_version_index
Revises: 0003_repair_durable_task_semantics
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.engine import Connection

revision = "0004_goal_contract_version_index"
down_revision = "0003_repair_durable_task_semantics"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if _has_legacy_task_unique(bind):
        bind.execute(sa.text("PRAGMA defer_foreign_keys=ON"))
        with op.batch_alter_table("goal_contracts", recreate="always", copy_from=_goal_contracts_table()) as batch:
            batch.alter_column("task_id", existing_type=sa.String(length=240), nullable=False)
    if not _has_version_unique(bind):
        op.create_index(
            "uq_goal_contracts_task_version",
            "goal_contracts",
            ["task_id", "version"],
            unique=True,
        )


def downgrade() -> None:
    # This compatibility repair is intentionally retained: restoring task_id-only
    # uniqueness would make valid versioned contracts unreadable.
    pass


def _has_legacy_task_unique(bind: Connection) -> bool:
    return ("task_id",) in _unique_column_sets(bind)


def _has_version_unique(bind: Connection) -> bool:
    return ("task_id", "version") in _unique_column_sets(bind)


def _unique_column_sets(bind: Connection) -> set[tuple[str, ...]]:
    result: set[tuple[str, ...]] = set()
    for row in bind.exec_driver_sql("PRAGMA index_list('goal_contracts')"):
        if not bool(row[2]):
            continue
        name = str(row[1]).replace('"', '""')
        columns = tuple(str(item[2]) for item in bind.exec_driver_sql(f'PRAGMA index_info("{name}")'))
        result.add(columns)
    return result


def _goal_contracts_table() -> sa.Table:
    """Return the immutable 0004 target shape, independent of application code."""
    metadata = sa.MetaData()
    table = sa.Table(
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
    sa.Index("idx_goal_contracts_task_created", table.c.task_id, table.c.created_at)
    return table
