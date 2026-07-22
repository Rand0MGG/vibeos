from __future__ import annotations

from collections.abc import Callable
from typing import Protocol

from .core.adapters.task_repository import SqliteTaskRepository
from .core.domain import transition
from .core.domain.task import GoalContract, TaskEventType, TaskLease, TaskRun, TaskStatus, TERMINAL_STATUSES
from .durable_action_executor import DurableActionExecutor
from .durable_task_engine import DurableTaskEngine
from .durable_task_support import after_seconds, event, load_planning, now_iso, stable_id
from .model_gateway.contracts import (
    CancellationBinding,
    ContextItem,
    ContextManifest,
    GatewayResult,
    ModelBudget,
    ProviderRoute,
    TaskAttemptBinding,
    facts_digest,
)
from .model_gateway.secrets import ProviderRouteRepository
from .models import CommandRequest
from .observation_service import ObservationService
from .planning_service import PlanningService
from .system_service_contracts import FIXTURE_UNIT, ServiceFactsV2
from .system_service_provider import ServiceProviderError
from .system_service_reconciliation import SystemServiceActionReconciler
from .system_service_task_support import FIXED_SERVICE_GOAL, FIXED_SERVICE_GOAL_EN, SCENARIO_SCOPE, SystemServiceEvidenceLedger, SystemServiceTaskResult
from .system_service_task_support import build_system_service_planning, facts_fresh, is_goal04_contract, observe_for_verification, validate_initial_facts
from .task_validation import validate_plan


class ServiceDiagnosisGateway(Protocol):
    def diagnose_service(
        self,
        *,
        route: ProviderRoute,
        binding: TaskAttemptBinding,
        facts: ServiceFactsV2,
        budget: ModelBudget,
        cancellation: CancellationBinding,
        request_id: str,
    ) -> GatewayResult: ...


