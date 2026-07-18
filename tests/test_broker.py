from __future__ import annotations

from pathlib import Path

from vibeos.apps import AppRegistry
from vibeos.audit import AuditLog
from vibeos.broker import CapabilityBroker
from vibeos.core.adapters.database import CoreDatabase
from vibeos.core.domain.task import TaskStatus
from vibeos.models import AppEntry, CommandRequest, WindowEntry

from tests.support_intent_broker import FixtureIntentBroker


class FakeApps(AppRegistry):
    def __init__(self) -> None:
        self.open_calls = 0

    def list_apps(self):
        return [AppEntry(desktop_id="firefox.desktop", name="Firefox", keywords=("browser",))]

    def open_app(self, app):
        self.open_calls += 1
        return {"status": "opened", "desktop_id": app.desktop_id}


class FakeWindows:
    def __init__(self) -> None:
        self.close_calls = 0

    def list_windows(self):
        return [WindowEntry(window_id="1", app_id="firefox.desktop", title="Firefox", focused=True)]

    def resolve(self, query):
        return self.list_windows() if query.lower() in {"firefox", "browser", "current"} else []

    def focus(self, window):
        return {"status": "focused", "window_id": window.window_id}

    def minimize(self, window):
        return {"status": "minimized", "window_id": window.window_id}

    def maximize(self, window):
        return {"status": "maximized", "window_id": window.window_id}

    def close(self, window):
        self.close_calls += 1
        return {"status": "closed", "window_id": window.window_id}


class FlakyApps(FakeApps):
    def open_app(self, app):
        self.open_calls += 1
        if self.open_calls == 1:
            return {"status": "timeout", "error": "fixture adapter timed out"}
        return {"status": "opened", "desktop_id": app.desktop_id}


def test_dry_run_creates_terminal_durable_task_without_side_effect(tmp_path: Path) -> None:
    apps = FakeApps()
    broker = make_broker(tmp_path, apps=apps)

    result = broker.handle(CommandRequest("open browser", dry_run=True))

    assert result.status == "dry_run"
    assert apps.open_calls == 0
    task = broker.task_repository.list()[0]
    contract = broker.task_repository.contract(task.task_id)
    assert contract is not None
    assert contract.dry_run is True
    assert task.status.value == "dry_run"
    assert task.terminal_outcome is not None
    assert task.terminal_outcome.evidence_ids
    assert task.revision > 0


def test_low_risk_execution_has_proposal_receipt_evidence_and_terminal_state(tmp_path: Path) -> None:
    apps = FakeApps()
    broker = make_broker(tmp_path, apps=apps)

    result = broker.handle(CommandRequest("open browser"))
    task = broker.task_repository.list()[0]

    assert result.status == "executed"
    assert apps.open_calls == 1
    assert task.status.value == "succeeded"
    assert len(broker.task_repository.receipts(task.task_id)) == 1
    assert task.terminal_outcome is not None
    assert task.terminal_outcome.evidence_ids
    assert any(item.status == "passed" for item in broker.task_repository.evidence(task.task_id))
    contract = broker.task_repository.contract(task.task_id)
    assert contract is not None
    assert "capability:app.open" in contract.scope
    assert "semantic_acceptance:passed" in contract.completion_conditions
    assert broker.task_repository.reconcile_candidates() == ()


def test_list_capabilities_use_durable_task_path(tmp_path: Path) -> None:
    broker = make_broker(tmp_path, apps=FakeApps(), windows=FakeWindows())

    apps = broker.list_apps()
    windows = broker.list_windows()

    assert apps[0]["desktop_id"] == "firefox.desktop"
    assert windows[0]["window_id"] == "1"
    tasks = broker.task_repository.list()
    assert len(tasks) == 2
    assert {item.status for item in tasks} == {TaskStatus.SUCCEEDED}


def test_transient_failure_schedules_retry_and_replans_before_dispatch(tmp_path: Path) -> None:
    apps = FlakyApps()
    broker = make_broker(tmp_path, apps=apps)

    broker.handle(CommandRequest("open browser"))
    waiting = broker.task_repository.list()[0]
    assert waiting.status is TaskStatus.RETRY_WAIT
    assert waiting.next_wake_at is not None

    broker.task_engine.resume_task(waiting.task_id)

    recovered = broker.task_repository.get(waiting.task_id)
    assert recovered is not None
    assert recovered.status is TaskStatus.SUCCEEDED
    assert apps.open_calls == 2
    assert len(broker.task_repository.receipts(waiting.task_id)) == 2


def test_review_survives_broker_restart_and_approval_is_bound_once(tmp_path: Path) -> None:
    database_path = tmp_path / "tasks.sqlite3"
    windows = FakeWindows()
    first = make_broker(tmp_path, windows=windows, database_path=database_path)

    pending = first.handle(CommandRequest("close firefox"))
    assert pending.status == "review_required"
    assert windows.close_calls == 0

    second = make_broker(tmp_path, windows=windows, database_path=database_path)
    approved = second.handle(CommandRequest("", review_id=pending.review_id, approve=True))

    assert approved.status == "executed"
    assert windows.close_calls == 1
    assert second.pending_reviews() == []
    assert second.task_repository.list()[0].status.value == "succeeded"


def test_task_list_show_and_control_use_cas(tmp_path: Path) -> None:
    broker = make_broker(tmp_path)
    result = broker.handle(CommandRequest("close firefox"))
    task = broker.task_repository.list()[0]

    assert broker.task(task.task_id)["task_id"] == task.task_id
    paused = broker.control_task(task.task_id, "pause", expected_revision=task.revision, reason="operator hold")
    assert paused["status"] == "paused"
    assert paused["revision"] == task.revision + 1
    assert result.review_id is not None


def test_capability_discovery_keeps_19_capability_contract(tmp_path: Path) -> None:
    broker = make_broker(tmp_path)
    payload = broker.capabilities()
    assert len(payload["capabilities"]) == 19
    assert len(payload["capability_details"]) == 19


def make_broker(
    tmp_path: Path,
    *,
    apps: FakeApps | None = None,
    windows: FakeWindows | None = None,
    database_path: Path | None = None,
) -> CapabilityBroker:
    return CapabilityBroker(
        intent_broker=FixtureIntentBroker(),
        apps=apps,
        windows=windows or FakeWindows(),
        audit=AuditLog(tmp_path / "audit.jsonl"),
        database=CoreDatabase(database_path or tmp_path / "tasks.sqlite3"),
    )
