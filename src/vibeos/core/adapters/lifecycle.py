from __future__ import annotations

import asyncio
from collections.abc import Callable

from .database import CoreDatabase


class DatabaseLifecycleComponent:
    name = "database"

    def __init__(self, database: CoreDatabase, *, after_ready: Callable[[], None] | None = None) -> None:
        self._database = database
        self._after_ready = after_ready
        self._status = "stopped"
        self._message = "database has not been checked"

    async def start(self) -> None:
        self._status = "starting"
        self._message = "applying authoritative database migrations"
        try:
            await asyncio.to_thread(self._database.upgrade)
            health = await asyncio.to_thread(self._database.health)
            if not bool(health["ready"]):
                missing = str(health["missing_tables"]) or "none"
                revision = str(health["alembic_revision"]) or "none"
                expected = str(health["expected_alembic_revision"]) or "none"
                raise RuntimeError(f"database schema is not ready: revision={revision}, expected={expected}, missing_tables={missing}")
            if self._after_ready is not None:
                await asyncio.to_thread(self._after_ready)
        except Exception:
            self._status = "failed"
            self._message = "database migration, schema validation, or compatibility binding failed"
            raise
        self._status = "ready"
        self._message = f"schema {health['alembic_revision']} ready; WAL, foreign keys and {health['busy_timeout_ms']}ms busy timeout enabled"

    async def stop(self) -> None:
        await asyncio.to_thread(self._database.dispose)
        self._status = "stopped"
        self._message = "database engine disposed"

    def health_status(self) -> tuple[str, str]:
        return self._status, self._message
