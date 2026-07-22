from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path

import pytest
from sqlalchemy import func, select

from vibeos.core.adapters.database import CoreDatabase
from vibeos.core.adapters.metadata import current_state, domain_events, outbox, task_runs, wait_conditions
from vibeos.core.adapters.outbox_repository import SqliteOutboxRepository
from vibeos.core.adapters.task_codec import encode_task
from vibeos.core.adapters.task_repository import SqliteTaskRepository, TaskConcurrencyError, TaskLeaseLost
from vibeos.core.domain.task import GoalContract, InvalidTaskTransition, TaskEvent, TaskEventType, TaskRun, TaskStatus
from vibeos.core.domain.task_transitions import transition


@pytest.mark.parametrize("fault_stage", ["after_task_state", "after_task_event", "after_task_outbox"])
def test_task_state_event_and_outbox_rollback_atomically_on_crash(tmp_path: Path, fault_stage: str) -> None:
    database, repository, state = _repository(tmp_path)
    crashing = SqliteTaskRepository(
        database,
        fault_injector=lambda stage: (_ for _ in ()).throw(RuntimeError("forced crash")) if stage == fault_stage else None,
    )

    with pytest.raises(RuntimeError, match="forced crash"):
        crashing.commit(transition(state, _event(state, TaskEventType.PLAN_REQUESTED)))

    with database.engine.connect() as connection:
        assert connection.execute(select(task_runs.c.revision)).scalar_one() == 0
        assert connection.execute(select(func.count()).select_from(current_state)).scalar_one() == 0
        assert connection.execute(select(func.count()).select_from(domain_events)).scalar_one() == 0
        assert connection.execute(select(func.count()).select_from(outbox)).scalar_one() == 0
    assert repository.get(state.task_id) == state


def test_revision_compare_and_swap_rejects_duplicate_transition(tmp_path: Path) -> None:
    _database, repository, state = _repository(tmp_path)
    task_transition = transition(state, _event(state, TaskEventType.PLAN_REQUESTED))
    committed = repository.commit(task_transition)
    assert committed.revision == 1
    with pytest.raises(TaskConcurrencyError):
        repository.commit(task_transition)


def test_goal_contract_revisions_are_append_only_and_latest_is_selected(tmp_path: Path) -> None:
    _database, repository, state = _repository(tmp_path)
    original = repository.contract(state.task_id)
    assert original is not None
    revised = replace(original, contract_id="contract-two", goal="durable test with clarified scope", version=2)
    repository.add_contract_version(revised)
    assert repository.contract(state.task_id) == revised
    with pytest.raises(TaskConcurrencyError):
        repository.add_contract_version(revised)


def test_clarified_contract_and_transition_rollback_as_one_transaction(tmp_path: Path) -> None:
    database, repository, created = _repository(tmp_path)
    planning = repository.commit(transition(created, _event(created, TaskEventType.PLAN_REQUESTED)))
    waiting = repository.commit(
        transition(
            planning,
            _event(planning, TaskEventType.CLARIFICATION_REQUIRED, interaction_id="clarification-one"),
        )
    )
    original = repository.contract(created.task_id)
    assert original is not None
    revised = replace(original, contract_id="contract-two", goal="durable clarified test", version=2)
    lease = repository.claim(
        created.task_id,
        owner="clarification-worker",
        now="2099-01-01T00:00:10.000Z",
        expires_at="2099-01-01T00:01:10.000Z",
    )
    assert lease is not None
    clarified = transition(waiting, _event(waiting, TaskEventType.CLARIFICATION_PROVIDED))
    crashing = SqliteTaskRepository(
        database,
        fault_injector=lambda stage: (_ for _ in ()).throw(RuntimeError("forced crash")) if stage == "after_task_outbox" else None,
    )

    with pytest.raises(RuntimeError, match="forced crash"):
        crashing.commit(clarified, contract_version=revised, lease=lease)

    assert repository.get(created.task_id) == waiting
    assert repository.contract(created.task_id) == original
    committed = repository.commit(clarified, contract_version=revised, lease=lease)
    assert committed.status is TaskStatus.PLANNING
    assert repository.contract(created.task_id) == revised


