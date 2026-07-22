from __future__ import annotations

from dataclasses import replace

from .acceptance_service import AcceptanceService
from .core.adapters.task_repository import SqliteTaskRepository, TaskConcurrencyError
from .core.domain import transition
from .core.domain.task import TaskEventType, TaskLease, TaskRun, TaskStatus
from .durable_action_executor import DurableActionExecutor
from .durable_task_models import DurableTaskResult, TaskEnginePolicy
from .durable_task_lease import LeaseHeartbeat
from .durable_task_driver import DurableTaskDriver
from .durable_task_planning import DurablePlanningCoordinator
from .durable_task_resumer import DurableTaskResumer
from .durable_task_results import DurableTaskResultFactory
from .durable_task_support import (
    after_seconds,
    event,
    load_planning,
    new_contract,
    new_task,
    now_iso,
    stable_id,
)
from .execution_service import StepExecutionService
from .models import CommandRequest, EffectAssessment
from .observation_service import ObservationService
from .planning_models import PlanningArtifacts
from .planning_service import PlanningService
from .recovery_service import RecoveryService
from .review_service import ReviewService
from .task_models import PlanExecutionResult, TaskPlan, TaskStep


class DurableTaskEngine:
    """Coordinates typed ports around the pure, persisted task transition function."""

    def __init__(
        self,
        *,
        repository: SqliteTaskRepository,
        planning: PlanningService,
        observation: ObservationService,
        reviews: ReviewService,
        execution: StepExecutionService,
        acceptance: AcceptanceService,
        recovery: RecoveryService,
        policy: TaskEnginePolicy | None = None,
    ) -> None:
        self.repository = repository
        self.planning = planning
        self.observation = observation
        self.reviews = reviews
        self.execution = execution
        self.action_executor = DurableActionExecutor(repository, execution)
        self.acceptance = acceptance
        self.recovery = recovery
        self.policy = policy or TaskEnginePolicy()
        self.results = DurableTaskResultFactory(repository)
        self.plans = DurablePlanningCoordinator(
            repository,
            planning,
            recovery,
            max_attempts=self.policy.max_attempts,
            retry_delay_seconds=self.policy.retry_delay_seconds,
        )
        self.driver = DurableTaskDriver(repository, acceptance, self.action_executor, observation, reviews, self.plans)
        self.resumer = DurableTaskResumer(repository, planning, self.plans, self.action_executor, self.policy)

    def start(self, request: CommandRequest, *, run_id: str) -> DurableTaskResult:
        created_at = now_iso()
        task_id = stable_id("task", run_id, request.utterance, created_at)
        contract = new_contract(task_id, request, created_at)
        state = new_task(task_id, contract, created_at, timeout_seconds=self.policy.task_timeout_seconds)
        self.repository.create(contract, state)
        lease = self.repository.claim(
            state.task_id,
            owner=f"command:{run_id}:planning",
            now=now_iso(),
            expires_at=after_seconds(self.policy.lease_seconds),
        )
        if lease is None:
            return self.results.build(state, request, None, message="task planning is currently owned by another worker")
        try:
            with self._heartbeat(lease) as heartbeat:
                state = self._commit(state, TaskEventType.PLAN_REQUESTED, lease=lease)
                planning = self.planning.plan(request)
                heartbeat.assert_valid()
                state = self.plans.accept(state, planning, lease=lease)
        finally:
            self.repository.release(lease, now=now_iso())
        return self._drive(state, request, run_id=run_id, planning=planning)

    def approve(self, interaction_id: str, request: CommandRequest, *, run_id: str) -> DurableTaskResult:
        state = self.repository.get_by_interaction(interaction_id)
        if state is None:
            return self._missing_interaction(interaction_id, request)
        contract = self.repository.contract(state.task_id)
        if contract is not None and contract.dry_run is True and not request.dry_run:
            request = replace(request, dry_run=True)
        planning = load_planning(state, self.repository, self.planning)
        if state.status is not TaskStatus.AWAITING_REVIEW or planning is None or planning.plan is None:
            return self.results.build(state, request, planning, message=f"interaction is not an approvable review while task is {state.status.value}")
        step = _step_by_id(planning.plan, state.current_step_id)
        if step is None:
            return self.results.build(state, request, planning, message="pending review is missing its bound step")
        pre = self.observation.observe(plan=planning.plan, step=step, phase="pre", level="O0")
        review, record = self.reviews.review_step(planning.plan, step, pre)
        current_id = "review_" + record.step_safety_review_id.removeprefix("srev_")
        if request.dry_run:
            return self.results.build(
                state, request, planning, review=review, review_id=interaction_id, message="approval preview did not consume the pending review"
            )
        lease = self.repository.claim(
            state.task_id,
            owner=f"command:{run_id}:approval",
            now=now_iso(),
            expires_at=after_seconds(self.policy.lease_seconds),
        )
        if lease is None:
            return self.results.build(state, request, planning, message="task approval is currently owned by another worker")
        try:
            with self._heartbeat(lease):
                if current_id != interaction_id or not review.allowed:
                    refreshed = event(
                        state,
                        TaskEventType.REVIEW_REQUIRED,
                        interaction_id=current_id,
                        step_id=step.id,
                        reason=review.reason,
                    )
                    state = self.repository.commit(transition(state, refreshed), lease=lease)
                    return self.results.build(
                        state,
                        request,
                        planning,
                        review=review,
                        review_id=current_id,
                        message="safety binding changed; a fresh approval is required",
                    )
                revised = (
                    replace(
                        contract,
                        contract_id=stable_id("contract", contract.task_id, contract.version + 1, "execution-intent", False),
                        version=contract.version + 1,
                        created_at=now_iso(),
                        dry_run=False,
                    )
                    if contract is not None and contract.dry_run is None
                    else None
                )
                state = self.repository.commit(
                    transition(
                        state,
                        event(
                            state,
                            TaskEventType.REVIEW_APPROVED,
                            step_id=step.id,
                            reason="user approved the bound action",
                        ),
                    ),
                    contract_version=revised,
                    lease=lease,
                )
        finally:
            self.repository.release(lease, now=now_iso())
        return self._drive(state, request, run_id=run_id, planning=planning, approved_review_id=interaction_id)

    def provide_input(self, interaction_id: str, detail: str, request: CommandRequest, *, run_id: str) -> DurableTaskResult:
        state = self.repository.get_by_interaction(interaction_id)
        if state is None:
            return self._missing_interaction(interaction_id, request)
        if state.status is not TaskStatus.AWAITING_CLARIFICATION:
            return self.results.build(
                state, request, load_planning(state, self.repository, self.planning), message="interaction does not accept supplemental input"
            )
        if not detail.strip():
            return self.results.build(state, request, None, message="supplemental input is required")
        contract = self.repository.contract(state.task_id)
        utterance = f"{contract.goal if contract is not None else ''}\n\nAdditional user detail: {detail.strip()}".strip()
        lease = self.repository.claim(
            state.task_id,
            owner=f"command:{run_id}:clarification",
            now=now_iso(),
            expires_at=after_seconds(self.policy.lease_seconds),
        )
        if lease is None:
            return self.results.build(state, request, None, message="task clarification is currently owned by another worker")
        persisted_dry_run = request.dry_run if contract is None or contract.dry_run is None else contract.dry_run or request.dry_run
        resumed = replace(
            request,
            utterance=utterance,
            dry_run=persisted_dry_run,
            review_id=None,
            supplemental_input=None,
            approve=False,
        )
        try:
            with self._heartbeat(lease) as heartbeat:
                revised = (
                    replace(
                        contract,
                        contract_id=stable_id("contract", contract.task_id, contract.version + 1, utterance),
                        goal=utterance,
                        version=contract.version + 1,
                        created_at=now_iso(),
                        dry_run=persisted_dry_run,
                    )
                    if contract is not None
                    else None
                )
                state = self.repository.commit(
                    transition(state, event(state, TaskEventType.CLARIFICATION_PROVIDED, reason="user supplied clarification")),
                    contract_version=revised,
                    lease=lease,
                )
                planning = self.planning.plan(resumed)
                heartbeat.assert_valid()
                state = self.plans.accept(state, planning, lease=lease)
        finally:
            self.repository.release(lease, now=now_iso())
        return self._drive(state, resumed, run_id=run_id, planning=planning)

    def reject(self, interaction_id: str, request: CommandRequest) -> DurableTaskResult:
        state = self.repository.get_by_interaction(interaction_id)
        if state is None:
            return self._missing_interaction(interaction_id, request)
        if state.status is TaskStatus.AWAITING_REVIEW:
            state = self._commit(state, TaskEventType.REVIEW_REJECTED, reason="user rejected the action")
        elif state.status is TaskStatus.AWAITING_CLARIFICATION:
            state = self._commit(state, TaskEventType.CANCEL_REQUESTED, reason="user cancelled clarification")
            state = self._commit(state, TaskEventType.CANCELLATION_CONFIRMED, reason="no external action remained active")
        return self.results.build(state, request, load_planning(state, self.repository, self.planning), message="pending interaction rejected by user")

    def control(
        self,
        task_id: str,
        operation: str,
        *,
        expected_revision: int,
        owner: str | None = None,
        reason: str = "",
    ) -> TaskRun:
        state = self.repository.get(task_id)
        if state is None:
            raise KeyError(task_id)
        if state.revision != expected_revision:
            raise TaskConcurrencyError(f"expected revision {expected_revision}, found {state.revision}")
        events = {
            "pause": TaskEventType.PAUSE_REQUESTED,
            "resume": TaskEventType.RESUME_REQUESTED,
            "cancel": TaskEventType.CANCEL_REQUESTED,
            "takeover": TaskEventType.TAKEOVER_REQUESTED,
            "release": TaskEventType.RELEASE_REQUESTED,
        }
        kind = events.get(operation)
        if kind is None:
            raise ValueError(f"unsupported task control operation: {operation}")
        action_active = state.status in {TaskStatus.RUNNING, TaskStatus.RECONCILING}
        state = self._commit(state, kind, owner=owner, reason=reason or f"user requested {operation}")
        if kind is TaskEventType.CANCEL_REQUESTED and not action_active:
            return self._commit(state, TaskEventType.CANCELLATION_CONFIRMED, reason="no external action remained active")
        return state

    def wake(self, task_id: str, request: CommandRequest, *, run_id: str) -> DurableTaskResult:
        state = self.repository.get(task_id)
        if state is None:
            raise KeyError(task_id)
        if state.status in {TaskStatus.WAITING, TaskStatus.RETRY_WAIT}:
            self.resume_task(task_id)
            refreshed = self.repository.get(task_id)
            if refreshed is not None:
                state = refreshed
        return self._drive(state, request, run_id=run_id, planning=load_planning(state, self.repository, self.planning))

    def pending_interactions(self) -> tuple[TaskRun, ...]:
        return self.repository.list(statuses=(TaskStatus.AWAITING_REVIEW, TaskStatus.AWAITING_CLARIFICATION))

    def wait_for_event(self, task_id: str, event_key: str, *, expected_revision: int, reason: str = "") -> TaskRun:
        state = self.repository.get(task_id)
        if state is None:
            raise KeyError(task_id)
        if state.revision != expected_revision:
            raise TaskConcurrencyError(f"expected revision {expected_revision}, found {state.revision}")
        return self._commit(
            state,
            TaskEventType.WAIT_REQUESTED,
            interaction_id=event_key,
            reason=reason or "task is waiting for an external event",
        )

    def signal_event(self, event_key: str, request: CommandRequest, *, run_id: str) -> DurableTaskResult:
        state = self.repository.get_by_event_key(event_key)
        if state is None:
            raise KeyError(event_key)
        state = self._commit(
            state,
            TaskEventType.EVENT_RECEIVED,
            interaction_id=event_key,
            reason="persisted external event was received",
        )
        return self._drive(state, request, run_id=run_id, planning=load_planning(state, self.repository, self.planning))

    def resume_task(self, task_id: str) -> None:
        outcome = self.resumer.prepare(task_id)
        if outcome is not None:
            self._drive(outcome.state, outcome.request, run_id=outcome.run_id, planning=outcome.planning)

    def _drive(
        self,
        state: TaskRun,
        request: CommandRequest,
        *,
        run_id: str,
        planning: PlanningArtifacts | None,
        approved_review_id: str | None = None,
    ) -> DurableTaskResult:
        contract = self.repository.contract(state.task_id)
        if contract is not None and contract.dry_run is True and not request.dry_run:
            request = replace(request, dry_run=True)
        planning = self._resolve_planning(state, planning)
        if planning is None or planning.plan is None or state.status not in {TaskStatus.READY, TaskStatus.RUNNING, TaskStatus.VERIFYING}:
            return self.results.build(state, request, planning)
        lease = self.repository.claim(state.task_id, owner=f"command:{run_id}", now=now_iso(), expires_at=after_seconds(self.policy.lease_seconds))
        if lease is None:
            return self.results.build(state, request, planning, message="task is currently owned by another worker")
        execution: PlanExecutionResult | None = None
        review: EffectAssessment | None = None
        review_id: str | None = None
        try:
            with self._heartbeat(lease) as heartbeat:
                if contract is None or contract.dry_run is None:
                    state = self._commit(
                        state,
                        TaskEventType.PAUSE_REQUESTED,
                        lease=lease,
                        reason="persisted execution intent is unknown; explicit user confirmation is required",
                    )
                    return self.results.build(
                        state,
                        request,
                        planning,
                        message="task paused because its persisted execution intent is unknown",
                    )
                for _ in range(self.policy.max_steps + self.policy.max_attempts):
                    heartbeat.assert_valid()
                    state, planning, execution, review, review_id, approved_review_id, should_break = self.driver.drive_once(
                        state,
                        planning,
                        request,
                        run_id=run_id,
                        lease=lease,
                        execution=execution,
                        review=review,
                        review_id=review_id,
                        approved_review_id=approved_review_id,
                    )
                    if should_break:
                        break
            return self.results.build(
                state,
                request,
                planning,
                execution=execution,
                review=review,
                review_id=review_id,
                run_id=run_id,
            )
        finally:
            self.repository.release(lease, now=now_iso())

    def _commit(self, state: TaskRun, kind: TaskEventType, *, lease: TaskLease | None = None, reason: str = "", **fields: object) -> TaskRun:
        return self.repository.commit(transition(state, event(state, kind, reason=reason, **fields)), lease=lease)

    def _resolve_planning(self, state: TaskRun, planning: PlanningArtifacts | None) -> PlanningArtifacts | None:
        return planning or load_planning(state, self.repository, self.planning)

    def _missing_interaction(self, interaction_id: str, request: CommandRequest) -> DurableTaskResult:
        timestamp = now_iso()
        contract = new_contract(f"missing_{interaction_id}", request, timestamp)
        state = replace(new_task(contract.task_id, contract, timestamp), status=TaskStatus.FAILED)
        return DurableTaskResult(task=state, request=request, planning=None, review_id=interaction_id, message="review request not found")

    def _heartbeat(self, lease: TaskLease) -> LeaseHeartbeat:
        return LeaseHeartbeat(
            self.repository,
            lease,
            lease_seconds=self.policy.lease_seconds,
            interval_seconds=self.policy.heartbeat_seconds,
        )


def _step_by_id(plan: TaskPlan, step_id: str | None) -> TaskStep | None:
    return next((step for step in plan.steps if step.id == step_id), None)
