from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError
from sqlalchemy import func, select

from vibeos.capabilities import capability_payload, effect_policy_summary, executable_actions
from vibeos.core.adapters.contracts import NotificationRequestV2, StatusRequestV2
from vibeos.core.adapters.database import CoreDatabase, DatabaseMigrationError
from vibeos.core.adapters.metadata import current_state, domain_events, outbox
from vibeos.core.composition import compose_foundation
from vibeos.notifications import NotificationAdapter
from vibeos.tool_protocol import ToolExecutionContext


class StubPortal:
    def status(self) -> dict[str, bool | str]:
        return {"available": False, "reason": "test session has no portal"}


class StubNotifications:
    def __init__(self, *, status: str = "sent", adapter: str | None = "stub-notify") -> None:
        self.status = status
        self.adapter = adapter
        self.calls: list[tuple[str, str]] = []

    def send(self, title: str, body: str = "") -> dict[str, str]:
        self.calls.append((title, body))
        if self.status == "sent":
            result = {"status": "sent", "title": title}
        else:
            result = {"status": self.status, "error": "delivery unavailable"}
        if self.adapter is not None:
            result["adapter"] = self.adapter
        return result


def test_strict_contracts_fail_closed_on_version_unknown_field_enum_and_coercion() -> None:
    valid = {"action_id": "action-1", "task_step_id": "status", "dry_run": False}

    assert StatusRequestV2.model_validate(valid, strict=True).schema_version == "v2"
    with pytest.raises(ValidationError):
        StatusRequestV2.model_validate({**valid, "schema_version": "v1"}, strict=True)
    with pytest.raises(ValidationError):
        StatusRequestV2.model_validate({**valid, "unexpected": True}, strict=True)
    with pytest.raises(ValidationError):
        StatusRequestV2.model_validate({**valid, "dry_run": "false"}, strict=True)
    with pytest.raises(ValidationError):
        StatusRequestV2.model_validate({**valid, "capability_id": "window.list"}, strict=True)
    notification = NotificationRequestV2.model_validate(
        {"action_id": "action-2", "task_step_id": "notification", "title": "   ", "dry_run": False},
        strict=True,
    )
    assert notification.canonical_title() == "VibeOS"
    assert notification.canonical_body() == ""
    with pytest.raises(ValidationError):
        NotificationRequestV2.model_validate(
            {"action_id": "action-2", "task_step_id": "notification", "body": 7, "dry_run": False},
            strict=True,
        )


def test_empty_database_is_created_by_alembic_with_required_pragmas(tmp_path: Path) -> None:
    database = CoreDatabase(tmp_path / "foundation.sqlite3")

    database.upgrade()

    with sqlite3.connect(database.path) as connection:
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        revision = connection.execute("SELECT version_num FROM alembic_version").fetchone()[0]
    assert {"goal_contracts", "task_runs", "task_leases", "current_state", "domain_events", "outbox"} <= tables
    assert {"reviews", "review_events"}.isdisjoint(tables)
    assert revision == "0006_effect_contract_v2"
    assert database.health() == {
        "ready": True,
        "journal_mode": "wal",
        "foreign_keys": 1,
        "busy_timeout_ms": 5000,
        "schema_ready": True,
        "alembic_revision": "0006_effect_contract_v2",
        "expected_alembic_revision": "0006_effect_contract_v2",
        "missing_tables": "",
        "path": str(database.path),
    }


