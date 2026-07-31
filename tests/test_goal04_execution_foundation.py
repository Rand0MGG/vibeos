from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest
from alembic import command
from pydantic import ValidationError

from vibeos.capabilities import capability_payload, effect_policy_summary
from vibeos.core.adapters.contracts import StatusRequestV2
from vibeos.core.adapters.database import CoreDatabase
from vibeos.core.adapters.task_repository import SqliteTaskRepository
from vibeos.system_service_contracts import FIXTURE_UNIT, ServiceFactsV2, ServiceProcessFactV2, SystemServiceActionSpecV2


def test_0006_migrates_resumable_json_and_preserves_terminal_v1_bytes(tmp_path: Path) -> None:
    database = _database_at_0005(tmp_path / "mixed.sqlite3")
    timestamp = "2099-01-01T00:00:00.000Z"
    terminal_payload = _task_payload("terminal", "succeeded", timestamp, terminal=True)
    terminal_raw = json.dumps(terminal_payload, separators=(",", ":"))
    active_payload = _task_payload("active", "ready", timestamp)
    active_payload["permission_policy"] = {
        "L0": "automatic observe-only",
        "L1": "automatic low-risk action with audit",
        "L2": "requires stored review approval",
        "L3": "rejected",
    }
    plan_payload = {
        "schema_version": "v1",
        "plan": {
            "schema_version": "v0.5",
            "plan_id": "plan-active",
            "utterance": "copy text",
            "steps": [
                {
                    "id": "step-active",
                    "action": "clipboard.write",
                    "capability_id": "clipboard.write",
                    "risk_level": "L2",
                }
            ],
        },
        "observation": {"observation_id": "obs-active", "level": "L1"},
    }
    with sqlite3.connect(database.path) as connection:
        _insert_task_family(connection, "terminal", "succeeded", terminal_raw, timestamp)
        _insert_task_family(connection, "active", "ready", json.dumps(active_payload), timestamp)
        connection.execute(
            "INSERT INTO plan_revisions VALUES (?,?,?,?,?,?,?,?)",
            ("planrev-active", "active", 1, "plan-active", "v1", json.dumps(plan_payload), timestamp, "fixture"),
        )
        connection.execute(
            "INSERT INTO task_steps VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "active:planrev-active:step-active",
                "step-active",
                "active",
                "planrev-active",
                0,
                "clipboard.write",
                "clipboard.write",
                "pending",
                "idem-active",
                "v1",
                json.dumps(plan_payload["plan"]["steps"][0]),
                timestamp,
                timestamp,
            ),
        )

    database.upgrade()

    with sqlite3.connect(database.path) as connection:
        terminal_after = connection.execute("SELECT schema_version,payload_json FROM task_runs WHERE task_id='terminal'").fetchone()
        active_after = connection.execute("SELECT schema_version,payload_json FROM task_runs WHERE task_id='active'").fetchone()
        plan_after = json.loads(connection.execute("SELECT payload_json FROM plan_revisions WHERE task_id='active'").fetchone()[0])
        step_after = json.loads(connection.execute("SELECT payload_json FROM task_steps WHERE task_id='active'").fetchone()[0])
    assert terminal_after == ("v1", terminal_raw)
    assert active_after[0] == "v2"
    assert json.loads(active_after[1])["schema_version"] == "v2"
    assert json.loads(active_after[1])["effect_policy"] == effect_policy_summary()
    assert plan_after["plan"]["steps"][0]["effect_level"] == "E1"
    assert plan_after["plan"]["steps"][0]["schema_version"] == "v2"
    assert plan_after["observation"]["level"] == "O1"
    assert step_after["effect_level"] == "E1"
    assert SqliteTaskRepository(database).get("terminal").schema_version == "v1"  # type: ignore[union-attr]


def test_0006_pauses_unbound_legacy_effect_for_manual_disposition(tmp_path: Path) -> None:
    database = _database_at_0005(tmp_path / "ambiguous.sqlite3")
    timestamp = "2099-01-01T00:00:00.000Z"
    with sqlite3.connect(database.path) as connection:
        _insert_task_family(connection, "ambiguous", "ready", json.dumps(_task_payload("ambiguous", "ready", timestamp)), timestamp)
        connection.execute(
            "INSERT INTO plan_revisions VALUES (?,?,?,?,?,?,?,?)",
            (
                "planrev-ambiguous",
                "ambiguous",
                1,
                "plan-ambiguous",
                "v1",
                json.dumps({"schema_version": "v1", "review": {"risk_level": "L2"}}),
                timestamp,
                "fixture",
            ),
        )

    database.upgrade()
    state = SqliteTaskRepository(database).get("ambiguous")

    assert state is not None
    assert state.status.value == "paused"
    assert "manual effect disposition" in (state.pending_reason or "")


