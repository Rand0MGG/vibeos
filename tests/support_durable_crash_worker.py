from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from vibeos.core.adapters.database import CoreDatabase
from vibeos.core.adapters.task_repository import SqliteTaskRepository
from vibeos.core.domain.task import (
    ActionProposal,
    ActionReceipt,
    Attempt,
    EvidenceBundle,
    GoalContract,
    PlanRevision,
    Step,
    TaskEvent,
    TaskEventType,
    TaskRun,
    TaskStatus,
)
from vibeos.core.domain.task_transitions import transition


def main() -> None:
    boundary = sys.argv[1]
    database_path = Path(sys.argv[2])
    marker_path = Path(sys.argv[3])
    database = CoreDatabase(database_path)
    database.upgrade()
    repository = SqliteTaskRepository(database)
    ready, running, step = _base(repository)

    if boundary == "before_proposal_commit":
        crashing = SqliteTaskRepository(database, fault_injector=lambda stage: os._exit(86) if stage == "after_task_state" else None)
        attempt, proposal = _proposal(running, step)
        crashing.commit(transition(running, _event(running, TaskEventType.ACTION_PROPOSED, step_id=step.step_id)), attempt=attempt, proposal=proposal)
    elif boundary in {"after_proposal_before_dispatch", "before_external_action", "after_external_success_before_receipt"}:
        _commit_proposal(repository, running, step)
        if boundary == "after_external_success_before_receipt":
            marker_path.write_text("external-success", encoding="utf-8")
    elif boundary in {"after_receipt_before_verify", "after_verify_before_terminal"}:
        proposed = _commit_proposal(repository, running, step)
        verifying = _commit_receipt(repository, proposed, step)
        if boundary == "after_verify_before_terminal":
            repository.commit(transition(verifying, _event(verifying, TaskEventType.VERIFICATION_PASSED, step_id=step.step_id)))
    elif boundary == "while_review_or_clarification_waits":
        repository.commit(
            transition(
                running,
                _event(
                    running,
                    TaskEventType.REVIEW_REQUIRED,
                    step_id=step.step_id,
                    interaction_id="review-process-crash",
                ),
            )
        )
    elif boundary == "during_cancel_takeover_or_lease_expiry":
        lease = repository.claim(
            running.task_id,
            owner="crashed-worker",
            now="2099-01-01T00:00:00.000Z",
            expires_at="2099-01-01T00:00:01.000Z",
        )
        assert lease is not None
        repository.commit(
            transition(running, _event(running, TaskEventType.TAKEOVER_REQUESTED, owner="operator")),
            lease=lease,
        )
        marker_path.write_text(json.dumps(lease.__dict__), encoding="utf-8")
    else:
        raise ValueError(boundary)
    os._exit(86)


def _base(repository: SqliteTaskRepository) -> tuple[TaskRun, TaskRun, Step]:
    timestamp = "2099-01-01T00:00:00.000Z"
    contract = GoalContract("contract-process-crash", "task-process-crash", "status", (), (), (), (), 1, timestamp)
    created = TaskRun(contract.task_id, contract.contract_id, TaskStatus.CREATED, 0, timestamp, timestamp)
    repository.create(contract, created)
    planning = repository.commit(transition(created, _event(created, TaskEventType.PLAN_REQUESTED)))
    revision = PlanRevision("planrev-process-crash", created.task_id, 1, "plan-process-crash", "{}", timestamp, "fixture")
    step = Step(
        "step-process-crash",
        created.task_id,
        revision.plan_revision_id,
        0,
        "system.status",
        "system.status",
        "pending",
        "idem-process-crash",
        "{}",
        timestamp,
        timestamp,
    )
    ready = repository.commit(
        transition(planning, _event(planning, TaskEventType.PLAN_READY, plan_revision_id=revision.plan_revision_id)),
        plan=revision,
        steps=(step,),
    )
    running = repository.commit(transition(ready, _event(ready, TaskEventType.DISPATCH_REQUESTED, step_id=step.step_id)))
    return ready, running, step


def _proposal(state: TaskRun, step: Step) -> tuple[Attempt, ActionProposal]:
    timestamp = "2099-01-01T00:00:10.000Z"
    attempt = Attempt("attempt-process-crash", state.task_id, step.step_id, 1, "initial", "dispatching", timestamp)
    proposal = ActionProposal(
        "proposal-process-crash",
        state.task_id,
        step.step_id,
        attempt.attempt_id,
        step.idempotency_key,
        step.action,
        step.capability_id,
        "{}",
        "dispatching",
        timestamp,
        timestamp,
    )
    return attempt, proposal


def _commit_proposal(repository: SqliteTaskRepository, state: TaskRun, step: Step) -> TaskRun:
    attempt, proposal = _proposal(state, step)
    return repository.commit(
        transition(state, _event(state, TaskEventType.ACTION_PROPOSED, step_id=step.step_id)),
        attempt=attempt,
        proposal=proposal,
    )


def _commit_receipt(repository: SqliteTaskRepository, state: TaskRun, step: Step) -> TaskRun:
    timestamp = "2099-01-01T00:00:20.000Z"
    receipt = ActionReceipt(
        "receipt-process-crash",
        state.task_id,
        step.step_id,
        "proposal-process-crash",
        step.idempotency_key,
        "succeeded",
        "fixture",
        "external:one",
        json.dumps({"step_id": step.step_id, "layer": "fixture", "status": "succeeded", "result": {"status": "ok"}}),
        timestamp,
    )
    evidence = EvidenceBundle(
        "evidence-process-crash",
        state.task_id,
        step.step_id,
        receipt.receipt_id,
        "observed",
        "fixture receipt",
        "{}",
        timestamp,
    )
    return repository.commit(
        transition(state, _event(state, TaskEventType.ACTION_SUCCEEDED, step_id=step.step_id)),
        receipt=receipt,
        evidence=evidence,
    )


def _event(
    state: TaskRun,
    event_type: TaskEventType,
    *,
    plan_revision_id: str | None = None,
    step_id: str | None = None,
    interaction_id: str | None = None,
    owner: str | None = None,
) -> TaskEvent:
    return TaskEvent(
        event_id=f"process-{state.revision + 1}-{event_type.value}",
        task_id=state.task_id,
        event_type=event_type,
        occurred_at=f"2099-01-01T00:00:{state.revision + 1:02d}.000Z",
        plan_revision_id=plan_revision_id,
        step_id=step_id,
        interaction_id=interaction_id,
        owner=owner,
    )


if __name__ == "__main__":
    main()
