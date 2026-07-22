from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import text

from vibeos.apps import AppRegistry
from vibeos.audit import AuditLog
from vibeos.broker import CapabilityBroker
from vibeos.core.adapters.database import CoreDatabase
from vibeos.core.adapters.task_repository import SqliteTaskRepository, TaskLeaseLost
from vibeos.core.domain.task import ActionProposal, Attempt, GoalContract, PlanRevision, Step, TaskEvent, TaskEventType, TaskLease, TaskRun, TaskStatus
from vibeos.core.domain.task_transitions import transition
from vibeos.durable_action_executor import DurableActionExecutor
from vibeos.task_reconciliation import ReconciliationResult
from vibeos.models import AppEntry, Intent
from vibeos.task_models import StepExecutionResult, TaskPlan, TaskRoute, TaskStep

CRASH_BOUNDARIES = (
    "before_proposal_commit",
    "after_proposal_before_dispatch",
    "before_external_action",
    "after_external_success_before_receipt",
    "after_receipt_before_verify",
    "after_verify_before_terminal",
    "while_review_or_clarification_waits",
    "during_cancel_takeover_or_lease_expiry",
)

DRY_RUN_CRASH_BOUNDARIES = ("before_proposal", "before_external_io", "before_receipt")


class RecordingExecution:
    def __init__(self, result: StepExecutionResult) -> None:
        self.result = result
        self.calls = 0
        self.attempt_ids: list[str] = []

    def execute_step(self, *, attempt_id: str, **_kwargs) -> StepExecutionResult:
        self.calls += 1
        self.attempt_ids.append(attempt_id)
        return self.result


class FixedReconciler:
    def __init__(self, result: ReconciliationResult) -> None:
        self.result = result
        self.calls = 0

    def reconcile(self, _proposal: ActionProposal) -> ReconciliationResult:
        self.calls += 1
        return self.result


class OpenAppIntentBroker:
    def parse(self, _utterance: str) -> Intent:
        return Intent(action="app.open", target={"name": "Firefox"}, reason="dry-run crash recovery fixture")


class RecordingApps(AppRegistry):
    def __init__(self) -> None:
        self.open_calls = 0

    def list_apps(self) -> list[AppEntry]:
        return [AppEntry(desktop_id="firefox.desktop", name="Firefox", keywords=("browser",))]

    def open_app(self, app: AppEntry) -> dict[str, str]:
        self.open_calls += 1
        return {"status": "opened", "desktop_id": app.desktop_id}


@pytest.mark.parametrize("boundary", DRY_RUN_CRASH_BOUNDARIES)
def test_dry_run_survives_real_process_crash_without_external_effects(tmp_path: Path, boundary: str) -> None:
    database_path = tmp_path / f"dry-run-{boundary}.sqlite3"
    external_marker = tmp_path / f"dry-run-{boundary}.external"
    simulation_marker = tmp_path / f"dry-run-{boundary}.simulated"
    worker = Path(__file__).with_name("support_dry_run_crash_worker.py")

    completed = subprocess.run(
        [sys.executable, str(worker), boundary, str(database_path), str(external_marker), str(simulation_marker)],
        check=False,
        timeout=30,
    )

    assert completed.returncode == 86
    assert not external_marker.exists()
    if boundary == "before_receipt":
        assert simulation_marker.read_text(encoding="utf-8") == "dry_run"
    else:
        assert not simulation_marker.exists()

    database = CoreDatabase(database_path)
    apps = RecordingApps()
    broker = CapabilityBroker(
        intent_broker=OpenAppIntentBroker(),
        apps=apps,
        audit=AuditLog(tmp_path / f"dry-run-{boundary}.recovery-audit.jsonl"),
        database=database,
    )
    task = broker.task_repository.list(limit=1)[0]
    contract = broker.task_repository.contract(task.task_id)
    assert contract is not None
    assert contract.dry_run is True
    with database.engine.begin() as connection:
        connection.execute(text("UPDATE task_leases SET expires_at = '1970-01-01T00:00:00.000Z' WHERE task_id = :task_id"), {"task_id": task.task_id})

    broker.task_engine.resume_task(task.task_id)

    recovered = broker.task_repository.get(task.task_id)
    assert recovered is not None
    assert recovered.status is TaskStatus.DRY_RUN
    assert recovered.terminal_outcome is not None
    assert recovered.terminal_outcome.status == "dry_run"
    assert apps.open_calls == 0
    assert not external_marker.exists()


