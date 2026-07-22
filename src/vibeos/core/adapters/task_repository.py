from __future__ import annotations

from collections.abc import Callable

from sqlalchemy import and_, func, insert, or_, select, update
from sqlalchemy.engine import Connection

from .database import CoreDatabase, FaultInjector
from .metadata import (
    action_proposals,
    evidence_bundles,
    goal_contracts,
    plan_revisions,
    task_action_receipts,
    task_leases,
    task_runs,
    task_steps,
    wait_conditions,
)
from .task_codec import encode_contract, encode_task
from .task_record_decoder import decode_contract_record, decode_task_record
from .task_persistence import (
    insert_contract_version,
    write_artifacts,
    write_current_state,
    write_effects,
    write_event,
    write_step_state,
    write_terminal,
    write_wait_state,
)
from .task_rows import TERMINAL_TASK_STATUSES, evidence_from_row, proposal_from_row, receipt_from_row, step_from_row, utc_now
from ..domain.task import (
    ActionProposal,
    ActionReceipt,
    Attempt,
    EvidenceBundle,
    GoalContract,
    PlanRevision,
    Step,
    TaskLease,
    TaskRun,
    TaskStatus,
    TaskTransition,
)


class TaskRepositoryError(RuntimeError):
    pass


class TaskNotFound(TaskRepositoryError):
    pass


class TaskConcurrencyError(TaskRepositoryError):
    pass


class TaskLeaseLost(TaskRepositoryError):
    pass


