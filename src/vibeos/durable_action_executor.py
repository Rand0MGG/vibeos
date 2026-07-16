from __future__ import annotations

import json
from dataclasses import asdict
from typing import Any

from .core.adapters.task_repository import SqliteTaskRepository
from .core.domain import transition
from .core.domain.task import ActionProposal, ActionReceipt, Attempt, EvidenceBundle, Step, TaskEventType, TaskLease, TaskRun, TaskStatus
from .durable_task_support import event, now_iso, stable_id
from .execution_service import StepExecutionService
from .models import CommandRequest
from .run_context import RunContext
from .task_reconciliation import ActionReconciler, ConservativeActionReconciler
from .task_models import StepExecutionResult, TaskPlan, TaskStep

_SECRET_KEYS = {"api_key", "authorization", "cookie", "credential", "password", "secret", "token"}
_CONTENT_KEYS = {"body", "content", "raw_output", "supplemental_input", "text", "utterance"}


class DurableActionExecutor:
    """Persists dispatch intent before I/O and durable receipts after I/O."""

    def __init__(
        self,
        repository: SqliteTaskRepository,
        execution: StepExecutionService,
        reconciler: ActionReconciler | None = None,
    ) -> None:
        self.repository = repository
        self.execution = execution
        self.reconciler = reconciler or ConservativeActionReconciler()

    def execute(self, state: TaskRun, plan: TaskPlan, request: CommandRequest, *, run_id: str, lease: TaskLease) -> TaskRun:
        step = _step_by_id(plan, state.current_step_id)
        stored_step = next(
            (item for item in self.repository.steps(state.task_id, state.active_plan_revision_id) if item.step_id == state.current_step_id),
            None,
        )
        if step is None or stored_step is None:
            return self._commit(state, TaskEventType.FAIL, reason="persisted current step is missing", lease=lease)
        existing_receipt = self.repository.receipt_for(stored_step.idempotency_key)
        if existing_receipt is not None:
            kind = TaskEventType.ACTION_SUCCEEDED if existing_receipt.status == "succeeded" else TaskEventType.FAIL
            return self._commit(state, kind, step_id=step.id, reason="stable receipt reconciled without replay", lease=lease)
        existing_proposal = self.repository.proposal_for(stored_step.idempotency_key)
        if existing_proposal is not None and step.risk_level != "L0" and state.last_event != TaskEventType.RECONCILIATION_NOT_APPLIED.value:
            state = self._reconcile(state, step, stored_step, existing_proposal, lease)
            if state.status is not TaskStatus.RUNNING:
                return state
        attempt_number = len(self.repository.receipts(state.task_id)) + 1
        attempt_id = existing_proposal.attempt_id if existing_proposal is not None else stable_id("attempt", state.task_id, step.id, attempt_number)
        proposal_id = existing_proposal.proposal_id if existing_proposal is not None else stable_id("proposal", stored_step.idempotency_key)
        if existing_proposal is None:
            timestamp = now_iso()
            attempt = Attempt(attempt_id, state.task_id, step.id, attempt_number, "initial", "dispatching", timestamp)
            proposal = ActionProposal(
                proposal_id,
                state.task_id,
                step.id,
                attempt_id,
                stored_step.idempotency_key,
                step.action,
                step.capability_id,
                stored_step.payload_json,
                "dispatching",
                timestamp,
                timestamp,
            )
            state = self.repository.commit(
                transition(state, event(state, TaskEventType.ACTION_PROPOSED, step_id=step.id)),
                attempt=attempt,
                proposal=proposal,
                lease=lease,
            )
        context = RunContext.from_request(request, run_id=run_id, goal_id=state.contract_id)
        result = self.execution.execute_step(context=context, plan=plan, step=step, request=request, attempt_id=attempt_id)
        return self._record_result(state, stored_step.idempotency_key, proposal_id, result, lease)

    def resume_reconciliation(
        self,
        state: TaskRun,
        plan: TaskPlan,
        request: CommandRequest,
        *,
        run_id: str,
        lease: TaskLease,
    ) -> TaskRun:
        step = _step_by_id(plan, state.current_step_id)
        stored_step = next(
            (item for item in self.repository.steps(state.task_id, state.active_plan_revision_id) if item.step_id == state.current_step_id),
            None,
        )
        proposal = self.repository.unresolved_proposal(state.task_id)
        if step is None or stored_step is None or proposal is None:
            return self._commit(state, TaskEventType.FAIL, reason="reconciliation binding is missing", lease=lease)
        reconciled = self._reconcile(state, step, stored_step, proposal, lease)
        if reconciled.status is TaskStatus.RUNNING:
            return self.execute(reconciled, plan, request, run_id=run_id, lease=lease)
        return reconciled

    def _reconcile(self, state: TaskRun, step: TaskStep, stored_step: Step, proposal: ActionProposal, lease: TaskLease) -> TaskRun:
        if state.status is TaskStatus.RUNNING:
            state = self._commit(
                state,
                TaskEventType.RECONCILIATION_REQUIRED,
                step_id=step.id,
                reason="proposal outcome is unknown after worker interruption",
                lease=lease,
            )
        reconciliation = self.reconciler.reconcile(proposal)
        if reconciliation.outcome == "succeeded" and reconciliation.step_result is not None:
            return self._record_result(
                state,
                stored_step.idempotency_key,
                proposal.proposal_id,
                reconciliation.step_result,
                lease,
                event_type=TaskEventType.RECONCILIATION_SUCCEEDED,
            )
        if reconciliation.outcome == "not_applied":
            return self._commit(
                state,
                TaskEventType.RECONCILIATION_NOT_APPLIED,
                step_id=step.id,
                reason=reconciliation.reason,
                lease=lease,
            )
        evidence = EvidenceBundle(
            evidence_id=stable_id("evidence_reconcile", proposal.proposal_id, reconciliation.reason),
            task_id=state.task_id,
            step_id=step.id,
            receipt_id=None,
            status="unknown",
            summary=reconciliation.reason,
            payload_json=json.dumps({"proposal_id": proposal.proposal_id, "outcome": reconciliation.outcome}, separators=(",", ":")),
            observed_at=now_iso(),
        )
        return self.repository.commit(
            transition(
                state,
                event(
                    state,
                    TaskEventType.RECONCILIATION_UNKNOWN,
                    step_id=step.id,
                    reason=reconciliation.reason,
                    evidence_ids=(evidence.evidence_id,),
                ),
            ),
            evidence=evidence,
            lease=lease,
        )

    def _record_result(
        self,
        state: TaskRun,
        idempotency_key: str,
        proposal_id: str,
        result: StepExecutionResult,
        lease: TaskLease,
        *,
        event_type: TaskEventType | None = None,
    ) -> TaskRun:
        timestamp = now_iso()
        succeeded = result.status == "succeeded"
        receipt = ActionReceipt(
            receipt_id=stable_id("receipt", idempotency_key),
            task_id=state.task_id,
            step_id=result.step_id,
            proposal_id=proposal_id,
            idempotency_key=idempotency_key,
            status="succeeded" if succeeded else "failed",
            adapter=result.adapter,
            external_reference=_selected_from_result(result),
            result_json=json.dumps(_safe_payload(asdict(result)), ensure_ascii=False, separators=(",", ":")),
            occurred_at=timestamp,
        )
        evidence = EvidenceBundle(
            evidence_id=stable_id("evidence", receipt.receipt_id),
            task_id=state.task_id,
            step_id=result.step_id,
            receipt_id=receipt.receipt_id,
            status="observed" if succeeded else "failed",
            summary=f"{result.capability_id or result.step_id} returned {result.status}",
            payload_json=json.dumps({"adapter": result.adapter, "adapter_status": result.adapter_status, "error_code": result.error_code}),
            observed_at=timestamp,
        )
        kind = event_type or (TaskEventType.ACTION_SUCCEEDED if succeeded else TaskEventType.ACTION_FAILED)
        return self.repository.commit(
            transition(state, event(state, kind, step_id=result.step_id, reason=result.error or result.status)),
            receipt=receipt,
            evidence=evidence,
            lease=lease,
        )

    def _commit(self, state: TaskRun, kind: TaskEventType, *, lease: TaskLease, reason: str = "", **fields: object) -> TaskRun:
        return self.repository.commit(transition(state, event(state, kind, reason=reason, **fields)), lease=lease)


def _step_by_id(plan: TaskPlan, step_id: str | None) -> TaskStep | None:
    return next((step for step in plan.steps if step.id == step_id), None)


def _selected_from_result(result: StepExecutionResult) -> str | None:
    for source in (result.result, result.diagnostics):
        for key in ("selected_target", "uri", "resolved_url"):
            if source.get(key) is not None:
                return str(source[key])
    return None


def _safe_payload(value: Any) -> Any:
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for key, item in value.items():
            normalized = str(key).lower()
            if normalized in _SECRET_KEYS or any(secret in normalized for secret in _SECRET_KEYS):
                result[str(key)] = "[REDACTED]"
            elif normalized in _CONTENT_KEYS:
                result[str(key)] = "[OMITTED]"
            else:
                result[str(key)] = _safe_payload(item)
        return result
    if isinstance(value, (list, tuple)):
        return [_safe_payload(item) for item in value]
    if isinstance(value, str) and len(value) > 2_048:
        return value[:2_048] + "...[TRUNCATED]"
    return value
