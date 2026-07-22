from __future__ import annotations

from pathlib import Path

from vibeos.apps import AppRegistry
from vibeos.audit import AuditLog
from vibeos.broker import CapabilityBroker
from vibeos.core.adapters.database import CoreDatabase
from vibeos.core.adapters.metadata import plan_revisions
from vibeos.core.domain.task import TaskStatus
from vibeos.intent import IntentBroker
from vibeos.models import AppEntry, CommandRequest, Intent, WindowEntry

from tests.support_intent_broker import FixtureIntentBroker
from sqlalchemy import update


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

    def close(self, window):
        self.close_calls += 1
        return {"status": "closed", "window_id": window.window_id}

    def focus(self, window):
        return {"status": "focused", "window_id": window.window_id}

    def minimize(self, window):
        return {"status": "minimized", "window_id": window.window_id}

    def maximize(self, window):
        return {"status": "maximized", "window_id": window.window_id}


class ClarifyingIntentBroker:
    """Requires one durable user-input round trip before producing a plan."""

    def parse(self, utterance: str) -> Intent:
        marker = "Additional user detail:"
        if marker not in utterance:
            return Intent.unknown("the application name is required")
        detail = utterance.rsplit(marker, 1)[-1].strip()
        if not detail:
            return Intent.unknown("the application name is required")
        return Intent(action="app.open", target={"name": detail}, reason="user supplied the missing application name")


def test_review_survives_restart_and_approval_is_consumed_exactly_once(tmp_path: Path) -> None:
    database_path = tmp_path / "tasks.sqlite3"
    windows = FakeWindows()
    first = make_broker(tmp_path, database_path=database_path, windows=windows)

    pending = first.handle(CommandRequest("close firefox"))
    assert pending.status == "review_required"
    assert pending.review_id is not None
    assert windows.close_calls == 0

    restarted = make_broker(tmp_path, database_path=database_path, windows=windows)
    assert [item["review_id"] for item in restarted.pending_reviews()] == [pending.review_id]

    approved = restarted.approve_review(pending.review_id)
    repeated = restarted.approve_review(pending.review_id)

    assert approved.status == "executed"
    assert repeated.status != "executed"
    assert windows.close_calls == 1
    assert restarted.pending_reviews() == []
    task = restarted.task_repository.list()[0]
    assert task.status is TaskStatus.SUCCEEDED
    assert len(restarted.task_repository.receipts(task.task_id)) == 1


def test_denied_review_is_terminal_and_cannot_later_execute(tmp_path: Path) -> None:
    database_path = tmp_path / "tasks.sqlite3"
    windows = FakeWindows()
    first = make_broker(tmp_path, database_path=database_path, windows=windows)

    pending = first.handle(CommandRequest("close firefox"))
    assert pending.review_id is not None

    restarted = make_broker(tmp_path, database_path=database_path, windows=windows)
    denied = restarted.reject_review(pending.review_id)
    repeated = restarted.approve_review(pending.review_id)

    assert denied.status == "rejected"
    assert repeated.status != "executed"
    assert windows.close_calls == 0
    task = restarted.task_repository.list()[0]
    assert task.status is TaskStatus.CANCELLED
    assert restarted.task_repository.receipts(task.task_id) == ()


def test_pending_reviews_fail_closed_when_legacy_planning_snapshot_is_malformed(tmp_path: Path) -> None:
    database_path = tmp_path / "tasks.sqlite3"
    first = make_broker(tmp_path, database_path=database_path)
    pending = first.handle(CommandRequest("close firefox"))
    state = first.task_repository.list()[0]
    assert pending.review_id is not None
    assert state.active_plan_revision_id is not None
    with first.database.engine.begin() as connection:
        connection.execute(update(plan_revisions).where(plan_revisions.c.plan_revision_id == state.active_plan_revision_id).values(payload_json="{malformed"))

    restarted = make_broker(tmp_path, database_path=database_path)
    interactions = restarted.pending_reviews()

    assert len(interactions) == 1
    assert interactions[0]["review_id"] == pending.review_id
    assert interactions[0]["plan_id"] is None
    assert interactions[0]["intent"]["action"] == "unknown"


def test_clarification_and_supplemental_input_resume_after_restart(tmp_path: Path) -> None:
    database_path = tmp_path / "tasks.sqlite3"
    apps = FakeApps()
    first = make_broker(
        tmp_path,
        database_path=database_path,
        apps=apps,
        intent_broker=ClarifyingIntentBroker(),
    )

    pending = first.handle(CommandRequest("open an application"))
    assert pending.status == "ambiguous"
    assert pending.review_id is not None
    assert apps.open_calls == 0
    assert first.task_repository.list()[0].status is TaskStatus.AWAITING_CLARIFICATION

    restarted = make_broker(
        tmp_path,
        database_path=database_path,
        apps=apps,
        intent_broker=ClarifyingIntentBroker(),
    )
    interactions = restarted.pending_reviews()
    assert len(interactions) == 1
    assert interactions[0]["review_kind"] == "user_input"
    assert interactions[0]["review_id"] == pending.review_id

    resumed = restarted.provide_review_input(pending.review_id, "Firefox")

    assert resumed.status == "executed"
    assert apps.open_calls == 1
    assert restarted.pending_reviews() == []
    task = restarted.task_repository.list()[0]
    contract = restarted.task_repository.contract(task.task_id)
    assert task.status is TaskStatus.SUCCEEDED
    assert contract is not None
    assert contract.version >= 2
    assert "Additional user detail: Firefox" in contract.goal
    assert len(restarted.task_repository.receipts(task.task_id)) == 1


def test_rejecting_clarification_after_restart_cancels_without_dispatch(tmp_path: Path) -> None:
    database_path = tmp_path / "tasks.sqlite3"
    apps = FakeApps()
    first = make_broker(
        tmp_path,
        database_path=database_path,
        apps=apps,
        intent_broker=ClarifyingIntentBroker(),
    )
    pending = first.handle(CommandRequest("open an application"))
    assert pending.review_id is not None

    restarted = make_broker(
        tmp_path,
        database_path=database_path,
        apps=apps,
        intent_broker=ClarifyingIntentBroker(),
    )
    rejected = restarted.reject_review(pending.review_id)

    assert rejected.status == "rejected"
    assert apps.open_calls == 0
    assert restarted.task_repository.list()[0].status is TaskStatus.CANCELLED


def test_unsupported_destructive_request_is_rejected_instead_of_suspended(tmp_path: Path) -> None:
    broker = make_broker(tmp_path, database_path=tmp_path / "tasks.sqlite3")

    result = broker.handle(CommandRequest("delete downloads"))

    assert result.status == "rejected"
    assert result.overall_status == "blocked"
    assert result.review_id is None
    assert broker.task_repository.list()[0].status is TaskStatus.BLOCKED


def make_broker(
    tmp_path: Path,
    *,
    database_path: Path,
    apps: FakeApps | None = None,
    windows: FakeWindows | None = None,
    intent_broker: IntentBroker | None = None,
) -> CapabilityBroker:
    return CapabilityBroker(
        intent_broker=intent_broker or FixtureIntentBroker(),
        apps=apps,
        windows=windows or FakeWindows(),
        audit=AuditLog(tmp_path / "audit.jsonl"),
        database=CoreDatabase(database_path),
    )
