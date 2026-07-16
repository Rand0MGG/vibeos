from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import or_, select, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from .database import CoreDatabase
from .metadata import outbox, outbox_deliveries


@dataclass(frozen=True)
class OutboxRecord:
    message_id: str
    aggregate_id: str
    topic: str
    payload_json: str


class SqliteOutboxRepository:
    def __init__(self, database: CoreDatabase) -> None:
        self.database = database

    def claim(
        self,
        *,
        owner: str,
        now: str,
        expires_at: str,
        limit: int = 100,
    ) -> tuple[OutboxRecord, ...]:
        candidates = (
            select(outbox.c.message_id)
            .where(
                outbox.c.published_at.is_(None),
                or_(outbox.c.available_at.is_(None), outbox.c.available_at <= now),
                or_(outbox.c.lease_owner.is_(None), outbox.c.lease_expires_at <= now, outbox.c.lease_owner == owner),
            )
            .order_by(outbox.c.occurred_at)
            .limit(limit)
        )
        claimed: list[OutboxRecord] = []
        with self.database.engine.begin() as connection:
            for message_id in connection.execute(candidates).scalars():
                changed = connection.execute(
                    update(outbox)
                    .where(
                        outbox.c.message_id == message_id,
                        outbox.c.published_at.is_(None),
                        or_(outbox.c.lease_owner.is_(None), outbox.c.lease_expires_at <= now, outbox.c.lease_owner == owner),
                    )
                    .values(lease_owner=owner, lease_expires_at=expires_at, attempts=outbox.c.attempts + 1)
                ).rowcount
                if changed != 1:
                    continue
                row = connection.execute(select(outbox).where(outbox.c.message_id == message_id)).mappings().one()
                claimed.append(OutboxRecord(str(row["message_id"]), str(row["aggregate_id"]), str(row["topic"]), str(row["payload_json"])))
        return tuple(claimed)

    def delivered(self, message_id: str, consumer: str, delivered_at: str, result_json: str) -> bool:
        statement = sqlite_insert(outbox_deliveries).values(
            message_id=message_id,
            consumer=consumer,
            delivered_at=delivered_at,
            result_json=result_json,
        )
        with self.database.engine.begin() as connection:
            changed = connection.execute(statement.on_conflict_do_nothing()).rowcount
            if changed == 1:
                connection.execute(
                    update(outbox).where(outbox.c.message_id == message_id).values(published_at=delivered_at, lease_owner=None, lease_expires_at=None)
                )
        return int(changed or 0) == 1