class SystemServiceTaskService:
    """Bounded Goal04 slice using the canonical task store and action executor."""

    def __init__(
        self,
        *,
        engine: DurableTaskEngine,
        repository: SqliteTaskRepository,
        planning: PlanningService,
        observation: ObservationService,
        gateway: ServiceDiagnosisGateway,
        route_repository: ProviderRouteRepository | None = None,
        checkpoint: Callable[[str], None] | None = None,
        lease_seconds: int = 30,
    ) -> None:
        self.engine = engine
        self.repository = repository
        self.planning = planning
        self.observation = observation
        self.gateway = gateway
        self.route_repository = route_repository or ProviderRouteRepository()
        self.checkpoint = checkpoint or (lambda _stage: None)
        self.lease_seconds = lease_seconds
        self.evidence = SystemServiceEvidenceLedger(repository)
        self.action_executor = DurableActionExecutor(
            repository,
            engine.execution,
            SystemServiceActionReconciler(observation),
            checkpoint=self.checkpoint,
        )

    def start(self, *, goal: str, route: ProviderRoute, run_id: str) -> SystemServiceTaskResult:
        timestamp = now_iso()
        task_id = stable_id("task_goal04_service", run_id, timestamp)
        contract = GoalContract(
            contract_id=stable_id("contract_goal04_service", task_id, goal),
            task_id=task_id,
            goal=goal.strip(),
            scope=(SCENARIO_SCOPE, f"unit:{FIXTURE_UNIT}", f"model-route:{route.route_id}"),
            completion_conditions=("independent observation proves active/running with a live main process",),
            allowed_effects=("E0 fixed-unit observation", "E1 one fixed-unit start or restart"),
            reality_boundaries=("controller stops before task start", "unknown action outcome requires systemd reconciliation"),
            version=1,
            created_at=timestamp,
            dry_run=False,
        )
        state = TaskRun(
            task_id=task_id,
            contract_id=contract.contract_id,
            status=TaskStatus.CREATED,
            revision=0,
            created_at=timestamp,
            updated_at=timestamp,
            deadline_at=after_seconds(300),
        )
        self.repository.create(contract, state)
        lease = self._claim(task_id, run_id)
        if lease is None:
            return self._result(state)
        try:
            state = self._commit(state, TaskEventType.PLAN_REQUESTED, lease, reason="fixed Goal04 service task entered planning")
            if goal.strip() not in {FIXED_SERVICE_GOAL, FIXED_SERVICE_GOAL_EN}:
                state = self._commit(
                    state,
                    TaskEventType.CLARIFICATION_REQUIRED,
                    lease,
                    reason=f"target is ambiguous; only {FIXTURE_UNIT} is allowed",
                    interaction_id=stable_id("clarify_goal04_service", task_id),
                )
                return self._result(state)
            return self._drive(state, route, run_id, lease)
        finally:
            self.repository.release(lease, now=now_iso())

    def resume(self, task_id: str, *, route: ProviderRoute, run_id: str, keyring_unlocked: bool = False) -> SystemServiceTaskResult:
        state = self.repository.get(task_id)
        contract = self.repository.contract(task_id)
        if state is None or contract is None or not is_goal04_contract(contract):
            raise KeyError(task_id)
        lease = self._claim(task_id, run_id)
        if lease is None:
            return self._result(state)
        try:
            if state.status is TaskStatus.WAITING:
                if not keyring_unlocked or not state.wait_event_key:
                    self.checkpoint("while_waiting")
                    return self._result(state)
                state = self._commit(
                    state,
                    TaskEventType.EVENT_RECEIVED,
                    lease,
                    reason="session keyring unlock was confirmed",
                    interaction_id=state.wait_event_key,
                )
            return self._drive(state, route, run_id, lease)
        finally:
            self.repository.release(lease, now=now_iso())

    def is_system_service_task(self, task_id: str) -> bool:
        contract = self.repository.contract(task_id)
        return contract is not None and is_goal04_contract(contract)

    def resume_scheduled(self, task_id: str) -> None:
        state = self.repository.get(task_id)
        contract = self.repository.contract(task_id)
        if state is None or contract is None or state.status is TaskStatus.WAITING:
            return
        route_id = next((item.removeprefix("model-route:") for item in contract.scope if item.startswith("model-route:")), None)
        route = self.route_repository.get(route_id) if route_id else None
        if route is None:
            return
        self.resume(task_id, route=route, run_id=stable_id("run_goal04_service_recovery", task_id, state.revision))

    def _drive(self, state: TaskRun, route: ProviderRoute, run_id: str, lease: TaskLease) -> SystemServiceTaskResult:
        if state.status in TERMINAL_STATUSES or state.status in {TaskStatus.PAUSED, TaskStatus.AWAITING_CLARIFICATION, TaskStatus.WAITING}:
            return self._result(state)
        if state.status is TaskStatus.PLANNING:
            state = self._plan(state, route, lease)
            if state.status is not TaskStatus.READY:
                return self._result(state)
        planning = load_planning(state, self.repository, self.planning)
        if planning is None or planning.plan is None:
            state = self._fail(state, lease, "persisted system-service plan is missing", "plan_missing")
            return self._result(state)
        if state.status is TaskStatus.READY:
            review = self.engine.reviews.review_task_plan(planning.plan)
            if review.status != "allowed":
                state = self._fail(state, lease, review.message, "effect_rejected")
                return self._result(state)
            state = self._commit(
                state,
                TaskEventType.DISPATCH_REQUESTED,
                lease,
                reason="fixed E1 system-service recovery was deterministically allowed",
                step_id=planning.plan.steps[0].id,
            )
            self.checkpoint("before_external_action")
        request = CommandRequest(FIXED_SERVICE_GOAL, transport="goal04-system-service")
        if state.status is TaskStatus.RECONCILING:
            state = self.action_executor.resume_reconciliation(state, planning.plan, request, run_id=run_id, lease=lease)
        elif state.status is TaskStatus.RUNNING:
            state = self.action_executor.execute(state, planning.plan, request, run_id=run_id, lease=lease)
        if state.status is TaskStatus.READY:
            state = self._fail(state, lease, state.pending_reason or "system-service recovery action failed", "action_failed")
            return self._result(state)
        if state.status is TaskStatus.PAUSED:
            return self._result(state)
        if state.status is TaskStatus.VERIFYING:
            self.checkpoint("before_independent_verify")
            state = self._verify(state, lease)
        return self._result(state)

    def _plan(self, state: TaskRun, route: ProviderRoute, lease: TaskLease) -> TaskRun:
        facts = self.evidence.latest_facts(state.task_id)
        if facts is None or not facts_fresh(facts):
            self.checkpoint("before_fact_collection")
            try:
                facts = self.observation.observe_service_fixture(include_journal=True)
            except ServiceProviderError as exc:
                return self._fail(state, lease, str(exc), exc.code)
            self.checkpoint("after_fact_collection_before_commit")
            evidence = self.evidence.make(state, "service_facts", {"facts": facts.model_dump(mode="json")}, "captured bounded service facts")
            state = self.repository.commit(
                transition(state, event(state, TaskEventType.FACTS_CAPTURED, reason="bounded D0 service facts captured")),
                evidence=evidence,
                lease=lease,
            )
        validation_error = validate_initial_facts(facts)
        if validation_error:
            return self._fail(state, lease, validation_error[1], validation_error[0])
        digest = facts_digest(facts)
        if not self.evidence.has_context_manifest(state.task_id, digest):
            manifest = ContextManifest(items=(ContextItem(sha256=digest, payload=facts),))
            evidence = self.evidence.make(
                state,
                "context_manifest",
                {"fact_digest": digest, "manifest": manifest.model_dump(mode="json")},
                "minimal D0 context manifest persisted before model invocation",
            )
            state = self.repository.commit(
                transition(state, event(state, TaskEventType.FACTS_CAPTURED, reason="minimal D0 context manifest recorded")),
                evidence=evidence,
                lease=lease,
            )
        self.checkpoint("after_context_manifest")
        diagnosis = self.evidence.diagnosis_for_digest(state.task_id, digest)
        if diagnosis is None:
            self.checkpoint("before_model_call")
            request_id = stable_id("modelreq_goal04_service", state.task_id, digest)
            gateway_result = self.gateway.diagnose_service(
                route=route,
                binding=TaskAttemptBinding(task_id=state.task_id, attempt_id=stable_id("modelattempt", request_id), attempt_number=1),
                facts=facts,
                budget=ModelBudget(timeout_seconds=20.0, total_budget_seconds=30.0, max_output_tokens=500, max_total_tokens=4096),
                cancellation=CancellationBinding(token_id=stable_id("cancel", request_id)),
                request_id=request_id,
            )
            self.checkpoint("after_model_call_before_commit")
            if gateway_result.status == "waiting" and gateway_result.failure is not None:
                evidence = self.evidence.make(
                    state,
                    "model_failure",
                    gateway_result.failure.model_dump(mode="json"),
                    gateway_result.failure.safe_message,
                )
                state = self.repository.commit(
                    transition(
                        state,
                        event(
                            state,
                            TaskEventType.WAIT_REQUESTED,
                            interaction_id=gateway_result.failure.wait_event_key,
                            reason=gateway_result.failure.safe_message,
                        ),
                    ),
                    evidence=evidence,
                    lease=lease,
                )
                self.checkpoint("while_waiting")
                return state
            if gateway_result.response is None:
                failure = gateway_result.failure
                return self._fail(
                    state,
                    lease,
                    failure.safe_message if failure is not None else "model gateway failed closed",
                    failure.code.value if failure is not None else "model_gateway_failed",
                )
            diagnosis = gateway_result.response.result
            evidence = self.evidence.make(
                state,
                "model_result",
                {
                    "fact_digest": digest,
                    "diagnosis": diagnosis.model_dump(mode="json"),
                    "usage": gateway_result.response.usage.model_dump(mode="json"),
                    "receipt": gateway_result.response.receipt.model_dump(mode="json"),
                },
                "strict service diagnosis and typed proposal recorded",
            )
            state = self.repository.commit(
                transition(state, event(state, TaskEventType.MODEL_RESULT_RECORDED, reason="typed model proposal persisted before execution")),
                evidence=evidence,
                lease=lease,
            )
            self.checkpoint("after_typed_proposal_commit")
        if diagnosis.proposal.action == "none":
            return self._fail(state, lease, "model proposed no bounded recovery for an unhealthy fixture", "recovery_not_proposed")
        artifacts = build_system_service_planning(state, diagnosis, FIXED_SERVICE_GOAL)
        validation = validate_plan(artifacts.plan) if artifacts.plan is not None else None
        if validation is None or not validation.ok:
            return self._fail(state, lease, "typed system-service plan failed deterministic validation", "plan_invalid")
        return self.engine.plans.accept(state, artifacts, lease=lease)

    def _verify(self, state: TaskRun, lease: TaskLease) -> TaskRun:
        try:
            facts, healthy = observe_for_verification(self.observation)
        except ServiceProviderError as exc:
            return self._fail(state, lease, str(exc), exc.code)
        evidence = self.evidence.make(
            state,
            "independent_verification",
            {"facts": facts.model_dump(mode="json"), "healthy": healthy, "controller_state_used": False},
            "independent systemd verification passed" if healthy else "independent systemd verification failed",
        )
        diagnosis = self.evidence.latest_diagnosis(state.task_id)
        action = diagnosis.proposal.action if diagnosis is not None else None
        current = f"{facts.load_state}/{facts.active_state}/{facts.sub_state}/pid={facts.process.main_pid}"
        if not healthy:
            return self.repository.commit(
                transition(
                    state,
                    event(
                        state,
                        TaskEventType.FAIL,
                        reason="one bounded recovery did not reach the defined healthy state",
                        evidence_ids=(*self.evidence.ids(state.task_id), evidence.evidence_id),
                        diagnosis=diagnosis.diagnosis if diagnosis is not None else None,
                        action=action,
                        current_state=current,
                        completion_judgment="failed independent verification",
                        unresolved_risks=("fixture remains unhealthy",),
                    ),
                ),
                evidence=evidence,
                lease=lease,
            )
        return self.repository.commit(
            transition(
                state,
                event(
                    state,
                    TaskEventType.VERIFICATION_PASSED,
                    step_id=state.current_step_id,
                    terminal_status=TaskStatus.SUCCEEDED,
                    reason="fixed fixture recovery independently verified",
                    evidence_ids=(*self.evidence.ids(state.task_id), evidence.evidence_id),
                    diagnosis=diagnosis.diagnosis if diagnosis is not None else None,
                    action=action,
                    current_state=current,
                    completion_judgment="active/running with a live main process and healthy fixture log",
                    unresolved_risks=(),
                ),
            ),
            evidence=evidence,
            lease=lease,
        )

    def _fail(self, state: TaskRun, lease: TaskLease, message: str, code: str) -> TaskRun:
        evidence = self.evidence.make(state, "failure", {"code": code, "message": message}, message)
        kind = TaskEventType.PLAN_FAILED if state.status is TaskStatus.PLANNING else TaskEventType.FAIL
        diagnosis = self.evidence.latest_diagnosis(state.task_id)
        return self.repository.commit(
            transition(
                state,
                event(
                    state,
                    kind,
                    reason=message,
                    evidence_ids=(*self.evidence.ids(state.task_id), evidence.evidence_id),
                    diagnosis=diagnosis.diagnosis if diagnosis is not None else None,
                    action=diagnosis.proposal.action if diagnosis is not None else None,
                    completion_judgment=f"failed closed: {code}",
                    unresolved_risks=(message,),
                ),
            ),
            evidence=evidence,
            lease=lease,
        )

    def _result(self, state: TaskRun) -> SystemServiceTaskResult:
        diagnosis = self.evidence.latest_diagnosis(state.task_id)
        terminal = state.terminal_outcome
        return SystemServiceTaskResult(
            state,
            terminal.diagnosis if terminal is not None else diagnosis.diagnosis if diagnosis is not None else None,
            terminal.action if terminal is not None else diagnosis.proposal.action if diagnosis is not None else None,
            terminal.current_state if terminal is not None else None,
            self.evidence.ids(state.task_id),
        )

    def _claim(self, task_id: str, run_id: str) -> TaskLease | None:
        return self.repository.claim(task_id, owner=f"goal04-system-service:{run_id}", now=now_iso(), expires_at=after_seconds(self.lease_seconds))

    def _commit(self, state: TaskRun, kind: TaskEventType, lease: TaskLease, *, reason: str, **fields: object) -> TaskRun:
        return self.repository.commit(transition(state, event(state, kind, reason=reason, **fields)), lease=lease)