def test_legacy_goal_contract_task_uniqueness_is_repaired_for_versioning(tmp_path: Path) -> None:
    db_path = tmp_path / "legacy-contract-uniqueness.sqlite3"
    with sqlite3.connect(db_path) as connection:
        connection.execute("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL PRIMARY KEY)")
        connection.execute("INSERT INTO alembic_version VALUES ('0003_repair_durable_task_semantics')")
        connection.execute(
            "CREATE TABLE goal_contracts (contract_id VARCHAR(240) PRIMARY KEY, task_id VARCHAR(240) NOT NULL UNIQUE, "
            "version INTEGER NOT NULL, schema_version VARCHAR(20) NOT NULL, payload_json TEXT NOT NULL, created_at VARCHAR(40) NOT NULL)"
        )
        connection.execute("INSERT INTO goal_contracts VALUES ('contract_v1','task_1',1,'v1','{}','2099-01-01T00:00:00.000Z')")

    CoreDatabase(db_path).upgrade()

    with sqlite3.connect(db_path) as connection:
        connection.execute("INSERT INTO goal_contracts VALUES ('contract_v2','task_1',2,'v1','{}','2099-01-01T00:00:01.000Z')")
        versions = connection.execute("SELECT version FROM goal_contracts WHERE task_id = 'task_1' ORDER BY version").fetchall()
        revision = connection.execute("SELECT version_num FROM alembic_version").fetchone()[0]
    assert versions == [(1,), (2,)]
    assert revision == "0006_effect_contract_v2"


def test_database_health_rejects_pragmas_without_authoritative_schema(tmp_path: Path) -> None:
    database = CoreDatabase(tmp_path / "unmigrated.sqlite3")

    health = database.health()

    assert health["ready"] is False
    assert health["schema_ready"] is False
    assert health["alembic_revision"] == ""
    assert {"alembic_version", "current_state", "domain_events", "outbox"} <= set(str(health["missing_tables"]).split(","))


def test_real_legacy_pending_review_fixture_upgrades_idempotently(tmp_path: Path) -> None:
    db_path = tmp_path / "reviews.sqlite3"
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            "CREATE TABLE reviews (review_id TEXT PRIMARY KEY, status TEXT NOT NULL, review_kind TEXT NOT NULL, "
            "plan_id TEXT, step_id TEXT, utterance TEXT NOT NULL, intent_payload TEXT NOT NULL, review_payload TEXT NOT NULL, "
            "plan_payload TEXT, snapshot_payload TEXT, supplemental_input TEXT, pending_reason TEXT, created_at TEXT NOT NULL, "
            "expires_at TEXT, version INTEGER NOT NULL DEFAULT 0, payload TEXT NOT NULL)"
        )
        connection.execute(
            "INSERT INTO reviews VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "rev_legacy_fixture",
                "pending",
                "intent",
                None,
                "close_firefox",
                "close Firefox",
                "{}",
                "{}",
                None,
                None,
                None,
                "explicit approval required",
                "2099-01-01T00:00:00.000Z",
                "2099-01-01T00:10:00.000Z",
                0,
                "{}",
            ),
        )
    database = CoreDatabase(db_path)

    database.upgrade()
    database.upgrade()
    with sqlite3.connect(db_path) as connection:
        task = connection.execute("SELECT status, pending_interaction_id FROM task_runs").fetchone()
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert task == ("paused", None)
    assert {"reviews", "review_events"}.isdisjoint(tables)


def test_legacy_review_with_complete_plan_migrates_plan_and_steps(tmp_path: Path) -> None:
    db_path = tmp_path / "restorable-reviews.sqlite3"
    plan = {
        "schema_version": "v0.3",
        "plan_id": "plan_legacy_close",
        "utterance": "close Firefox",
        "selected_route_id": "window_close_route",
        "routes": [{"id": "window_close_route", "score": 1.0, "domain_id": "windows"}],
        "steps": [
            {
                "id": "close_firefox",
                "action": "window.close",
                "capability_id": "window.close",
                "target": {"name": "Firefox"},
                "effect_level": "E3",
            }
        ],
    }
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            "CREATE TABLE reviews (review_id TEXT PRIMARY KEY, status TEXT NOT NULL, review_kind TEXT NOT NULL, "
            "plan_id TEXT, step_id TEXT, utterance TEXT NOT NULL, intent_payload TEXT NOT NULL, review_payload TEXT NOT NULL, "
            "plan_payload TEXT, snapshot_payload TEXT, supplemental_input TEXT, pending_reason TEXT, created_at TEXT NOT NULL, "
            "expires_at TEXT, version INTEGER NOT NULL DEFAULT 0, payload TEXT NOT NULL)"
        )
        connection.execute(
            "INSERT INTO reviews VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "rev_restorable",
                "pending",
                "intent",
                plan["plan_id"],
                "close_firefox",
                plan["utterance"],
                "{}",
                "{}",
                json.dumps(plan),
                json.dumps({"snapshot_version": 0, "plan": plan}),
                None,
                "explicit approval required",
                "2099-01-01T00:00:00.000Z",
                None,
                0,
                "{}",
            ),
        )

    database = CoreDatabase(db_path)
    database.upgrade()
    with sqlite3.connect(db_path) as connection:
        task = connection.execute("SELECT status, pending_interaction_id, active_plan_revision_id, current_step_id, payload_json FROM task_runs").fetchone()
        step = connection.execute("SELECT step_id, capability_id, status FROM task_steps").fetchone()

    assert task is not None
    assert task[:4] == ("awaiting_review", "rev_restorable", task[2], "close_firefox")
    assert task[2]
    assert json.loads(task[4])["active_plan_revision_id"] == task[2]
    assert step == ("close_firefox", "window.close", "pending")


