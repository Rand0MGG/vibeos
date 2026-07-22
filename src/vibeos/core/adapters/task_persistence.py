from __future__ import annotations

from sqlalchemy import insert, select, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.engine import Connection

from .metadata import (
    action_proposals,
    current_state,
    domain_events,
    evidence_bundles,
    goal_contracts,
    outbox,
    plan_revisions,
    task_action_receipts,
    task_attempts,
    task_steps,
    terminal_outcomes,
    wait_conditions,
)
from .task_codec import (
    encode_contract,
    encode_effect,
    encode_event,
    validated_attempt,
    validated_evidence,
    validated_plan,
    validated_proposal,
    validated_receipt,
    validated_step,
)
from ..domain.task import (
    ActionProposal,
    ActionReceipt,
    Attempt,
    EvidenceBundle,
    GoalContract,
    PlanRevision,
    Step,
    TaskEventType,
    TaskRun,
    TaskTransition,
)


def write_current_state(connection: Connection, transition: TaskTransition, state_json: str) -> None:
    statement = sqlite_insert(current_state).values(
        state_key=f"task:{transition.state.task_id}",
        aggregate_type="task",
        aggregate_id=transition.state.task_id,
        state_version=transition.state.revision,
        status=transition.state.status.value,
        schema_version=transition.state.schema_version,
        payload_json=state_json,
        updated_at=transition.state.updated_at,
    )
    connection.execute(
        statement.on_conflict_do_update(
            index_elements=[current_state.c.state_key],
            set_={
                "state_version": transition.state.revision,
                "status": transition.state.status.value,
                "payload_json": state_json,
                "updated_at": transition.state.updated_at,
            },
        )
    )


def write_event(connection: Connection, transition: TaskTransition) -> None:
    connection.execute(
        insert(domain_events).values(
            event_id=transition.event.event_id,
            state_key=f"task:{transition.state.task_id}",
            aggregate_type="task",
            aggregate_id=transition.state.task_id,
            event_type=transition.event.event_type.value,
            schema_version=transition.state.schema_version,
            occurred_at=transition.event.occurred_at,
            payload_json=encode_event(transition.event, transition.state.revision),
        )
    )


def write_effects(connection: Connection, transition: TaskTransition) -> None:
    for effect in transition.effects:
        connection.execute(
            sqlite_insert(outbox)
            .values(
                message_id=effect.effect_id,
                state_key=f"task:{transition.state.task_id}",
                aggregate_id=transition.state.task_id,
                topic=f"task.effect.{effect.kind.value}",
                schema_version=transition.state.schema_version,
                occurred_at=transition.event.occurred_at,
                payload_json=encode_effect(effect),
                attempts=0,
                idempotency_key=effect.effect_id,
                available_at=effect.not_before or transition.event.occurred_at,
            )
            .on_conflict_do_nothing()
        )


def insert_contract_version(connection: Connection, contract: GoalContract, current_version: int | None) -> None:
    if current_version is None or contract.version != current_version + 1:
        raise ValueError("goal contract version must advance exactly once")
    connection.execute(
        insert(goal_contracts).values(
            contract_id=contract.contract_id,
            task_id=contract.task_id,
            version=contract.version,
            schema_version=contract.schema_version,
            payload_json=encode_contract(contract),
            created_at=contract.created_at,
        )
    )


def write_wait_state(connection: Connection, transition: TaskTransition) -> None:
    opened = {
        TaskEventType.WAIT_REQUESTED: "timer",
        TaskEventType.RETRY_SCHEDULED: "retry_timer",
        TaskEventType.REVIEW_REQUIRED: "review",
        TaskEventType.CLARIFICATION_REQUIRED: "clarification",
    }
    event_type = transition.event.event_type
    if event_type in opened:
        wait_kind = "event" if event_type is TaskEventType.WAIT_REQUESTED and transition.event.wake_at is None else opened[event_type]
        connection.execute(
            sqlite_insert(wait_conditions)
            .values(
                wait_id=f"wait:{transition.event.event_id}",
                task_id=transition.state.task_id,
                kind=wait_kind,
                due_at=transition.event.wake_at,
                event_key=transition.event.interaction_id,
                status="active",
                schema_version=transition.state.schema_version,
                created_at=transition.event.occurred_at,
            )
            .on_conflict_do_nothing()
        )
        return
    satisfied = {
        TaskEventType.TIMER_ELAPSED,
        TaskEventType.EVENT_RECEIVED,
        TaskEventType.REVIEW_APPROVED,
        TaskEventType.REVIEW_REJECTED,
        TaskEventType.CLARIFICATION_PROVIDED,
    }
    if event_type in satisfied:
        connection.execute(
            update(wait_conditions)
            .where(wait_conditions.c.task_id == transition.state.task_id, wait_conditions.c.status == "active")
            .values(status="satisfied", satisfied_at=transition.event.occurred_at)
        )


