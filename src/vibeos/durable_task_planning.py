from __future__ import annotations

import json
from dataclasses import replace

from .core.adapters.task_repository import SqliteTaskRepository
from .core.domain import transition
from .core.domain.task import EvidenceBundle, GoalContract, TaskEventType, TaskLease, TaskRun, TaskStatus
from .durable_task_support import after_seconds, event, execution_message, now_iso, plan_artifacts, public_attempts, stable_id
from .models import CommandRequest
from .planning_models import PlanningArtifacts
from .planning_service import PlanningService
from .recovery_service import RecoveryService
from .task_models import FailureClassification, PlanExecutionResult


class DurablePlanningCoordinator:
    def __init__(
        self,
        repository: SqliteTaskRepository,
        planning: PlanningService,
        recovery: RecoveryService,
        *,
        max_attempts: int,
        retry_delay_seconds: int = 5,
    ) -> None:
        self.repository = repository
        self.planning = planning
        self.recovery = recovery
        self.max_attempts = max_attempts
        self.retry_delay_seconds = retry_delay_seconds

    def accept(self, state: TaskRun, artifacts: PlanningArtifacts, *, lease: TaskLease | None = None) -> TaskRun:
        if artifacts.plan is None:
            route_action = artifacts.route_decision.action if artifacts.route_decision is not None else None
            message = (
                artifacts.route_decision.reason
                if artifacts.route_decision is not None and artifacts.route_decision.reason
                else artifacts.analysis.chat_response or artifacts.analysis.explanation or "task requires clarification"
            )
            if artifacts.analysis.type == "chat":
                return self._commit(state, TaskEventType.COMPLETE, lease=lease, reason=message)
            if route_action == "unsupported" and _must_reject_unsupported(artifacts.analysis.utterance):
                return self._commit(
                    state,
                    TaskEventType.FAIL,
                    lease=lease,
                    reason=message,
                    terminal_status=TaskStatus.BLOCKED,
                )
            return self._commit(
                state,
                TaskEventType.CLARIFICATION_REQUIRED,
                lease=lease,
                interaction_id=stable_id("clarify", state.task_id, state.revision, message),
                reason=message,
            )
        revision, steps = plan_artifacts(state, artifacts, self.planning)
        contract = self._contract_for_plan(state, artifacts)
        task_event = event(
            state,
            TaskEventType.PLAN_READY,
            plan_revision_id=revision.plan_revision_id,
            reason=revision.reason,
        )
        return self.repository.commit(
            transition(state, task_event),
            plan=revision,
            steps=steps,
            contract_version=contract,
            lease=lease,
        )

    def finish_or_recover(
        self,
        state: TaskRun,
        artifacts: PlanningArtifacts,
        execution: PlanExecutionResult,
        request: CommandRequest,
        lease: TaskLease,
    ) -> tuple[TaskRun, PlanningArtifacts]:
        plan = artifacts.plan
        assert plan is not None
        failure = self.recovery.classify(plan, execution)
        if failure.failure_class == "none":
            return self._finish_success(state, artifacts, execution, lease)
        return self._recover_failure(state, artifacts, execution, request, failure, lease)

    def _finish_success(
        self,
        state: TaskRun,
        artifacts: PlanningArtifacts,
        execution: PlanExecutionResult,
        lease: TaskLease,
    ) -> tuple[TaskRun, PlanningArtifacts]:
        plan = artifacts.plan
        assert plan is not None
        evidence = self._outcome_evidence(state, execution)
        existing_ids = tuple(item.evidence_id for item in self.repository.evidence(state.task_id))
        evidences = () if evidence.evidence_id in existing_ids else (evidence,)
        if execution.execution_status != "dry_run" and execution.acceptance_status == "passed" and state.last_event != "verification_passed":
            step_id = state.completed_step_ids[-1] if state.completed_step_ids else plan.steps[-1].id
            verification_event = event(
                state,
                TaskEventType.VERIFICATION_PASSED,
                step_id=step_id,
                reason="registered verification and semantic acceptance passed",
                evidence_ids=(evidence.evidence_id,),
            )
            state = self.repository.commit(transition(state, verification_event), evidences=evidences, lease=lease)
            evidences = ()
        previous_ids = tuple(item.evidence_id for item in self.repository.evidence(state.task_id))
        kind = TaskEventType.DRY_RUN_COMPLETED if execution.execution_status == "dry_run" else TaskEventType.COMPLETE
        task_event = event(
            state,
            kind,
            reason=execution_message(execution),
            evidence_ids=tuple(dict.fromkeys((*previous_ids, evidence.evidence_id))),
        )
        return self.repository.commit(transition(state, task_event), evidences=evidences, lease=lease), artifacts

    def _recover_failure(
        self,
        state: TaskRun,
        artifacts: PlanningArtifacts,
        execution: PlanExecutionResult,
        request: CommandRequest,
        failure: FailureClassification,
        lease: TaskLease,
    ) -> tuple[TaskRun, PlanningArtifacts]:
        plan = artifacts.plan
        assert plan is not None
        attempts = public_attempts(state.task_id, plan, execution.step_results)
        failure_evidence = self._outcome_evidence(state, execution)
        failure_evidences = (
            () if failure_evidence.evidence_id in {item.evidence_id for item in self.repository.evidence(state.task_id)} else (failure_evidence,)
        )
        if failure.retryable and len(attempts) < self.max_attempts:
            wake_at = after_seconds(self.retry_delay_seconds)
            task_event = event(
                state,
                TaskEventType.RETRY_SCHEDULED,
                reason=failure.message,
                wake_at=wake_at,
                evidence_ids=(failure_evidence.evidence_id,),
            )
            return self.repository.commit(transition(state, task_event), evidences=failure_evidences, lease=lease), artifacts
        decision = self.recovery.decide(
            request.utterance,
            plan,
            attempts,
            failure,
            artifacts.understanding.understanding_id,
            artifacts.candidate_set.candidate_set_id if artifacts.candidate_set else None,
            tuple(route.domain_id for route in plan.routes if route.domain_id),
        )
        if decision.action in {"repair", "replan_with_constraints"} and len(attempts) < self.max_attempts:
            state = self._commit(state, TaskEventType.REPLAN_REQUESTED, lease=lease, reason=decision.reason)
            replanned = self.planning.replan(
                artifacts,
                request,
                decision.do_not_repeat_route_ids,
                decision.do_not_repeat_capability_ids,
                decision.candidate_domain_ids,
            )
            return self.accept(state, replanned, lease=lease), replanned
        if decision.action == "ask_user":
            interaction_id = stable_id("clarify", state.task_id, state.revision, decision.reason)
            return self._commit(
                state,
                TaskEventType.CLARIFICATION_REQUIRED,
                lease=lease,
                interaction_id=interaction_id,
                reason=decision.reason,
            ), artifacts
        previous_ids = tuple(item.evidence_id for item in self.repository.evidence(state.task_id))
        task_event = event(
            state,
            TaskEventType.FAIL,
            reason=decision.reason or failure.message,
            evidence_ids=(*previous_ids, failure_evidence.evidence_id),
        )
        return self.repository.commit(transition(state, task_event), evidences=failure_evidences, lease=lease), artifacts

    def _contract_for_plan(self, state: TaskRun, artifacts: PlanningArtifacts) -> GoalContract | None:
        current = self.repository.contract(state.task_id)
        plan = artifacts.plan
        if current is None or plan is None:
            return None
        domains = tuple(dict.fromkeys(route.domain_id for route in plan.routes if route.domain_id))
        capabilities = tuple(dict.fromkeys(step.capability_id for step in plan.steps))
        verifier_ids = tuple(dict.fromkeys(item for route in plan.routes for item in route.default_verifier_ids))
        expected = tuple(dict.fromkeys(step.expected_state.kind for step in plan.steps if step.expected_state is not None))
        effect_levels = tuple(dict.fromkeys(step.effect_level for step in plan.steps))
        return replace(
            current,
            contract_id=stable_id("contract", state.task_id, current.version + 1, plan.plan_id),
            scope=tuple(f"domain:{item}" for item in domains) + tuple(f"capability:{item}" for item in capabilities),
            completion_conditions=tuple(f"expected_state:{item}" for item in expected)
            + tuple(f"verifier:{item}" for item in verifier_ids)
            + ("semantic_acceptance:passed",),
            allowed_effects=tuple(f"capability:{item}" for item in capabilities),
            reality_boundaries=tuple(f"effect_level:{item}" for item in effect_levels) + ("unknown external outcomes require reconciliation proof",),
            version=current.version + 1,
            created_at=now_iso(),
        )

    @staticmethod
    def _outcome_evidence(state: TaskRun, execution: PlanExecutionResult) -> EvidenceBundle:
        timestamp = now_iso()
        payload = {
            "execution_status": execution.execution_status,
            "acceptance_status": execution.acceptance_status,
            "verification_status": execution.verification_status,
            "verification_results": [
                {
                    "verifier_id": item.get("verifier_id"),
                    "status": item.get("status"),
                    "message": str(item.get("message") or "")[:500],
                }
                for item in execution.verification_results
            ],
            "acceptance_result": {
                "status": (execution.acceptance_result or {}).get("status"),
                "message": str((execution.acceptance_result or {}).get("message") or "")[:500],
                "reasons": tuple(str(item)[:500] for item in (execution.acceptance_result or {}).get("reasons", ())),
            },
            "error": str(execution.error or "")[:500] or None,
        }
        return EvidenceBundle(
            evidence_id=stable_id(
                "evidence_acceptance",
                state.task_id,
                state.active_plan_revision_id,
                json.dumps(payload, sort_keys=True, default=str),
            ),
            task_id=state.task_id,
            step_id=None,
            receipt_id=None,
            status="simulated" if execution.execution_status == "dry_run" else execution.acceptance_status,
            summary=(
                f"execution={execution.execution_status}; acceptance={execution.acceptance_status}; "
                f"verification={execution.verification_status or 'not_required'}"
            ),
            payload_json=json.dumps(payload, ensure_ascii=False, separators=(",", ":"), default=str),
            observed_at=timestamp,
        )

    def _commit(
        self,
        state: TaskRun,
        kind: TaskEventType,
        *,
        lease: TaskLease | None,
        reason: str,
        **fields: object,
    ) -> TaskRun:
        return self.repository.commit(transition(state, event(state, kind, reason=reason, **fields)), lease=lease)


def _must_reject_unsupported(utterance: str) -> bool:
    lowered = utterance.strip().lower()
    destructive_terms = (
        "delete",
        "remove",
        "erase",
        "format",
        "uninstall",
        "删除",
        "移除",
        "清空",
        "格式化",
        "卸载",
    )
    return any(term in lowered for term in destructive_terms)
