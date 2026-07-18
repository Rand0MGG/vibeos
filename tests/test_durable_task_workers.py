from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from vibeos.audit import AuditLog
from vibeos.broker import CapabilityBroker
from vibeos.core.adapters.database import CoreDatabase
from vibeos.core.adapters.task_repository import SqliteTaskRepository
from vibeos.core.application.task_workers import OutboxDispatcherComponent, TaskSchedulerComponent
from vibeos.core.domain.task import GoalContract, TaskEvent, TaskEventType, TaskRun, TaskStatus
from vibeos.core.domain.task_transitions import transition

from tests.support_intent_broker import FixtureIntentBroker


def test_daemon_scheduler_restart_scans_and_resumes_persisted_due_timer(tmp_path: Path) -> None:
    path = tmp_path / "tasks.sqlite3"
    first_database = CoreDatabase(path)
    first_database.upgrade()
    first = SqliteTaskRepository(first_database)
    timestamp = "2099-01-01T00:00:00.000Z"
    contract = GoalContract("contract-restart", "task-restart", "wait", (), (), (), (), 1, timestamp, dry_run=False)
    state = TaskRun("task-restart", contract.contract_id, TaskStatus.CREATED, 0, timestamp, timestamp)
    first.create(contract, state)
    planning = first.commit(transition(state, _event(state, TaskEventType.PLAN_REQUESTED)))
    ready = first.commit(transition(planning, _event(planning, TaskEventType.PLAN_READY, plan_revision_id="planrev-restart")))
    first.commit(transition(ready, _event(ready, TaskEventType.WAIT_REQUESTED, wake_at="2099-01-01T01:00:00.000Z")))
    first_database.dispose()

    restarted = SqliteTaskRepository(CoreDatabase(path))
    resumed: list[str] = []

    def resume(task_id: str) -> None:
        current = restarted.get(task_id)
        assert current is not None
        lease = restarted.claim(
            task_id,
            owner="restarted-daemon",
            now="2099-01-01T01:00:00.000Z",
            expires_at="2099-01-01T01:01:00.000Z",
        )
        assert lease is not None
        restarted.commit(transition(current, _event(current, TaskEventType.TIMER_ELAPSED)), lease=lease)
        restarted.release(lease, now="2099-01-01T01:00:01.000Z")
        resumed.append(task_id)

    scheduler = TaskSchedulerComponent(
        scan=lambda: restarted.recoverable("2099-01-01T01:00:00.000Z"),
        resume=resume,
        poll_seconds=60,
    )
    assert asyncio.run(scheduler.tick()) == 1
    recovered = restarted.get(state.task_id)
    assert resumed == [state.task_id]
    assert recovered is not None
    assert recovered.status is TaskStatus.READY
    assert recovered.next_wake_at is None


@pytest.mark.parametrize("initial_status", [TaskStatus.CREATED, TaskStatus.PLANNING])
def test_scheduler_recovers_task_interrupted_before_plan_was_persisted(tmp_path: Path, initial_status: TaskStatus) -> None:
    database = CoreDatabase(tmp_path / "planning-recovery.sqlite3")
    broker = CapabilityBroker(
        intent_broker=FixtureIntentBroker(),
        audit=AuditLog(tmp_path / "planning-recovery-audit.jsonl"),
        database=database,
    )
    timestamp = "2099-01-01T00:00:00.000Z"
    contract = GoalContract("contract-planning-recovery", "task-planning-recovery", "status", (), (), (), (), 1, timestamp, dry_run=False)
    state = TaskRun(contract.task_id, contract.contract_id, TaskStatus.CREATED, 0, timestamp, timestamp)
    broker.task_repository.create(contract, state)
    if initial_status is TaskStatus.PLANNING:
        state = broker.task_repository.commit(transition(state, _event(state, TaskEventType.PLAN_REQUESTED)))

    assert broker.task_repository.recoverable("2099-01-01T00:00:10.000Z") == (state.task_id,)
    broker.task_engine.resume_task(state.task_id)

    recovered = broker.task_repository.get(state.task_id)
    assert recovered is not None
    assert recovered.status is TaskStatus.SUCCEEDED
    receipts = broker.task_repository.receipts(state.task_id)
    assert len(receipts) == 1
    assert receipts[0].status == "succeeded"


def test_scheduler_isolates_one_task_failure_and_keeps_serving() -> None:
    batches = iter((("bad", "good"), ("later",)))
    resumed: list[str] = []

    def resume(task_id: str) -> None:
        if task_id == "bad":
            raise RuntimeError("fixture failure")
        resumed.append(task_id)

    scheduler = TaskSchedulerComponent(scan=lambda: next(batches), resume=resume, max_concurrency=2)

    assert asyncio.run(scheduler.tick()) == 2
    assert scheduler.health_status()[0] == "degraded"
    assert asyncio.run(scheduler.tick()) == 1
    assert resumed == ["good", "later"]


def test_outbox_dispatcher_isolates_one_message_failure_and_keeps_serving() -> None:
    batches = iter((("bad", "good"), ("later",)))
    consumed: list[str] = []

    def consume(message: object) -> None:
        if message == "bad":
            raise RuntimeError("fixture failure")
        consumed.append(str(message))

    dispatcher = OutboxDispatcherComponent(claim=lambda: next(batches), consume=consume, max_concurrency=2)

    assert asyncio.run(dispatcher.tick()) == 2
    assert dispatcher.health_status()[0] == "degraded"
    assert asyncio.run(dispatcher.tick()) == 1
    assert consumed == ["good", "later"]


def test_recovery_commits_explicit_timeout_for_overdue_task(tmp_path: Path) -> None:
    broker = CapabilityBroker(
        intent_broker=FixtureIntentBroker(),
        audit=AuditLog(tmp_path / "timeout-audit.jsonl"),
        database=CoreDatabase(tmp_path / "timeout.sqlite3"),
    )
    timestamp = "2026-01-01T00:00:00.000Z"
    contract = GoalContract("contract-timeout", "task-timeout", "status", (), (), (), (), 1, timestamp, dry_run=False)
    state = TaskRun(
        contract.task_id,
        contract.contract_id,
        TaskStatus.CREATED,
        0,
        timestamp,
        timestamp,
        deadline_at="2026-01-01T01:00:00.000Z",
    )
    broker.task_repository.create(contract, state)

    broker.task_engine.resume_task(state.task_id)

    timed_out = broker.task_repository.get(state.task_id)
    assert timed_out is not None
    assert timed_out.status is TaskStatus.FAILED
    assert timed_out.last_event == "timeout"
    assert timed_out.terminal_outcome is not None
    assert timed_out.terminal_outcome.reason == "task deadline elapsed"


def _event(
    state: TaskRun,
    event_type: TaskEventType,
    *,
    plan_revision_id: str | None = None,
    wake_at: str | None = None,
) -> TaskEvent:
    return TaskEvent(
        event_id=f"restart-{state.revision + 1}-{event_type.value}",
        task_id=state.task_id,
        event_type=event_type,
        occurred_at=f"2099-01-01T00:00:{state.revision + 1:02d}.000Z",
        plan_revision_id=plan_revision_id,
        wake_at=wake_at,
    )