@pytest.mark.parametrize("boundary", CRASH_BOUNDARIES)
def test_real_process_crash_matrix_reopens_authoritative_state(tmp_path: Path, boundary: str) -> None:
    database_path = tmp_path / f"{boundary}.sqlite3"
    marker_path = tmp_path / f"{boundary}.marker"
    worker = Path(__file__).with_name("support_durable_crash_worker.py")

    completed = subprocess.run(
        [sys.executable, str(worker), boundary, str(database_path), str(marker_path)],
        check=False,
        timeout=30,
    )

    assert completed.returncode == 86
    repository = SqliteTaskRepository(CoreDatabase(database_path), clock=lambda: "2099-01-01T00:00:02.000Z")
    state = repository.get("task-process-crash")
    assert state is not None
    if boundary == "before_proposal_commit":
        assert state.status is TaskStatus.RUNNING
        assert state.last_event == "dispatch_requested"
        assert repository.proposal_for("idem-process-crash") is None
    elif boundary in {"after_proposal_before_dispatch", "before_external_action", "after_external_success_before_receipt"}:
        assert state.status is TaskStatus.RUNNING
        assert repository.proposal_for("idem-process-crash") is not None
        assert repository.receipt_for("idem-process-crash") is None
        assert marker_path.exists() is (boundary == "after_external_success_before_receipt")
    elif boundary == "after_receipt_before_verify":
        assert state.status is TaskStatus.VERIFYING
        assert repository.receipt_for("idem-process-crash") is not None
    elif boundary == "after_verify_before_terminal":
        assert state.status is TaskStatus.READY
        assert state.last_event == "verification_passed"
        assert state.terminal_outcome is None
    elif boundary == "while_review_or_clarification_waits":
        assert state.status is TaskStatus.AWAITING_REVIEW
        assert repository.get_by_interaction("review-process-crash") == state
    else:
        raw = json.loads(marker_path.read_text(encoding="utf-8"))
        stale = TaskLease(**raw)
        with pytest.raises(TaskLeaseLost):
            repository.commit(
                transition(state, _event(state, TaskEventType.RELEASE_REQUESTED)),
                lease=stale,
            )
        replacement = repository.claim(
            state.task_id,
            owner="replacement-worker",
            now="2099-01-01T00:00:02.000Z",
            expires_at="2099-01-01T00:01:02.000Z",
        )
        assert replacement is not None


def test_unknown_e1_proposal_pauses_instead_of_replaying_after_restart(tmp_path: Path) -> None:
    repository, state, plan, stored_step = _running_task(tmp_path, effect_level="E1")
    proposal = _persist_proposal(repository, state, stored_step)
    state = repository.get(state.task_id)
    assert state is not None
    execution = RecordingExecution(_success_result(plan.steps[0]))
    lease = repository.claim(
        state.task_id,
        owner="recovery-worker",
        now="2099-01-01T00:10:00.000Z",
        expires_at="2099-01-01T00:11:00.000Z",
    )
    assert lease is not None

    recovered = DurableActionExecutor(repository, execution).execute(state, plan, _request(), run_id="recovery", lease=lease)  # type: ignore[arg-type]

    assert recovered.status is TaskStatus.PAUSED
    assert "no safe reconciliation proof" in (recovered.pending_reason or "")
    assert execution.calls == 0
    persisted = repository.proposal_for(stored_step.idempotency_key)
    assert persisted is not None
    assert persisted.proposal_id == proposal.proposal_id
    assert persisted.status == "unknown"
    assert repository.receipt_for(stored_step.idempotency_key) is None