def test_0007_repairs_policy_summary_written_by_original_0006(tmp_path: Path) -> None:
    database = CoreDatabase(tmp_path / "broken-policy.sqlite3")
    command.upgrade(database._alembic_config(), "0006_effect_contract_v2")
    timestamp = "2099-01-01T00:00:00.000Z"
    payload = _task_payload("broken-policy", "ready", timestamp)
    payload["schema_version"] = "v2"
    payload["effect_policy"] = {
        "E0": "automatic observe-only",
        "E1": "automatic low-risk action with audit",
        "E2": "requires stored review approval",
        "E3": "rejected",
    }
    with sqlite3.connect(database.path) as connection:
        _insert_task_family(connection, "broken-policy", "ready", json.dumps(payload), timestamp)

    database.upgrade()

    with sqlite3.connect(database.path) as connection:
        repaired = json.loads(connection.execute("SELECT payload_json FROM task_runs WHERE task_id='broken-policy'").fetchone()[0])
        revision = connection.execute("SELECT version_num FROM alembic_version").fetchone()[0]
    assert repaired["effect_policy"] == effect_policy_summary()
    assert revision == "0007_repair_effect_policy_summary"


def test_v2_contracts_and_fixed_service_action_fail_closed() -> None:
    request = StatusRequestV2.model_validate({"action_id": "a", "task_step_id": "s", "dry_run": False}, strict=True)
    assert request.schema_version == "v2"
    with pytest.raises(ValidationError):
        StatusRequestV2.model_validate({"schema_version": "v1", "action_id": "a", "task_step_id": "s", "dry_run": False}, strict=True)
    assert all(item["schema_version"] == "v2" for item in capability_payload())

    action = SystemServiceActionSpecV2(operation="restart", idempotency_key="goal04-fixture-operation")
    assert action.unit == FIXTURE_UNIT
    assert action.effect_level == "E1"
    with pytest.raises(ValidationError):
        SystemServiceActionSpecV2.model_validate({"operation": "restart", "unit": "ssh.service", "idempotency_key": "goal04-fixture-operation"}, strict=True)
    facts = ServiceFactsV2(
        load_state="loaded",
        active_state="failed",
        sub_state="failed",
        result="exit-code",
        restart_count=0,
        process=ServiceProcessFactV2(main_pid=0, running=False, exit_code=1, exit_status=1),
        source="systemd_user_dbus",
        captured_at="2099-01-01T00:00:00.000Z",
        ttl_seconds=10,
        evidence_reference="fact-goal04",
    )
    assert facts.sensitivity == "D0"


def _database_at_0005(path: Path) -> CoreDatabase:
    database = CoreDatabase(path)
    command.upgrade(database._alembic_config(), "0005_persist_dry_run_intent")
    return database


def _insert_task_family(connection: sqlite3.Connection, task_id: str, status: str, payload: str, timestamp: str) -> None:
    contract_id = f"contract-{task_id}"
    contract = {
        "schema_version": "v1",
        "contract_id": contract_id,
        "task_id": task_id,
        "goal": task_id,
        "scope": [],
        "completion_conditions": [],
        "allowed_effects": [],
        "reality_boundaries": [],
        "version": 1,
        "created_at": timestamp,
        "dry_run": False,
    }
    connection.execute(
        "INSERT INTO goal_contracts VALUES (?,?,?,?,?,?)",
        (contract_id, task_id, 1, "v1", json.dumps(contract), timestamp),
    )
    connection.execute(
        "INSERT INTO task_runs (task_id,contract_id,status,revision,schema_version,payload_json,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?)",
        (task_id, contract_id, status, 1, "v1", payload, timestamp, timestamp),
    )
    connection.execute("INSERT INTO task_leases (task_id,fencing_token,updated_at) VALUES (?,?,?)", (task_id, 0, timestamp))


def _task_payload(task_id: str, status: str, timestamp: str, *, terminal: bool = False) -> dict[str, object]:
    terminal_outcome = (
        {
            "schema_version": "v1",
            "task_id": task_id,
            "status": status,
            "reason": "complete",
            "evidence_ids": [],
            "finished_at": timestamp,
        }
        if terminal
        else None
    )
    return {
        "schema_version": "v1",
        "task_id": task_id,
        "contract_id": f"contract-{task_id}",
        "status": status,
        "revision": 1,
        "created_at": timestamp,
        "updated_at": timestamp,
        "active_plan_revision_id": None,
        "current_step_id": None,
        "completed_step_ids": [],
        "pending_interaction_id": None,
        "pending_reason": None,
        "next_wake_at": None,
        "wait_event_key": None,
        "deadline_at": None,
        "suspended_status": None,
        "cancel_requested": False,
        "takeover_owner": None,
        "last_event": "fixture",
        "terminal_outcome": terminal_outcome,
    }