def write_step_state(connection: Connection, transition: TaskTransition) -> None:
    step_id = transition.event.step_id
    if step_id is None:
        return
    status = {
        TaskEventType.DISPATCH_REQUESTED: "running",
        TaskEventType.ACTION_FAILED: "failed",
        TaskEventType.STEP_EVIDENCE_RECORDED: "evidence_recorded",
        TaskEventType.VERIFICATION_PASSED: "succeeded",
        TaskEventType.VERIFICATION_FAILED: "failed",
    }.get(transition.event.event_type)
    if status is not None:
        connection.execute(
            update(task_steps)
            .where(task_steps.c.task_id == transition.state.task_id, task_steps.c.step_id == step_id)
            .values(status=status, updated_at=transition.event.occurred_at)
        )
    if transition.event.event_type is TaskEventType.RECONCILIATION_UNKNOWN:
        connection.execute(
            update(action_proposals)
            .where(
                action_proposals.c.task_id == transition.state.task_id,
                action_proposals.c.step_id == step_id,
                action_proposals.c.status == "dispatching",
            )
            .values(status="unknown", updated_at=transition.event.occurred_at)
        )


def write_artifacts(
    connection: Connection,
    plan: PlanRevision | None,
    steps: tuple[Step, ...],
    attempt: Attempt | None,
    proposal: ActionProposal | None,
    receipt: ActionReceipt | None,
    evidence: EvidenceBundle | None,
    evidences: tuple[EvidenceBundle, ...] = (),
) -> None:
    if plan is not None:
        connection.execute(insert(plan_revisions).values(**validated_plan(plan).model_dump()))
    for step in steps:
        payload = validated_step(step).model_dump()
        connection.execute(insert(task_steps).values(step_key=f"{step.task_id}:{step.plan_revision_id}:{step.step_id}", **payload))
    if attempt is not None:
        connection.execute(insert(task_attempts).values(**validated_attempt(attempt).model_dump()))
    if proposal is not None:
        connection.execute(insert(action_proposals).values(**validated_proposal(proposal).model_dump()))
    if receipt is not None:
        connection.execute(insert(task_action_receipts).values(**validated_receipt(receipt).model_dump()))
        connection.execute(
            update(action_proposals).where(action_proposals.c.proposal_id == receipt.proposal_id).values(status=receipt.status, updated_at=receipt.occurred_at)
        )
        attempt_id = select(action_proposals.c.attempt_id).where(action_proposals.c.proposal_id == receipt.proposal_id).scalar_subquery()
        connection.execute(update(task_attempts).where(task_attempts.c.attempt_id == attempt_id).values(status=receipt.status, finished_at=receipt.occurred_at))
    if evidence is not None:
        connection.execute(insert(evidence_bundles).values(**validated_evidence(evidence).model_dump()))
    for item in evidences:
        connection.execute(insert(evidence_bundles).values(**validated_evidence(item).model_dump()))


def write_terminal(connection: Connection, state: TaskRun) -> None:
    outcome = state.terminal_outcome
    if outcome is None:
        return
    connection.execute(
        sqlite_insert(terminal_outcomes)
        .values(
            task_id=outcome.task_id,
            status=outcome.status,
            reason=outcome.reason,
            schema_version=outcome.schema_version,
            evidence_ids_json="[" + ",".join(f'"{item}"' for item in outcome.evidence_ids) + "]",
            finished_at=outcome.finished_at,
        )
        .on_conflict_do_nothing()
    )
