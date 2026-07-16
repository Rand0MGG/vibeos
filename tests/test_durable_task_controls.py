from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import select

from vibeos.audit import AuditLog
from vibeos.broker import CapabilityBroker
from vibeos.core.adapters.database import CoreDatabase
from vibeos.core.adapters.metadata import domain_events
from vibeos.core.adapters.task_repository import TaskConcurrencyError
from vibeos.models import CommandRequest, WindowEntry

from tests.support_intent_broker import FixtureIntentBroker


class FakeWindows:
    def list_windows(self):
        return [WindowEntry(window_id="1", app_id="firefox.desktop", title="Firefox", focused=True)]

    def resolve(self, query):
        return self.list_windows()

    def close(self, window):
        return {"status": "closed", "window_id": window.window_id}


def test_pause_resume_takeover_release_and_cancel_are_cas_audited(tmp_path: Path) -> None:
    database = CoreDatabase(tmp_path / "tasks.sqlite3")
    broker = CapabilityBroker(
        intent_broker=FixtureIntentBroker(),
        windows=FakeWindows(),
        audit=AuditLog(tmp_path / "audit.jsonl"),
        database=database,
    )
    pending = broker.handle(CommandRequest("close firefox"))
    task = broker.task_repository.list()[0]

    paused = broker.task_engine.control(task.task_id, "pause", expected_revision=task.revision, reason="operator hold")
    with pytest.raises(TaskConcurrencyError):
        broker.task_engine.control(task.task_id, "resume", expected_revision=task.revision)
    resumed = broker.task_engine.control(task.task_id, "resume", expected_revision=paused.revision)
    taken = broker.task_engine.control(task.task_id, "takeover", expected_revision=resumed.revision, owner="alice")
    released = broker.task_engine.control(task.task_id, "release", expected_revision=taken.revision)
    cancelled = broker.task_engine.control(task.task_id, "cancel", expected_revision=released.revision)

    assert pending.review_id is not None
    assert paused.status.value == "paused"
    assert resumed.status.value == "awaiting_review"
    assert taken.takeover_owner == "alice"
    assert released.status.value == "awaiting_review"
    assert cancelled.status.value == "cancelled"
    with database.engine.connect() as connection:
        event_types = tuple(connection.execute(select(domain_events.c.event_type).where(domain_events.c.aggregate_id == task.task_id)).scalars())
    for expected in ("pause_requested", "resume_requested", "takeover_requested", "release_requested", "cancel_requested", "cancellation_confirmed"):
        assert expected in event_types
