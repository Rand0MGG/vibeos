from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest
from alembic import command

from vibeos.core.adapters.database import CoreDatabase, DatabaseMigrationError


HEAD_REVISION = "0004_goal_contract_version_index"
MIGRATION_FILES = (
    "0001_core_foundation.py",
    "0002_durable_task_engine.py",
    "0003_repair_durable_task_semantics.py",
    "0004_goal_contract_version_index.py",
)


def test_historical_migrations_do_not_import_runtime_metadata() -> None:
    root = Path(__file__).resolve().parents[1] / "migrations" / "versions"
    for name in MIGRATION_FILES:
        source = (root / name).read_text(encoding="utf-8")
        assert "vibeos.core.adapters.metadata" not in source
        assert "from vibeos" not in source
        assert "import vibeos" not in source


def test_empty_goal01_and_interrupted_upgrade_paths_converge(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    empty = CoreDatabase(tmp_path / "empty.sqlite3")
    empty.upgrade()

    direct = _goal01_database(tmp_path / "direct.sqlite3")
    direct.upgrade()

    interrupted = _goal01_database(tmp_path / "interrupted.sqlite3")
    before_failure = _database_snapshot(interrupted.path)
    real_upgrade = interrupted._run_alembic_upgrade
    config = interrupted._alembic_config()

    def fail_after_durable_schema() -> None:
        command.upgrade(config, "0002_durable_task_engine")
        raise RuntimeError("injected failure after 0002")

    monkeypatch.setattr(interrupted, "_run_alembic_upgrade", fail_after_durable_schema)
    with pytest.raises(DatabaseMigrationError, match="database migration failed"):
        interrupted.upgrade()
    assert _database_snapshot(interrupted.path) == before_failure

    monkeypatch.setattr(interrupted, "_run_alembic_upgrade", real_upgrade)
    interrupted.upgrade()

    assert _revision(empty.path) == HEAD_REVISION
    assert _revision(direct.path) == HEAD_REVISION
    assert _revision(interrupted.path) == HEAD_REVISION
    assert _schema_snapshot(empty.path) == _schema_snapshot(direct.path)
    assert _schema_snapshot(direct.path) == _schema_snapshot(interrupted.path)
    assert _durable_data_snapshot(direct.path) == _durable_data_snapshot(interrupted.path)

    with sqlite3.connect(direct.path) as connection:
        tables = _table_names(connection)
        task = connection.execute("SELECT status, pending_interaction_id, current_step_id FROM task_runs").fetchone()
        step = connection.execute("SELECT step_id, capability_id, status FROM task_steps").fetchone()
    assert {"reviews", "review_events"}.isdisjoint(tables)
    assert task == ("awaiting_review", "review_goal01", "step_goal01")
    assert step == ("step_goal01", "window.close", "pending")


def _goal01_database(path: Path) -> CoreDatabase:
    database = CoreDatabase(path)
    command.upgrade(database._alembic_config(), "0001_core_foundation")
    plan = {
        "schema_version": "v0.3",
        "plan_id": "plan_goal01",
        "utterance": "close Firefox",
        "selected_route_id": "window_close_route",
        "routes": [{"id": "window_close_route", "score": 1.0, "domain_id": "windows"}],
        "steps": [
            {
                "id": "step_goal01",
                "action": "window.close",
                "capability_id": "window.close",
                "target": {"name": "Firefox"},
                "risk_level": "L2",
            }
        ],
    }
    with sqlite3.connect(path) as connection:
        connection.execute(
            "INSERT INTO reviews VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "review_goal01",
                "pending",
                "intent",
                plan["plan_id"],
                "step_goal01",
                plan["utterance"],
                "{}",
                "{}",
                json.dumps(plan, sort_keys=True),
                json.dumps({"snapshot_version": 0, "plan": plan}, sort_keys=True),
                None,
                "explicit approval required",
                "2099-01-01T00:00:00.000Z",
                None,
                0,
                "{}",
            ),
        )
    return database


def _schema_snapshot(path: Path) -> dict[str, Any]:
    with sqlite3.connect(path) as connection:
        result: dict[str, Any] = {}
        for table in sorted(_table_names(connection) - {"alembic_version"}):
            quoted_table = _quote_identifier(table)
            create_sql = connection.execute(
                "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = ?",
                (table,),
            ).fetchone()[0]
            columns = connection.execute(f"PRAGMA table_info({quoted_table})").fetchall()
            foreign_keys = connection.execute(f"PRAGMA foreign_key_list({quoted_table})").fetchall()
            indexes = []
            for index_row in connection.execute(f"PRAGMA index_list({quoted_table})").fetchall():
                index_name = str(index_row[1])
                index_sql_row = connection.execute(
                    "SELECT sql FROM sqlite_master WHERE type = 'index' AND name = ?",
                    (index_name,),
                ).fetchone()
                indexes.append(
                    (
                        tuple(index_row[1:]),
                        tuple(connection.execute(f"PRAGMA index_xinfo({_quote_identifier(index_name)})")),
                        index_sql_row[0] if index_sql_row else None,
                    )
                )
            result[table] = {
                "sql": " ".join(str(create_sql).split()),
                "columns": tuple(columns),
                "foreign_keys": tuple(foreign_keys),
                "indexes": tuple(sorted(indexes, key=repr)),
            }
        return result


def _database_snapshot(path: Path) -> dict[str, Any]:
    with sqlite3.connect(path) as connection:
        tables = sorted(_table_names(connection))
        data = {table: tuple(sorted(connection.execute(f"SELECT * FROM {_quote_identifier(table)}").fetchall(), key=repr)) for table in tables}
    return {"schema": _schema_snapshot(path), "data": data}


def _durable_data_snapshot(path: Path) -> dict[str, tuple[tuple[Any, ...], ...]]:
    tables = (
        "goal_contracts",
        "task_runs",
        "plan_revisions",
        "task_steps",
        "task_leases",
    )
    with sqlite3.connect(path) as connection:
        return {table: tuple(sorted(connection.execute(f"SELECT * FROM {_quote_identifier(table)}").fetchall(), key=repr)) for table in tables}


def _revision(path: Path) -> str:
    with sqlite3.connect(path) as connection:
        return str(connection.execute("SELECT version_num FROM alembic_version").fetchone()[0])


def _table_names(connection: sqlite3.Connection) -> set[str]:
    return {str(row[0]) for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'")}


def _quote_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'