def test_two_workers_have_one_owner_and_fencing_rejects_stale_worker(tmp_path: Path) -> None:
    _database, first, state = _repository(tmp_path)
    second = SqliteTaskRepository(first.database)

    lease_one = first.claim(state.task_id, owner="worker-one", now="2099-01-01T00:00:00.000Z", expires_at="2099-01-01T00:01:00.000Z")
    assert lease_one is not None
    assert second.claim(state.task_id, owner="worker-two", now="2099-01-01T00:00:30.000Z", expires_at="2099-01-01T00:02:00.000Z") is None
    lease_two = second.claim(state.task_id, owner="worker-two", now="2099-01-01T00:01:01.000Z", expires_at="2099-01-01T00:02:00.000Z")
    assert lease_two is not None
    assert lease_two.fencing_token > lease_one.fencing_token

    task_transition = transition(state, _event(state, TaskEventType.PLAN_REQUESTED))
    with pytest.raises(TaskLeaseLost):
        first.commit(task_transition, lease=lease_one)
    assert second.commit(task_transition, lease=lease_two).revision == 1


def test_concurrent_claim_race_yields_exactly_one_valid_owner(tmp_path: Path) -> None:
    _database, repository, state = _repository(tmp_path)

    def claim(index: int):
        return SqliteTaskRepository(repository.database).claim(
            state.task_id,
            owner=f"worker-{index}",
            now="2099-01-01T00:00:00.000Z",
            expires_at="2099-01-01T00:01:00.000Z",
        )

    with ThreadPoolExecutor(max_workers=8) as executor:
        claims = tuple(executor.map(claim, range(8)))
    assert len([lease for lease in claims if lease is not None]) == 1


def test_expired_lease_cannot_commit_until_reclaimed(tmp_path: Path) -> None:
    database, _, state = _repository(tmp_path)
    clock = ["2099-01-01T00:00:00.000Z"]
    repository = SqliteTaskRepository(database, clock=lambda: clock[0])
    expired = repository.claim(
        state.task_id,
        owner="expired-worker",
        now=clock[0],
        expires_at="2099-01-01T00:00:01.000Z",
    )
    assert expired is not None
    clock[0] = "2099-01-01T00:00:02.000Z"

    with pytest.raises(TaskLeaseLost, match="stale owner"):
        repository.commit(transition(state, _event(state, TaskEventType.PLAN_REQUESTED)), lease=expired)

    replacement = repository.claim(
        state.task_id,
        owner="replacement-worker",
        now=clock[0],
        expires_at="2099-01-01T00:01:02.000Z",
    )
    assert replacement is not None
    assert repository.commit(transition(state, _event(state, TaskEventType.PLAN_REQUESTED)), lease=replacement).revision == 1


def test_lease_renewal_extends_same_fencing_token(tmp_path: Path) -> None:
    database, _, state = _repository(tmp_path)
    clock = ["2099-01-01T00:00:00.000Z"]
    repository = SqliteTaskRepository(database, clock=lambda: clock[0])
    lease = repository.claim(state.task_id, owner="worker", now=clock[0], expires_at="2099-01-01T00:00:02.000Z")
    assert lease is not None
    clock[0] = "2099-01-01T00:00:01.000Z"
    renewed = repository.renew(lease, now=clock[0], expires_at="2099-01-01T00:01:01.000Z")

    assert renewed.fencing_token == lease.fencing_token
    clock[0] = "2099-01-01T00:00:03.000Z"
    assert repository.commit(transition(state, _event(state, TaskEventType.PLAN_REQUESTED)), lease=lease).revision == 1


def test_one_hour_timer_is_indexed_and_survives_repository_restart(tmp_path: Path) -> None:
    database, repository, state = _repository(tmp_path)
    planning = repository.commit(transition(state, _event(state, TaskEventType.PLAN_REQUESTED)))
    ready = repository.commit(transition(planning, _event(planning, TaskEventType.PLAN_READY, plan_revision_id="planrev-one")))
    waiting = repository.commit(transition(ready, _event(ready, TaskEventType.WAIT_REQUESTED, wake_at="2099-01-01T01:00:00.000Z")))

    assert waiting.status is TaskStatus.WAITING
    assert repository.due("2099-01-01T00:59:59.999Z") == ()
    restarted = SqliteTaskRepository(CoreDatabase(database.path))
    assert restarted.due("2099-01-01T01:00:00.000Z") == (state.task_id,)
    with restarted.database.engine.connect() as connection:
        row = connection.execute(select(wait_conditions.c.kind, wait_conditions.c.status, wait_conditions.c.due_at)).one()
    assert tuple(row) == ("timer", "active", "2099-01-01T01:00:00.000Z")


