from __future__ import annotations

import os
import shutil
import sqlite3
from collections.abc import Callable
from pathlib import Path
from uuid import uuid4

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.engine import URL
from sqlalchemy.pool import NullPool

from .metadata import metadata


class DatabaseConfigurationError(ValueError):
    """The configured authoritative database is outside the supported boundary."""


class DatabaseMigrationError(RuntimeError):
    """Alembic could not upgrade the database and the pre-upgrade state was restored."""


class CoreDatabase:
    def __init__(self, path: Path, *, busy_timeout_ms: int = 5_000) -> None:
        self.path = path.expanduser().resolve()
        self.busy_timeout_ms = busy_timeout_ms
        _require_local_filesystem(self.path)
        url = URL.create("sqlite+pysqlite", database=str(self.path))
        self.engine = create_engine(
            url,
            future=True,
            poolclass=NullPool,
            connect_args={"check_same_thread": False, "timeout": busy_timeout_ms / 1_000},
        )
        event.listen(self.engine, "connect", self._configure_connection)

    def upgrade(self) -> None:
        """Upgrade with a same-directory backup so failed DDL cannot leave half-state."""

        self._migrate_with_backup(self._run_alembic_upgrade)

    def downgrade(self, revision: str = "base") -> None:
        """Safely roll back foundation tables while preserving legacy reviews."""

        self._migrate_with_backup(lambda: self._run_alembic_downgrade(revision))

    def _migrate_with_backup(self, operation: Callable[[], None]) -> None:

        self.path.parent.mkdir(parents=True, exist_ok=True)
        existed = self.path.exists()
        backup = self.path.with_name(f".{self.path.name}.migration-{uuid4().hex}.bak")
        if existed:
            with sqlite3.connect(self.path) as connection:
                connection.execute("PRAGMA wal_checkpoint(FULL)")
            shutil.copy2(self.path, backup)
        self.engine.dispose()
        try:
            operation()
        except Exception as exc:
            self.engine.dispose()
            if existed and backup.exists():
                os.replace(backup, self.path)
            elif not existed:
                _remove_sqlite_files(self.path)
            raise DatabaseMigrationError(f"database migration failed for {self.path}") from exc
        finally:
            if backup.exists():
                backup.unlink()

    def compatibility_connection(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, check_same_thread=False, timeout=self.busy_timeout_ms / 1_000)
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=FULL")
        connection.execute(f"PRAGMA busy_timeout={self.busy_timeout_ms}")
        return connection

    def health(self) -> dict[str, str | int | bool]:
        expected_revisions = set(ScriptDirectory.from_config(self._alembic_config()).get_heads())
        with self.engine.connect() as connection:
            connection.execute(text("SELECT 1"))
            journal_mode = str(connection.execute(text("PRAGMA journal_mode")).scalar_one()).lower()
            foreign_keys = int(connection.execute(text("PRAGMA foreign_keys")).scalar_one())
            busy_timeout = int(connection.execute(text("PRAGMA busy_timeout")).scalar_one())
            tables = set(inspect(connection).get_table_names())
            current_revisions = set(connection.execute(text("SELECT version_num FROM alembic_version")).scalars()) if "alembic_version" in tables else set()
        required_tables = set(metadata.tables) | {"alembic_version"}
        missing_tables = required_tables - tables
        schema_ready = not missing_tables and current_revisions == expected_revisions
        return {
            "ready": journal_mode == "wal" and foreign_keys == 1 and schema_ready,
            "journal_mode": journal_mode,
            "foreign_keys": foreign_keys,
            "busy_timeout_ms": busy_timeout,
            "schema_ready": schema_ready,
            "alembic_revision": ",".join(sorted(current_revisions)),
            "expected_alembic_revision": ",".join(sorted(expected_revisions)),
            "missing_tables": ",".join(sorted(missing_tables)),
            "path": str(self.path),
        }

    def dispose(self) -> None:
        self.engine.dispose()

    def _run_alembic_upgrade(self) -> None:
        command.upgrade(self._alembic_config(), "head")

    def _run_alembic_downgrade(self, revision: str) -> None:
        command.downgrade(self._alembic_config(), revision)

    def _alembic_config(self) -> Config:
        root = Path(__file__).resolve().parents[4]
        config = Config(str(root / "alembic.ini"))
        config.set_main_option("script_location", str(root / "migrations"))
        config.set_main_option("sqlalchemy.url", self.engine.url.render_as_string(hide_password=False).replace("%", "%%"))
        return config

    def _configure_connection(self, dbapi_connection: object, _connection_record: object) -> None:
        if not isinstance(dbapi_connection, sqlite3.Connection):
            raise TypeError("CoreDatabase requires the sqlite3 DB-API driver")
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA synchronous=FULL")
            cursor.execute(f"PRAGMA busy_timeout={self.busy_timeout_ms}")
        finally:
            cursor.close()


def _remove_sqlite_files(path: Path) -> None:
    for candidate in (path, path.with_name(path.name + "-wal"), path.with_name(path.name + "-shm")):
        if candidate.exists():
            candidate.unlink()


def _require_local_filesystem(path: Path) -> None:
    raw = str(path)
    if raw.startswith(("\\\\", "//")) or "://" in raw:
        raise DatabaseConfigurationError("the authoritative SQLite database must be on a local filesystem")
    if os.name != "posix":
        return
    filesystem = _filesystem_type(path.parent)
    if filesystem in {"nfs", "nfs4", "cifs", "smbfs", "sshfs", "fuse.sshfs", "ceph", "glusterfs"}:
        raise DatabaseConfigurationError(f"unsupported network filesystem for SQLite: {filesystem}")


def _filesystem_type(path: Path) -> str | None:
    mounts = Path("/proc/mounts")
    if not mounts.exists():
        return None
    resolved = str(path.resolve())
    best: tuple[int, str] | None = None
    try:
        lines = mounts.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    for line in lines:
        parts = line.split()
        if len(parts) < 3:
            continue
        mountpoint = parts[1].replace("\\040", " ")
        if resolved == mountpoint or resolved.startswith(mountpoint.rstrip("/") + "/"):
            candidate = (len(mountpoint), parts[2])
            if best is None or candidate[0] > best[0]:
                best = candidate
    return best[1] if best is not None else None


FaultInjector = Callable[[str], None]