class SqliteTaskRepository:
    """The single transactional authority for durable tasks and dispatch intent."""

    def __init__(
        self,
        database: CoreDatabase,
        *,
        fault_injector: FaultInjector | None = None,
        clock: Callable[[], str] | None = None,
    ) -> None:
        self.database = database
        self._fault_injector = fault_injector
        self._clock = clock or utc_now

    def create(self, contract: GoalContract, state: TaskRun) -> None:
        if state.revision != 0 or state.status is not TaskStatus.CREATED:
            raise ValueError("a new task must start at created revision zero")
        contract_json = encode_contract(contract)
        state_json = encode_task(state)
        with self.database.engine.begin() as connection:
            connection.execute(
                insert(goal_contracts).values(
                    contract_id=contract.contract_id,
                    task_id=contract.task_id,
                    version=contract.version,
                    schema_version=contract.schema_version,
                    payload_json=contract_json,
                    created_at=contract.created_at,
                )
            )
            connection.execute(
                insert(task_runs).values(
                    task_id=state.task_id,
                    contract_id=state.contract_id,
                    status=state.status.value,
                    revision=state.revision,
                    schema_version=state.schema_version,
                    payload_json=state_json,
                    created_at=state.created_at,
                    updated_at=state.updated_at,
                )
            )
            connection.execute(insert(task_leases).values(task_id=state.task_id, fencing_token=0, updated_at=state.updated_at))
            self._inject("after_task_create")

    def get(self, task_id: str) -> TaskRun | None:
        with self.database.engine.connect() as connection:
            row = (
                connection.execute(select(task_runs.c.schema_version, task_runs.c.status, task_runs.c.payload_json).where(task_runs.c.task_id == task_id))
                .mappings()
                .one_or_none()
            )
        return decode_task_record(str(row["schema_version"]), str(row["status"]), str(row["payload_json"])) if row else None

    def get_by_interaction(self, interaction_id: str) -> TaskRun | None:
        with self.database.engine.connect() as connection:
            row = (
                connection.execute(
                    select(task_runs.c.schema_version, task_runs.c.status, task_runs.c.payload_json).where(task_runs.c.pending_interaction_id == interaction_id)
                )
                .mappings()
                .one_or_none()
            )
        return decode_task_record(str(row["schema_version"]), str(row["status"]), str(row["payload_json"])) if row else None

    def get_by_event_key(self, event_key: str) -> TaskRun | None:
        with self.database.engine.connect() as connection:
            task_id = connection.execute(
                select(wait_conditions.c.task_id)
                .where(wait_conditions.c.event_key == event_key, wait_conditions.c.status == "active")
                .order_by(wait_conditions.c.created_at.desc())
                .limit(1)
            ).scalar_one_or_none()
        return self.get(str(task_id)) if task_id is not None else None

    def contract(self, task_id: str) -> GoalContract | None:
        with self.database.engine.connect() as connection:
            row = (
                connection.execute(
                    select(goal_contracts.c.schema_version, goal_contracts.c.payload_json, task_runs.c.status)
                    .join(task_runs, task_runs.c.task_id == goal_contracts.c.task_id)
                    .where(goal_contracts.c.task_id == task_id)
                    .order_by(goal_contracts.c.version.desc())
                    .limit(1)
                )
                .mappings()
                .one_or_none()
            )
        if row is None:
            return None
        return decode_contract_record(str(row["schema_version"]), str(row["status"]), str(row["payload_json"]))

    def add_contract_version(self, contract: GoalContract) -> None:
        with self.database.engine.begin() as connection:
            self._insert_contract_version(connection, contract)

    def list(self, *, statuses: tuple[TaskStatus, ...] = (), limit: int = 100) -> tuple[TaskRun, ...]:
        statement = (
            select(task_runs.c.schema_version, task_runs.c.status, task_runs.c.payload_json).order_by(task_runs.c.updated_at.desc()).limit(max(0, limit))
        )
        if statuses:
            statement = statement.where(task_runs.c.status.in_([status.value for status in statuses]))
        with self.database.engine.connect() as connection:
            rows = connection.execute(statement).mappings()
            return tuple(decode_task_record(str(row["schema_version"]), str(row["status"]), str(row["payload_json"])) for row in rows)

    def commit(
        self,
        transition: TaskTransition,
        *,
        plan: PlanRevision | None = None,
        steps: tuple[Step, ...] = (),
        attempt: Attempt | None = None,
        proposal: ActionProposal | None = None,
        receipt: ActionReceipt | None = None,
        evidence: EvidenceBundle | None = None,
        evidences: tuple[EvidenceBundle, ...] = (),
        contract_version: GoalContract | None = None,
        lease: TaskLease | None = None,
    ) -> TaskRun:
        state_json = encode_task(transition.state)
        with self.database.engine.begin() as connection:
            self._assert_lease(connection, transition.state.task_id, lease)
            changed = connection.execute(
                update(task_runs)
                .where(task_runs.c.task_id == transition.state.task_id, task_runs.c.revision == transition.previous_revision)
                .values(
                    status=transition.state.status.value,
                    revision=transition.state.revision,
                    active_plan_revision_id=transition.state.active_plan_revision_id,
                    current_step_id=transition.state.current_step_id,
                    pending_interaction_id=transition.state.pending_interaction_id,
                    next_wake_at=transition.state.next_wake_at,
                    schema_version=transition.state.schema_version,
                    payload_json=state_json,
                    updated_at=transition.state.updated_at,
                )
            ).rowcount
            if changed != 1:
                raise TaskConcurrencyError(f"task revision changed while committing {transition.state.task_id}")
            write_current_state(connection, transition, state_json)
            self._inject("after_task_state")
            write_event(connection, transition)
            self._inject("after_task_event")
            write_effects(connection, transition)
            if contract_version is not None:
                self._insert_contract_version(connection, contract_version)
            write_wait_state(connection, transition)
            write_step_state(connection, transition)
            write_artifacts(connection, plan, steps, attempt, proposal, receipt, evidence, evidences)
            write_terminal(connection, transition.state)
            self._inject("after_task_outbox")
        return transition.state

    def plan_payload(self, plan_revision_id: str) -> str | None:
        with self.database.engine.connect() as connection:
            raw = connection.execute(select(plan_revisions.c.payload_json).where(plan_revisions.c.plan_revision_id == plan_revision_id)).scalar_one_or_none()
        return str(raw) if raw is not None else None

    def steps(self, task_id: str, plan_revision_id: str | None = None) -> tuple[Step, ...]:
        statement = select(task_steps).where(task_steps.c.task_id == task_id).order_by(task_steps.c.ordinal)
        if plan_revision_id is not None:
            statement = statement.where(task_steps.c.plan_revision_id == plan_revision_id)
        with self.database.engine.connect() as connection:
            rows = connection.execute(statement).mappings()
            return tuple(step_from_row(row) for row in rows)

    def receipt_for(self, idempotency_key: str) -> ActionReceipt | None:
        with self.database.engine.connect() as connection:
            row = connection.execute(select(task_action_receipts).where(task_action_receipts.c.idempotency_key == idempotency_key)).mappings().one_or_none()
        return receipt_from_row(row) if row is not None else None

    def proposal_for(self, idempotency_key: str) -> ActionProposal | None:
        with self.database.engine.connect() as connection:
            row = connection.execute(select(action_proposals).where(action_proposals.c.idempotency_key == idempotency_key)).mappings().one_or_none()
        return proposal_from_row(row) if row is not None else None

    def receipts(self, task_id: str, plan_revision_id: str | None = None) -> tuple[ActionReceipt, ...]:
        statement = select(task_action_receipts).where(task_action_receipts.c.task_id == task_id).order_by(task_action_receipts.c.occurred_at)
        if plan_revision_id is not None:
            statement = (
                select(task_action_receipts)
                .join(task_steps, task_steps.c.idempotency_key == task_action_receipts.c.idempotency_key)
                .where(task_action_receipts.c.task_id == task_id, task_steps.c.plan_revision_id == plan_revision_id)
                .order_by(task_action_receipts.c.occurred_at)
            )
        with self.database.engine.connect() as connection:
            return tuple(receipt_from_row(row) for row in connection.execute(statement).mappings())

    def evidence(self, task_id: str) -> tuple[EvidenceBundle, ...]:
        statement = select(evidence_bundles).where(evidence_bundles.c.task_id == task_id).order_by(evidence_bundles.c.observed_at)
        with self.database.engine.connect() as connection:
            return tuple(evidence_from_row(row) for row in connection.execute(statement).mappings())

    def claim(self, task_id: str, *, owner: str, now: str, expires_at: str) -> TaskLease | None:
        with self.database.engine.begin() as connection:
            changed = connection.execute(
                update(task_leases)
                .where(
                    task_leases.c.task_id == task_id,
                    or_(task_leases.c.owner.is_(None), task_leases.c.expires_at <= now, task_leases.c.owner == owner),
                )
                .values(
                    owner=owner,
                    expires_at=expires_at,
                    fencing_token=task_leases.c.fencing_token + 1,
                    updated_at=now,
                )
            ).rowcount
            if changed != 1:
                return None
            row = connection.execute(select(task_leases).where(task_leases.c.task_id == task_id)).mappings().one()
        return TaskLease(task_id=task_id, owner=owner, expires_at=expires_at, fencing_token=int(row["fencing_token"]))

    def release(self, lease: TaskLease, *, now: str) -> bool:
        with self.database.engine.begin() as connection:
            changed = connection.execute(
                update(task_leases)
                .where(
                    task_leases.c.task_id == lease.task_id,
                    task_leases.c.owner == lease.owner,
                    task_leases.c.fencing_token == lease.fencing_token,
                )
                .values(owner=None, expires_at=None, updated_at=now)
            ).rowcount
        return int(changed or 0) == 1

    def renew(self, lease: TaskLease, *, now: str, expires_at: str) -> TaskLease:
        with self.database.engine.begin() as connection:
            changed = connection.execute(
                update(task_leases)
                .where(
                    task_leases.c.task_id == lease.task_id,
                    task_leases.c.owner == lease.owner,
                    task_leases.c.fencing_token == lease.fencing_token,
                    task_leases.c.expires_at.is_not(None),
                    task_leases.c.expires_at > now,
                )
                .values(expires_at=expires_at, updated_at=now)
            ).rowcount
        if changed != 1:
            raise TaskLeaseLost(f"lease renewal rejected expired or stale owner for {lease.task_id}")
        return TaskLease(lease.task_id, lease.owner, expires_at, lease.fencing_token)

    def due(self, now: str, *, limit: int = 100) -> tuple[str, ...]:
        statuses = (TaskStatus.WAITING.value, TaskStatus.RETRY_WAIT.value)
        statement = (
            select(task_runs.c.task_id)
            .where(task_runs.c.status.in_(statuses), task_runs.c.next_wake_at.is_not(None), task_runs.c.next_wake_at <= now)
            .order_by(task_runs.c.next_wake_at)
            .limit(limit)
        )
        with self.database.engine.connect() as connection:
            return tuple(str(item) for item in connection.execute(statement).scalars())

    def recoverable(self, now: str, *, limit: int = 100) -> tuple[str, ...]:
        active = (
            TaskStatus.CREATED.value,
            TaskStatus.PLANNING.value,
            TaskStatus.READY.value,
            TaskStatus.RUNNING.value,
            TaskStatus.VERIFYING.value,
            TaskStatus.REPLANNING.value,
            TaskStatus.RECONCILING.value,
            TaskStatus.CANCEL_REQUESTED.value,
        )
        statement = (
            select(task_runs.c.task_id)
            .where(
                or_(
                    task_runs.c.status.in_(active),
                    and_(
                        task_runs.c.status.in_((TaskStatus.WAITING.value, TaskStatus.RETRY_WAIT.value)),
                        task_runs.c.next_wake_at.is_not(None),
                        task_runs.c.next_wake_at <= now,
                    ),
                    and_(
                        ~task_runs.c.status.in_([status.value for status in TaskStatus if status in TERMINAL_TASK_STATUSES]),
                        func.json_extract(task_runs.c.payload_json, "$.deadline_at").is_not(None),
                        func.json_extract(task_runs.c.payload_json, "$.deadline_at") <= now,
                    ),
                )
            )
            .order_by(task_runs.c.updated_at)
            .limit(limit)
        )
        with self.database.engine.connect() as connection:
            return tuple(str(item) for item in connection.execute(statement).scalars())

    def reconcile_candidates(self, *, limit: int = 100) -> tuple[ActionProposal, ...]:
        statement = (
            select(action_proposals).where(action_proposals.c.status.in_(("dispatching", "unknown"))).order_by(action_proposals.c.updated_at).limit(limit)
        )
        with self.database.engine.connect() as connection:
            rows = connection.execute(statement).mappings()
            return tuple(proposal_from_row(row) for row in rows)

    def unresolved_proposal(self, task_id: str) -> ActionProposal | None:
        statement = (
            select(action_proposals)
            .where(action_proposals.c.task_id == task_id, action_proposals.c.status.in_(("dispatching", "unknown")))
            .order_by(action_proposals.c.updated_at.desc())
            .limit(1)
        )
        with self.database.engine.connect() as connection:
            row = connection.execute(statement).mappings().one_or_none()
        return proposal_from_row(row) if row is not None else None

    def _assert_lease(self, connection: Connection, task_id: str, lease: TaskLease | None) -> None:
        if lease is None:
            return
        valid = connection.execute(
            select(task_leases.c.task_id).where(
                task_leases.c.task_id == task_id,
                task_leases.c.owner == lease.owner,
                task_leases.c.fencing_token == lease.fencing_token,
                task_leases.c.expires_at.is_not(None),
                task_leases.c.expires_at > self._clock(),
            )
        ).scalar_one_or_none()
        if valid is None:
            raise TaskLeaseLost(f"lease fencing rejected stale owner for {task_id}")

    def _insert_contract_version(self, connection: Connection, contract: GoalContract) -> None:
        current_version = connection.execute(
            select(goal_contracts.c.version).where(goal_contracts.c.task_id == contract.task_id).order_by(goal_contracts.c.version.desc()).limit(1)
        ).scalar_one_or_none()
        try:
            insert_contract_version(connection, contract, int(current_version) if current_version is not None else None)
        except ValueError as exc:
            raise TaskConcurrencyError(str(exc)) from exc

    def _inject(self, stage: str) -> None:
        if self._fault_injector is not None:
            self._fault_injector(stage)