def test_l0_recovery_reuses_proposal_attempt_and_receipt_then_finishes_without_duplicate(tmp_path: Path) -> None:
    repository, state, plan, stored_step = _running_task(tmp_path, effect_level="E0")
    proposal = _persist_proposal(repository, state, stored_step)
    state = repository.get(state.task_id)
    assert state is not None
    execution = RecordingExecution(_success_result(plan.steps[0]))
    lease = repository.claim(
        state.task_id,
        owner="recovery-worker",
        now="2099-01-01T00:10:00.000Z",
        expires_at="2099-01-01T00:11:00.000Z",
    )
    assert lease is not None

    verifying = DurableActionExecutor(repository, execution).execute(state, plan, _request(), run_id="recovery", lease=lease)  # type: ignore[arg-type]
    verified = repository.commit(transition(verifying, _event(verifying, TaskEventType.VERIFICATION_PASSED, step_id=stored_step.step_id)), lease=lease)
    evidence_ids = tuple(item.evidence_id for item in repository.evidence(state.task_id))
    terminal = repository.commit(transition(verified, _event(verified, TaskEventType.COMPLETE, evidence_ids=evidence_ids)), lease=lease)

    assert execution.calls == 1
    assert execution.attempt_ids == [proposal.attempt_id]
    assert verifying.status is TaskStatus.VERIFYING
    assert verified.completed_step_ids == (stored_step.step_id,)
    assert terminal.status is TaskStatus.SUCCEEDED
    assert len(repository.receipts(state.task_id)) == 1


def test_reconciliation_proof_records_receipt_without_replaying_e1_action(tmp_path: Path) -> None:
    repository, state, plan, stored_step = _running_task(tmp_path, effect_level="E1")
    _persist_proposal(repository, state, stored_step)
    state = repository.get(state.task_id)
    assert state is not None
    execution = RecordingExecution(_success_result(plan.steps[0]))
    reconciler = FixedReconciler(ReconciliationResult("succeeded", "external state proves success", _success_result(plan.steps[0])))
    lease = repository.claim(
        state.task_id,
        owner="recovery-worker",
        now="2099-01-01T00:10:00.000Z",
        expires_at="2099-01-01T00:11:00.000Z",
    )
    assert lease is not None

    recovered = DurableActionExecutor(repository, execution, reconciler).execute(state, plan, _request(), run_id="recovery", lease=lease)  # type: ignore[arg-type]

    assert recovered.status is TaskStatus.VERIFYING
    assert recovered.last_event == "reconciliation_succeeded"
    assert reconciler.calls == 1
    assert execution.calls == 0
    assert repository.receipt_for(stored_step.idempotency_key) is not None


def test_reconciliation_not_applied_allows_one_safe_dispatch(tmp_path: Path) -> None:
    repository, state, plan, stored_step = _running_task(tmp_path, effect_level="E1")
    proposal = _persist_proposal(repository, state, stored_step)
    state = repository.get(state.task_id)
    assert state is not None
    execution = RecordingExecution(_success_result(plan.steps[0]))
    reconciler = FixedReconciler(ReconciliationResult("not_applied", "external state proves action was not applied"))
    lease = repository.claim(
        state.task_id,
        owner="recovery-worker",
        now="2099-01-01T00:10:00.000Z",
        expires_at="2099-01-01T00:11:00.000Z",
    )
    assert lease is not None

    recovered = DurableActionExecutor(repository, execution, reconciler).execute(state, plan, _request(), run_id="recovery", lease=lease)  # type: ignore[arg-type]

    assert recovered.status is TaskStatus.VERIFYING
    assert execution.calls == 1
    assert execution.attempt_ids == [proposal.attempt_id]
    assert len(repository.receipts(state.task_id)) == 1


def test_receipt_is_redacted_and_committed_before_verify_boundary(tmp_path: Path) -> None:
    repository, state, plan, stored_step = _running_task(tmp_path, effect_level="E0")
    execution = RecordingExecution(
        StepExecutionResult(
            step_id=plan.steps[0].id,
            layer="registered_tool_execute",
            status="succeeded",
            adapter="fixture",
            capability_id=plan.steps[0].capability_id,
            result={"text": "private canary", "token": "secret canary", "selected_target": "fixture"},
        )
    )
    lease = repository.claim(
        state.task_id,
        owner="worker",
        now="2099-01-01T00:10:00.000Z",
        expires_at="2099-01-01T00:11:00.000Z",
    )
    assert lease is not None

    verifying = DurableActionExecutor(repository, execution).execute(state, plan, _request(), run_id="run", lease=lease)  # type: ignore[arg-type]
    receipt = repository.receipt_for(stored_step.idempotency_key)

    assert verifying.status is TaskStatus.VERIFYING
    assert receipt is not None
    assert "private canary" not in receipt.result_json
    assert "secret canary" not in receipt.result_json
    assert json.loads(receipt.result_json)["result"]["text"] == "[OMITTED]"
    assert repository.recoverable("2099-01-01T00:10:01.000Z") == (state.task_id,)


