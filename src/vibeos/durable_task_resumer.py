from __future__ import annotations

from dataclasses import dataclass

from .core.adapters.task_repository import SqliteTaskRepository
from .core.domain import allowed_events, transition
from .core.domain.task import TaskEventType, TaskLease, TaskRun, TaskStatus
from .durable_action_executor import DurableActionExecutor
from .durable_task_lease import LeaseHeartbeat
from .durable_task_models import TaskEnginePolicy
from .durable_task_planning import DurablePlanningCoordinator
from .durable_task_recovery import DurableInterruptionRecovery
from .durable_task_support import after_seconds, event, load_planning, now_iso, stable_id
from .models import CommandRequest
from .planning_models import PlanningArtifacts
from .planning_service import PlanningService


@dataclass(frozen=True)
class ResumeOutcome:
    state: TaskRun
    request: CommandRequest
    run_id: str
    planning: PlanningArtifacts | None


class DurableTaskResumer:
    """Restores one leased task to a state the normal bounded driver can consume."""

    def __init__(
        self,
        repository: SqliteTaskRepository,
        planning: PlanningService,
        plans: DurablePlanningCoordinator,
        action_executor: DurableActionExecutor,
        policy: TaskEnginePolicy,
    ) -> None:
        self.repository = repository
        self.planning = planning
        self.plans = plans
        self.action_executor = action_executor
        self.policy = policy
        self.interruptions = DurableInterruptionRecovery(repository)

    def prepare(self, task_id: str) -> ResumeOutcome | None:
        state = self.repository.get(task_id)
        contract = self.repository.contract(task_id)
        if state is None or contract is None:
            return None
        lease = self.repository.claim(
            task_id,
            owner=f"recovery:{id(self)}",
            now=now_iso(),
            expires_at=after_seconds(self.policy.lease_seconds),
        )
        if lease is None:
            return None
        if contract.dry_run is None:
            try:
                if TaskEventType.PAUSE_REQUESTED in allowed_events(state.status):
                    self._commit(
                        state,
                        TaskEventType.PAUSE_REQUESTED,
                        lease,
                        reason="persisted execution intent is unknown; explicit user confirmation is required",
                    )
            finally:
                self.repository.release(lease, now=now_iso())
            return None
        request = CommandRequest(contract.goal, dry_run=contract.dry_run, transport="daemon-recovery")
        should_drive = True
        resolved: PlanningArtifacts | None = None
        try:
            with self._heartbeat(lease) as heartbeat:
                state, should_drive = self._advance_time(state, lease)
                if should_drive:
                    state, resolved, should_drive = self._reconcile(state, request, lease)
                if should_drive and self.interruptions.resolve(state, lease):
                    should_drive = False
                if should_drive:
                    state, resolved = self._ensure_plan(state, request, resolved, lease, heartbeat)
        finally:
            self.repository.release(lease, now=now_iso())
        if not should_drive:
            return None
        planning = resolved or load_planning(state, self.repository, self.planning)
        return ResumeOutcome(state, request, stable_id("run_recovery", task_id, state.revision), planning)

    def _advance_time(self, state: TaskRun, lease: TaskLease) -> tuple[TaskRun, bool]:
        if state.deadline_at is not None and state.deadline_at <= now_iso():
            self._commit(state, TaskEventType.TIMEOUT, lease, reason="task deadline elapsed")
            return state, False
        retry_elapsed = state.status is TaskStatus.RETRY_WAIT
        if state.status not in {TaskStatus.WAITING, TaskStatus.RETRY_WAIT}:
            return state, True
        state = self._commit(state, TaskEventType.TIMER_ELAPSED, lease, reason="persisted timer became due after scheduler scan")
        if retry_elapsed:
            state = self._commit(
                state,
                TaskEventType.REPLAN_REQUESTED,
                lease,
                reason="retry timer elapsed; a fresh plan revision is required",
            )
        return state, True

    def _reconcile(
        self,
        state: TaskRun,
        request: CommandRequest,
        lease: TaskLease,
    ) -> tuple[TaskRun, PlanningArtifacts | None, bool]:
        if state.status is not TaskStatus.RECONCILING:
            return state, None, True
        planning = load_planning(state, self.repository, self.planning)
        if planning is None or planning.plan is None:
            return state, planning, True
        state = self.action_executor.resume_reconciliation(
            state,
            planning.plan,
            request,
            run_id=stable_id("run_reconcile", state.task_id, state.revision),
            lease=lease,
        )
        return state, planning, state.status is not TaskStatus.PAUSED

    def _ensure_plan(
        self,
        state: TaskRun,
        request: CommandRequest,
        resolved: PlanningArtifacts | None,
        lease: TaskLease,
        heartbeat: LeaseHeartbeat,
    ) -> tuple[TaskRun, PlanningArtifacts | None]:
        if state.status is TaskStatus.CREATED:
            state = self._commit(state, TaskEventType.PLAN_REQUESTED, lease, reason="scheduler recovered an unplanned task")
        if state.status not in {TaskStatus.PLANNING, TaskStatus.REPLANNING}:
            return state, resolved
        previous = load_planning(state, self.repository, self.planning)
        if state.status is TaskStatus.REPLANNING and previous is not None:
            resolved = self.planning.replan(previous, request, (), (), ())
        else:
            resolved = self.planning.plan(request)
        heartbeat.assert_valid()
        return self.plans.accept(state, resolved, lease=lease), resolved

    def _commit(self, state: TaskRun, kind: TaskEventType, lease: TaskLease, *, reason: str) -> TaskRun:
        return self.repository.commit(transition(state, event(state, kind, reason=reason)), lease=lease)

    def _heartbeat(self, lease: TaskLease) -> LeaseHeartbeat:
        return LeaseHeartbeat(
            self.repository,
            lease,
            lease_seconds=self.policy.lease_seconds,
            interval_seconds=self.policy.heartbeat_seconds,
        )
