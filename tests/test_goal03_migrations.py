from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest
from alembic import command

from vibeos.apps import AppRegistry
from vibeos.audit import AuditLog
from vibeos.broker import CapabilityBroker
from vibeos.core.adapters.database import CoreDatabase, DatabaseMigrationError
from vibeos.models import AppEntry, Intent, WindowEntry

from tests.support_intent_broker import FixtureIntentBroker


HEAD_REVISION = "0005_persist_dry_run_intent"
MIGRATION_FILES = (
    "0001_core_foundation.py",
    "0002_durable_task_engine.py",
    "0003_repair_durable_task_semantics.py",
    "0004_goal_contract_version_index.py",
    "0005_persist_dry_run_intent.py",
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


def test_goal01_pending_review_upgrade_is_publicly_resumable(tmp_path: Path) -> None:
    database = _goal01_database(tmp_path / "review.sqlite3")
    database.upgrade()
    windows = _MigratedWindows()
    broker = CapabilityBroker(
        intent_broker=FixtureIntentBroker(),
        windows=windows,
        audit=AuditLog(tmp_path / "review-audit.jsonl"),
        database=database,
    )

    pending = broker.pending_reviews()
    rebound = broker.approve_review("review_goal01")
    assert rebound.review_id is not None
    approved = broker.approve_review(rebound.review_id)

    assert len(pending) == 1
    assert pending[0]["review_id"] == "review_goal01"
    assert pending[0]["review_kind"] == "action"
    assert rebound.status == "review_required"
    assert rebound.message == "safety binding changed; a fresh approval is required"
    assert rebound.review_id != "review_goal01"
    assert approved.status == "executed"
    assert windows.close_calls == 1
    assert broker.pending_reviews() == []


def test_goal01_pending_clarification_upgrade_accepts_supplemental_input(tmp_path: Path) -> None:
    database = _goal01_database(tmp_path / "clarification.sqlite3")
    with sqlite3.connect(database.path) as connection:
        connection.execute(
            "UPDATE reviews SET review_kind = 'user_input', plan_id = NULL, step_id = NULL, "
            "utterance = 'open an application', plan_payload = NULL, snapshot_payload = NULL "
            "WHERE review_id = 'review_goal01'"
        )
    database.upgrade()
    apps = _MigratedApps()
    broker = CapabilityBroker(
        intent_broker=_MigratedClarifyingIntentBroker(),
        apps=apps,
        audit=AuditLog(tmp_path / "clarification-audit.jsonl"),
        database=database,
    )

    pending = broker.pending_reviews()
    resumed = broker.provide_review_input("review_goal01", "Firefox")

    assert len(pending) == 1
    assert pending[0]["review_id"] == "review_goal01"
    assert pending[0]["review_kind"] == "user_input"
    assert resumed.status == "executed"
    assert apps.open_calls == 1
    assert broker.pending_reviews() == []


def test_0005_pauses_active_task_when_legacy_execution_intent_is_unknown(tmp_path: Path) -> None:
    path = tmp_path / "unknown-execution-intent.sqlite3"
    database = CoreDatabase(path)
    command.upgrade(database._alembic_config(), "0004_goal_contract_version_index")
    created_at = "2099-01-01T00:00:00.000Z"
    contract_payload = {
        "schema_version": "v1",
        "contract_id": "contract-unknown-intent",
        "task_id": "task-unknown-intent",
        "goal": "open Firefox",
        "scope": [],
        "completion_conditions": [],
        "allowed_effects": [],
        "reality_boundaries": [],
        "version": 1,
        "created_at": created_at,
    }
    task_payload = {
        "schema_version": "v1",
        "task_id": "task-unknown-intent",
        "contract_id": "contract-unknown-intent",
        "status": "created",
        "revision": 0,
        "created_at": created_at,
        "updated_at": created_at,
        "last_event": "created",
    }
    with sqlite3.connect(path) as connection:
        connection.execute(
            "INSERT INTO goal_contracts VALUES (?,?,?,?,?,?)",
            ("contract-unknown-intent", "task-unknown-intent", 1, "v1", json.dumps(contract_payload), created_at),
        )
        connection.execute(
            "INSERT INTO task_runs (task_id, contract_id, status, revision, schema_version, payload_json, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?)",
            ("task-unknown-intent", "contract-unknown-intent", "created", 0, "v1", json.dumps(task_payload), created_at, created_at),
        )
        connection.execute(
            "INSERT INTO task_leases (task_id, fencing_token, updated_at) VALUES (?,?,?)",
            ("task-unknown-intent", 0, created_at),
        )

    database.upgrade()
    broker = CapabilityBroker(
        intent_broker=FixtureIntentBroker(),
        audit=AuditLog(tmp_path / "unknown-execution-intent.audit.jsonl"),
        database=database,
    )
    contract = broker.task_repository.contract("task-unknown-intent")
    assert contract is not None
    assert contract.dry_run is None
    with sqlite3.connect(path) as connection:
        migrated_payload = json.loads(connection.execute("SELECT payload_json FROM goal_contracts").fetchone()[0])
    assert "dry_run" in migrated_payload
    assert migrated_payload["dry_run"] is None

    broker.task_engine.resume_task("task-unknown-intent")

    paused = broker.task_repository.get("task-unknown-intent")
    assert paused is not None
    assert paused.status.value == "paused"
    assert paused.pending_reason == "persisted execution intent is unknown; explicit user confirmation is required"


class _MigratedWindows:
    def __init__(self) -> None:
        self.close_calls = 0

    def list_windows(self):
        return [WindowEntry(window_id="1", app_id="firefox.desktop", title="Firefox", focused=True)]

    def resolve(self, query):
        return self.list_windows() if query.lower() in {"firefox", "current"} else []

    def focus(self, window):
        return {"status": "focused", "window_id": window.window_id}

    def minimize(self, window):
        return {"status": "minimized", "window_id": window.window_id}

    def maximize(self, window):
        return {"status": "maximized", "window_id": window.window_id}

    def close(self, window):
        self.close_calls += 1
        return {"status": "closed", "window_id": window.window_id}


class _MigratedApps(AppRegistry):
    def __init__(self) -> None:
        self.open_calls = 0

    def list_apps(self):
        return [AppEntry(desktop_id="firefox.desktop", name="Firefox", keywords=("browser",))]

    def open_app(self, app):
        self.open_calls += 1
        return {"status": "opened", "desktop_id": app.desktop_id}


class _MigratedClarifyingIntentBroker:
    def parse(self, utterance: str) -> Intent:
        marker = "Additional user detail:"
        if marker not in utterance:
            return Intent.unknown("the application name is required")
        name = utterance.rsplit(marker, 1)[-1].strip()
        return Intent(action="app.open", target={"name": name}, reason="user supplied the missing application name")


def _goal01_database(path: Path) -> CoreDatabase:
    database = CoreDatabase(path)
    command.upgrade(database._alembic_config(), "0001_core_foundation")
    plan = {
        "schema_version": "v0.3",
        "plan_id": "plan_goal01",
        "utterance": "close Firefox",
        "selected_route_id": "window_close_route",
        "routes": [{"id": "window_close_route", "score": 1.0, "domain_id": "window_management"}],
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