def test_event_wait_is_persisted_and_only_matching_event_satisfies_it(tmp_path: Path) -> None:
    database, repository, state = _repository(tmp_path)
    planning = repository.commit(transition(state, _event(state, TaskEventType.PLAN_REQUESTED)))
    ready = repository.commit(transition(planning, _event(planning, TaskEventType.PLAN_READY, plan_revision_id="planrev-one")))
    waiting = repository.commit(transition(ready, _event(ready, TaskEventType.WAIT_REQUESTED, interaction_id="external:event:one")))
    restarted = SqliteTaskRepository(CoreDatabase(database.path))

    assert restarted.get_by_event_key("external:event:one") == waiting
    with pytest.raises(InvalidTaskTransition, match="does not match"):
        transition(waiting, _event(waiting, TaskEventType.EVENT_RECEIVED, interaction_id="external:event:other"))
    resumed = restarted.commit(transition(waiting, _event(waiting, TaskEventType.EVENT_RECEIVED, interaction_id="external:event:one")))
    assert resumed.status is TaskStatus.READY
    assert restarted.get_by_event_key("external:event:one") is None


def test_overdue_nonterminal_task_is_recoverable_for_timeout(tmp_path: Path) -> None:
    _database, repository, state = _repository(tmp_path)
    overdue = replace(state, deadline_at="2098-12-31T23:59:59.000Z")
    # Replace only the fixture row before any event so the recovery index reads the authoritative payload.
    with repository.database.engine.begin() as connection:
        connection.execute(task_runs.update().where(task_runs.c.task_id == state.task_id).values(payload_json=encode_task(overdue)))
    assert repository.recoverable("2099-01-01T00:00:00.000Z") == (state.task_id,)


def test_outbox_is_at_least_once_claimed_and_consumer_delivery_is_idempotent(tmp_path: Path) -> None:
    database, repository, state = _repository(tmp_path)
    repository.commit(transition(state, _event(state, TaskEventType.PLAN_REQUESTED)))
    outbox_repository = SqliteOutboxRepository(database)

    claimed = outbox_repository.claim(
        owner="dispatcher-one",
        now="2100-01-01T00:00:00.000Z",
        expires_at="2100-01-01T00:01:00.000Z",
    )
    assert len(claimed) == 1
    message = claimed[0]
    assert outbox_repository.delivered(message.message_id, "task-worker", "2100-01-01T00:00:01.000Z", "{}") is True
    assert outbox_repository.delivered(message.message_id, "task-worker", "2100-01-01T00:00:02.000Z", "{}") is False
    assert (
        outbox_repository.claim(
            owner="dispatcher-two",
            now="2100-01-01T00:02:00.000Z",
            expires_at="2100-01-01T00:03:00.000Z",
        )
        == ()
    )


def _repository(tmp_path: Path) -> tuple[CoreDatabase, SqliteTaskRepository, TaskRun]:
    database = CoreDatabase(tmp_path / "tasks.sqlite3")
    database.upgrade()
    repository = SqliteTaskRepository(database)
    created_at = "2099-01-01T00:00:00.000Z"
    contract = GoalContract(
        contract_id="contract-one",
        task_id="task-one",
        goal="durable test",
        scope=(),
        completion_conditions=("verified",),
        allowed_effects=(),
        reality_boundaries=(),
        version=1,
        created_at=created_at,
    )
    state = TaskRun("task-one", contract.contract_id, TaskStatus.CREATED, 0, created_at, created_at)
    repository.create(contract, state)
    return database, repository, state


def _event(
    state: TaskRun,
    event_type: TaskEventType,
    *,
    plan_revision_id: str | None = None,
    wake_at: str | None = None,
    interaction_id: str | None = None,
) -> TaskEvent:
    return TaskEvent(
        event_id=f"event-{state.revision + 1}-{event_type.value}",
        task_id=state.task_id,
        event_type=event_type,
        occurred_at=f"2099-01-01T00:00:{state.revision + 1:02d}.000Z",
        plan_revision_id=plan_revision_id,
        wake_at=wake_at,
        interaction_id=interaction_id,
    )