def test_failed_migration_restores_exact_preupgrade_database(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "failed.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE legacy_marker (value TEXT NOT NULL)")
        connection.execute("INSERT INTO legacy_marker VALUES ('preserve-me')")
    database = CoreDatabase(path)

    def fail_after_partial_ddl() -> None:
        with sqlite3.connect(path) as connection:
            connection.execute("CREATE TABLE half_migrated (value TEXT)")
        raise RuntimeError("injected migration failure")

    monkeypatch.setattr(database, "_run_alembic_upgrade", fail_after_partial_ddl)

    with pytest.raises(DatabaseMigrationError):
        database.upgrade()
    with sqlite3.connect(path) as connection:
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        marker = connection.execute("SELECT value FROM legacy_marker").fetchone()[0]
    assert marker == "preserve-me"
    assert "half_migrated" not in tables


def test_process_crash_does_not_commit_partial_wal_transaction(tmp_path: Path) -> None:
    database = CoreDatabase(tmp_path / "crash.sqlite3")
    database.upgrade()
    script = (
        "import os,sqlite3,sys; "
        "c=sqlite3.connect(sys.argv[1]); c.execute('PRAGMA journal_mode=WAL'); c.execute('BEGIN IMMEDIATE'); "
        'c.execute("INSERT INTO current_state '
        "(state_key,aggregate_type,aggregate_id,state_version,status,schema_version,payload_json,updated_at) "
        "VALUES ('crash','action','crash',1,'succeeded','v1','{}','2099-01-01T00:00:00Z')\"); "
        "os._exit(17)"
    )

    completed = subprocess.run([sys.executable, "-c", script, str(database.path)], check=False)

    assert completed.returncode == 17
    with sqlite3.connect(database.path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM current_state WHERE state_key = 'crash'").fetchone()[0] == 0
        assert connection.execute("PRAGMA quick_check").fetchone()[0] == "ok"
        assert connection.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"


def test_safe_downgrade_recreates_legacy_rollback_schema(tmp_path: Path) -> None:
    database = CoreDatabase(tmp_path / "tasks.sqlite3")
    database.upgrade()
    database.downgrade()

    with sqlite3.connect(database.path) as connection:
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert {"reviews", "review_events", "schema_migrations"} <= tables
    assert {"current_state", "domain_events", "outbox", "task_runs"}.isdisjoint(tables)


def test_two_slices_return_adapter_material_without_a_second_durable_aggregate(tmp_path: Path) -> None:
    database = CoreDatabase(tmp_path / "slices.sqlite3")
    database.upgrade()
    notifications = StubNotifications()
    foundation = compose_foundation(
        database=database,
        portal=StubPortal(),
        notifications=notifications,
        capabilities=_capabilities,
    )
    context = ToolExecutionContext(
        session_id="session",
        goal_id="goal",
        turn_id="turn",
        attempt_id="attempt",
        strategy_id="strategy",
        environment=SimpleNamespace(dry_run=False),
    )
    tools = {spec.tool_id: spec for spec in foundation.tool_specs}

    status = tools["system.status"].runner({"task_step_id": "system_status"}, context)
    notification = tools["notification.send"].runner({"task_step_id": "notification_send", "title": "VibeOS", "body": "done"}, context)

    assert status.status == "succeeded"
    assert status.evidence["capability_count"] == 19
    assert "action_receipt" not in status.output
    assert "observation_evidence" not in status.output
    assert notification.status == "succeeded"
    assert "action_receipt" not in notification.output
    assert notification.output["notification_adapter"] == "stub-notify"
    assert notifications.calls == [("VibeOS", "done")]
    assert table_counts(database) == (0, 0, 0)


@pytest.mark.parametrize("dry_run", [False, True], ids=["execute", "dry-run"])
def test_system_status_frozen_legacy_compatibility_projection(tmp_path: Path, dry_run: bool) -> None:
    database = CoreDatabase(tmp_path / "status-equivalence.sqlite3")
    database.upgrade()
    foundation = compose_foundation(
        database=database,
        portal=StubPortal(),
        notifications=StubNotifications(),
        capabilities=_capabilities,
    )
    status = next(spec for spec in foundation.tool_specs if spec.tool_id == "system.status")

    result = status.runner(
        {"task_step_id": "system_status", "dry_run": dry_run},
        _tool_context(dry_run=dry_run),
    )

    # Frozen compatibility fields from the removed legacy ToolSpec. Typed
    # receipt/evidence fields are additive and intentionally remain outside
    # this projection comparison.
    assert result.status == "succeeded"
    assert result.output["adapter"] == "system.status"
    assert result.output["adapter_status"] == "succeeded"
    assert result.output["portal"] == {"available": False, "reason": "test session has no portal"}
    assert result.output["capabilities"] == executable_actions()
    assert result.evidence["capability_count"] == 19


@pytest.mark.parametrize(
    ("payload", "dry_run", "adapter_status", "expected_title", "expected_body", "expected_result", "expected_failure"),
    [
        ({}, False, "sent", "VibeOS", "", "succeeded", "none"),
        ({"title": "   ", "body": "   "}, False, "sent", "VibeOS", "", "succeeded", "none"),
        ({"title": "Notice", "message": " hello "}, False, "sent", "Notice", "hello", "succeeded", "none"),
        ({"title": "Notice", "body": "", "message": "fallback"}, False, "sent", "Notice", "fallback", "succeeded", "none"),
        ({"title": "Notice", "body": "hello"}, False, "unavailable", "Notice", "hello", "failed", "environment_unreachable"),
        ({"title": "Notice", "body": "hello"}, False, "timeout", "Notice", "hello", "failed", "tool_timeout"),
        ({"title": "   ", "body": ""}, True, "sent", "VibeOS", "", "succeeded", "none"),
    ],
    ids=["missing-fields", "blank-fields", "message-alias", "empty-body-fallback", "unavailable", "timeout", "dry-run"],
)
def test_notification_frozen_legacy_compatibility_matrix(
    tmp_path: Path,
    payload: dict[str, str],
    dry_run: bool,
    adapter_status: str,
    expected_title: str,
    expected_body: str,
    expected_result: str,
    expected_failure: str,
) -> None:
    database = CoreDatabase(tmp_path / "notification-equivalence.sqlite3")
    database.upgrade()
    notifications = StubNotifications(status=adapter_status)
    foundation = compose_foundation(
        database=database,
        portal=StubPortal(),
        notifications=notifications,
        capabilities=_capabilities,
    )
    notification = next(spec for spec in foundation.tool_specs if spec.tool_id == "notification.send")

    result = notification.runner(
        {"task_step_id": "notification_send", "dry_run": dry_run, **payload},
        _tool_context(dry_run=dry_run),
    )

    assert result.status == expected_result
    assert result.failure_class == expected_failure
    assert result.output["adapter"] == "notifications.send"
    assert result.output["adapter_status"] == ("dry_run" if dry_run else "succeeded" if adapter_status == "sent" else adapter_status)
    assert result.evidence["title"] == expected_title
    assert "body" not in result.evidence  # Security-governed redaction from the legacy evidence shape.
    if dry_run:
        assert notifications.calls == []
        assert result.evidence["dry_run"] is True
        assert "status" not in result.output
        assert "notification_adapter" not in result.output
    else:
        assert notifications.calls == [(expected_title, expected_body)]
        assert result.output["status"] == adapter_status
        assert result.output["notification_adapter"] == "stub-notify"
        assert result.evidence["notification_adapter"] == "stub-notify"
    if expected_result == "succeeded":
        assert result.output["selected_target"] == expected_title
        assert result.state_updates == {"selected_target": expected_title}
    else:
        assert "selected_target" not in result.output
        assert result.message == "delivery unavailable"


def test_slice_tool_boundary_rejects_unknown_fields_without_dispatch(tmp_path: Path) -> None:
    database = CoreDatabase(tmp_path / "rejected.sqlite3")
    database.upgrade()
    notifications = StubNotifications()
    foundation = compose_foundation(
        database=database,
        portal=StubPortal(),
        notifications=notifications,
        capabilities=_capabilities,
    )
    context = ToolExecutionContext(
        session_id="session",
        goal_id="goal",
        turn_id="turn",
        attempt_id="attempt",
        strategy_id="strategy",
        environment=SimpleNamespace(dry_run=False),
    )
    notification = next(spec for spec in foundation.tool_specs if spec.tool_id == "notification.send")

    result = notification.runner(
        {"task_step_id": "notification_send", "title": "VibeOS", "body": "done", "bypass": True},
        context,
    )

    assert result.status == "failed"
    assert result.failure_class == "invalid_contract"
    assert notifications.calls == []
    assert table_counts(database) == (0, 0, 0)


def test_notification_content_is_absent_from_receipt_evidence_and_database(tmp_path: Path) -> None:
    canary = "sk-canary person@example.com"
    database = CoreDatabase(tmp_path / "privacy.sqlite3")
    database.upgrade()
    foundation = compose_foundation(
        database=database,
        portal=StubPortal(),
        notifications=StubNotifications(),
        capabilities=_capabilities,
    )
    context = ToolExecutionContext(
        session_id="session",
        goal_id="goal",
        turn_id="turn",
        attempt_id="attempt",
        strategy_id="strategy",
        environment=SimpleNamespace(dry_run=False),
    )
    notification = next(spec for spec in foundation.tool_specs if spec.tool_id == "notification.send")

    result = notification.runner(
        {"task_step_id": "notification_send", "title": "VibeOS", "body": canary},
        context,
    )
    with database.engine.connect() as connection:
        persisted = "\n".join(str(row[0]) for table in (current_state, domain_events, outbox) for row in connection.execute(select(table.c.payload_json)))

    assert result.status == "succeeded"
    assert canary not in repr(result)
    assert canary not in persisted


def test_real_notification_adapter_is_retained_below_the_new_slice(tmp_path: Path) -> None:
    database = CoreDatabase(tmp_path / "real-adapter.sqlite3")
    database.upgrade()
    foundation = compose_foundation(
        database=database,
        portal=StubPortal(),
        notifications=NotificationAdapter(),
        capabilities=_capabilities,
    )

    assert type(foundation.slices).__name__ == "FoundationSliceService"
    assert {spec.tool_id for spec in foundation.tool_specs} == {"system.status", "notification.send"}


def table_counts(database: CoreDatabase) -> tuple[int, int, int]:
    with database.engine.connect() as connection:
        return (
            int(connection.execute(select(func.count()).select_from(current_state)).scalar_one()),
            int(connection.execute(select(func.count()).select_from(domain_events)).scalar_one()),
            int(connection.execute(select(func.count()).select_from(outbox)).scalar_one()),
        )


def _tool_context(*, dry_run: bool) -> ToolExecutionContext:
    return ToolExecutionContext(
        session_id="session",
        goal_id="goal",
        turn_id="turn",
        attempt_id="attempt",
        strategy_id="strategy",
        environment=SimpleNamespace(dry_run=dry_run),
    )


def _capabilities() -> dict[str, object]:
    return {
        "schema_version": "v2",
        "capabilities": executable_actions(),
        "capability_details": capability_payload(),
        "effect_policy": effect_policy_summary(),
    }
