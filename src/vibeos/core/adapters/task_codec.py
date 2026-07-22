from __future__ import annotations

from .task_contracts import (
    ActionProposalPayloadV2,
    ActionReceiptPayloadV2,
    AttemptPayloadV2,
    EvidenceBundlePayloadV2,
    GoalContractPayloadV2,
    PlanRevisionPayloadV2,
    StepPayloadV2,
    TaskEffectPayloadV2,
    TaskEventPayloadV2,
    TaskStatePayloadV2,
)
from ..domain.task import (
    ActionProposal,
    ActionReceipt,
    Attempt,
    EvidenceBundle,
    GoalContract,
    PlanRevision,
    Step,
    TaskEvent,
    TaskRun,
    TaskStatus,
    TerminalOutcome,
    TransitionEffect,
)


def encode_task(state: TaskRun) -> str:
    terminal = state.terminal_outcome
    payload = TaskStatePayloadV2.model_validate(
        {
            "schema_version": state.schema_version,
            "task_id": state.task_id,
            "contract_id": state.contract_id,
            "status": state.status.value,
            "revision": state.revision,
            "created_at": state.created_at,
            "updated_at": state.updated_at,
            "active_plan_revision_id": state.active_plan_revision_id,
            "current_step_id": state.current_step_id,
            "completed_step_ids": state.completed_step_ids,
            "pending_interaction_id": state.pending_interaction_id,
            "pending_reason": state.pending_reason,
            "next_wake_at": state.next_wake_at,
            "wait_event_key": state.wait_event_key,
            "deadline_at": state.deadline_at,
            "suspended_status": state.suspended_status.value if state.suspended_status is not None else None,
            "cancel_requested": state.cancel_requested,
            "takeover_owner": state.takeover_owner,
            "last_event": state.last_event,
            "terminal_outcome": {
                "schema_version": terminal.schema_version,
                "task_id": terminal.task_id,
                "status": terminal.status,
                "reason": terminal.reason,
                "evidence_ids": terminal.evidence_ids,
                "finished_at": terminal.finished_at,
            }
            if terminal is not None
            else None,
        },
        strict=True,
    )
    return payload.model_dump_json()


def decode_task(raw: str) -> TaskRun:
    payload = TaskStatePayloadV2.model_validate_json(raw, strict=True)
    terminal = payload.terminal_outcome
    return TaskRun(
        task_id=payload.task_id,
        contract_id=payload.contract_id,
        status=TaskStatus(payload.status),
        revision=payload.revision,
        created_at=payload.created_at,
        updated_at=payload.updated_at,
        active_plan_revision_id=payload.active_plan_revision_id,
        current_step_id=payload.current_step_id,
        completed_step_ids=payload.completed_step_ids,
        pending_interaction_id=payload.pending_interaction_id,
        pending_reason=payload.pending_reason,
        next_wake_at=payload.next_wake_at,
        wait_event_key=payload.wait_event_key,
        deadline_at=payload.deadline_at,
        suspended_status=TaskStatus(payload.suspended_status) if payload.suspended_status is not None else None,
        cancel_requested=payload.cancel_requested,
        takeover_owner=payload.takeover_owner,
        last_event=payload.last_event,
        terminal_outcome=TerminalOutcome(
            task_id=terminal.task_id,
            status=terminal.status,
            reason=terminal.reason,
            evidence_ids=terminal.evidence_ids,
            finished_at=terminal.finished_at,
            schema_version=terminal.schema_version,
        )
        if terminal is not None
        else None,
        schema_version=payload.schema_version,
    )


def encode_contract(contract: GoalContract) -> str:
    return GoalContractPayloadV2.model_validate(contract, from_attributes=True, strict=True).model_dump_json()


def decode_contract(raw: str) -> GoalContract:
    payload = GoalContractPayloadV2.model_validate_json(raw, strict=True)
    return GoalContract(
        contract_id=payload.contract_id,
        task_id=payload.task_id,
        goal=payload.goal,
        scope=payload.scope,
        completion_conditions=payload.completion_conditions,
        allowed_effects=payload.allowed_effects,
        reality_boundaries=payload.reality_boundaries,
        version=payload.version,
        created_at=payload.created_at,
        dry_run=payload.dry_run,
        schema_version=payload.schema_version,
    )


def encode_event(event: TaskEvent, state_revision: int) -> str:
    return TaskEventPayloadV2.model_validate(
        {
            "event_id": event.event_id,
            "task_id": event.task_id,
            "event_type": event.event_type.value,
            "occurred_at": event.occurred_at,
            "reason": event.reason,
            "interaction_id": event.interaction_id,
            "plan_revision_id": event.plan_revision_id,
            "step_id": event.step_id,
            "wake_at": event.wake_at,
            "owner": event.owner,
            "terminal_status": event.terminal_status.value if event.terminal_status is not None else None,
            "evidence_ids": event.evidence_ids,
            "state_revision": state_revision,
        },
        strict=True,
    ).model_dump_json()


def encode_effect(effect: TransitionEffect) -> str:
    return TaskEffectPayloadV2.model_validate(
        {
            "effect_id": effect.effect_id,
            "task_id": effect.task_id,
            "kind": effect.kind.value,
            "step_id": effect.step_id,
            "not_before": effect.not_before,
        },
        strict=True,
    ).model_dump_json()


def validated_plan(plan: PlanRevision) -> PlanRevisionPayloadV2:
    return PlanRevisionPayloadV2.model_validate(plan, from_attributes=True, strict=True)


def validated_step(step: Step) -> StepPayloadV2:
    return StepPayloadV2.model_validate(step, from_attributes=True, strict=True)


def validated_attempt(attempt: Attempt) -> AttemptPayloadV2:
    return AttemptPayloadV2.model_validate(attempt, from_attributes=True, strict=True)


def validated_proposal(proposal: ActionProposal) -> ActionProposalPayloadV2:
    return ActionProposalPayloadV2.model_validate(proposal, from_attributes=True, strict=True)


def validated_receipt(receipt: ActionReceipt) -> ActionReceiptPayloadV2:
    return ActionReceiptPayloadV2.model_validate(receipt, from_attributes=True, strict=True)


def validated_evidence(evidence: EvidenceBundle) -> EvidenceBundlePayloadV2:
    return EvidenceBundlePayloadV2.model_validate(evidence, from_attributes=True, strict=True)