def _running_task(tmp_path: Path, *, effect_level: str) -> tuple[SqliteTaskRepository, TaskRun, TaskPlan, Step]:
    database = CoreDatabase(tmp_path / "tasks.sqlite3")
    database.upgrade()
    repository = SqliteTaskRepository(database)
    timestamp = "2099-01-01T00:00:00.000Z"
    contract = GoalContract("contract-crash", "task-crash", "status", (), (), (), (), 1, timestamp)
    state = TaskRun("task-crash", contract.contract_id, TaskStatus.CREATED, 0, timestamp, timestamp)
    repository.create(contract, state)
    planning = repository.commit(transition(state, _event(state, TaskEventType.PLAN_REQUESTED)))
    task_step = TaskStep("step-crash", "system.status", "system.status", effect_level=effect_level)  # type: ignore[arg-type]
    plan = TaskPlan("v2", "plan-crash", "status", selected_route_id="system_status_route", routes=(TaskRoute("system_status_route", 1.0),), steps=(task_step,))
    plan_revision = PlanRevision("planrev-crash", state.task_id, 1, plan.plan_id, "{}", timestamp, "fixture")
    stored_step = Step(
        task_step.id,
        state.task_id,
        plan_revision.plan_revision_id,
        0,
        task_step.action,
        task_step.capability_id,
        "pending",
        "idem-crash",
        "{}",
        timestamp,
        timestamp,
    )
    ready = repository.commit(
        transition(planning, _event(planning, TaskEventType.PLAN_READY, plan_revision_id=plan_revision.plan_revision_id)),
        plan=plan_revision,
        steps=(stored_step,),
    )
    running = repository.commit(transition(ready, _event(ready, TaskEventType.DISPATCH_REQUESTED, step_id=task_step.id)))
    return repository, running, plan, stored_step


def _persist_proposal(repository: SqliteTaskRepository, state: TaskRun, stored_step: Step) -> ActionProposal:
    timestamp = "2099-01-01T00:00:10.000Z"
    attempt = Attempt("attempt-crash", state.task_id, stored_step.step_id, 1, "initial", "dispatching", timestamp)
    proposal = ActionProposal(
        "proposal-crash",
        state.task_id,
        stored_step.step_id,
        attempt.attempt_id,
        stored_step.idempotency_key,
        stored_step.action,
        stored_step.capability_id,
        "{}",
        "dispatching",
        timestamp,
        timestamp,
    )
    repository.commit(
        transition(state, _event(state, TaskEventType.ACTION_PROPOSED, step_id=stored_step.step_id)),
        attempt=attempt,
        proposal=proposal,
    )
    return proposal


def _success_result(step: TaskStep) -> StepExecutionResult:
    return StepExecutionResult(step.id, "registered_tool_execute", "succeeded", adapter="fixture", capability_id=step.capability_id, result={"status": "ok"})


def _event(
    state: TaskRun,
    event_type: TaskEventType,
    *,
    plan_revision_id: str | None = None,
    step_id: str | None = None,
    evidence_ids: tuple[str, ...] = (),
) -> TaskEvent:
    return TaskEvent(
        event_id=f"event-{state.revision + 1}-{event_type.value}",
        task_id=state.task_id,
        event_type=event_type,
        occurred_at=f"2099-01-01T00:00:{state.revision + 1:02d}.000Z",
        plan_revision_id=plan_revision_id,
        step_id=step_id,
        evidence_ids=evidence_ids,
    )


def _request():
    from vibeos.models import CommandRequest

    return CommandRequest("status")
