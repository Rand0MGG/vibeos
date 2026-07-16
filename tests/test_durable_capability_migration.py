from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import pytest

from vibeos.app_fixtures import AppSearchFixture
from vibeos.apps import AppRegistry
from vibeos.broker import CapabilityBroker
from vibeos.capabilities import CAPABILITIES
from vibeos.core.adapters.database import CoreDatabase
from vibeos.core.domain.task import TaskStatus
from vibeos.models import AppEntry, CommandRequest, Intent, WindowEntry


TARGETS: dict[str, dict[str, object]] = {
    "app.list": {},
    "window.list": {},
    "system.status": {},
    "app.open": {"name": "Firefox"},
    "window.focus": {"name": "Firefox"},
    "window.minimize": {"name": "Firefox"},
    "window.maximize": {"name": "Firefox"},
    "notification.send": {"title": "VibeOS", "body": "test"},
    "window.close": {"name": "Firefox"},
    "portal.open_uri": {"uri": "https://example.com"},
    "clipboard.write": {"text": "test"},
    "browser.open_url": {"uri": "https://example.com"},
    "browser.search_web": {"query": "hello"},
    "browser.open_named_target": {"name": "example"},
    "browser.open_site_search": {"site": "example.com", "query": "hello"},
    "media.search": {"query": "song"},
    "media.play": {"query": "song", "selection": "best_match"},
    "media.pause": {},
    "app.search_history": {"app": "chat", "query": "hello"},
}


@dataclass(frozen=True)
class CapabilityContract:
    risk: str
    real_outcome: str
    invalid_error: str


CONTRACTS: dict[str, CapabilityContract] = {
    "app.list": CapabilityContract("L0", "succeeded", "unexpected target"),
    "window.list": CapabilityContract("L0", "succeeded", "unexpected target"),
    "system.status": CapabilityContract("L0", "succeeded", "unexpected target"),
    "app.open": CapabilityContract("L1", "succeeded", "missing installed app name"),
    "window.focus": CapabilityContract("L1", "succeeded", "missing visible window"),
    "window.minimize": CapabilityContract("L1", "succeeded", "missing visible window"),
    "window.maximize": CapabilityContract("L1", "succeeded", "missing visible window"),
    "notification.send": CapabilityContract("L1", "succeeded", "empty notification body"),
    "window.close": CapabilityContract("L2", "review_then_succeeded", "missing visible window"),
    "portal.open_uri": CapabilityContract("L2", "review_then_environment_incomplete", "unsupported URI scheme"),
    "clipboard.write": CapabilityContract("L2", "review_then_succeeded", "empty clipboard text"),
    "browser.open_url": CapabilityContract("L1", "environment_incomplete", "unsupported URL scheme"),
    "browser.search_web": CapabilityContract("L1", "environment_incomplete", "empty search query"),
    "browser.open_named_target": CapabilityContract("L1", "environment_incomplete", "unknown named target"),
    "browser.open_site_search": CapabilityContract("L1", "environment_incomplete", "empty site or query"),
    "media.search": CapabilityContract("L1", "clarification_unavailable", "empty media query"),
    "media.play": CapabilityContract("L1", "environment_incomplete", "empty media query"),
    "media.pause": CapabilityContract("L1", "clarification_unavailable", "dedicated adapter unavailable"),
    "app.search_history": CapabilityContract("L1", "succeeded", "missing fixture or query"),
}


class OneIntentBroker:
    def __init__(self, action: str) -> None:
        self.action = action

    def parse(self, _utterance: str) -> Intent:
        return Intent(action=self.action, target=TARGETS[self.action], reason="capability migration fixture")


class FakeApps(AppRegistry):
    def list_apps(self):
        return [AppEntry(desktop_id="firefox.desktop", name="Firefox", keywords=("browser",))]

    def open_app(self, app):
        return {"status": "opened", "desktop_id": app.desktop_id}


class FakeWindows:
    def list_windows(self):
        return [WindowEntry(window_id="1", app_id="firefox.desktop", title="Firefox", focused=True)]

    def resolve(self, _query):
        return self.list_windows()

    def focus(self, window):
        return {"status": "focused", "window_id": window.window_id}

    def minimize(self, window):
        return {"status": "minimized", "window_id": window.window_id}

    def maximize(self, window):
        return {"status": "maximized", "window_id": window.window_id}

    def close(self, window):
        return {"status": "closed", "window_id": window.window_id}


class FakePortal:
    def status(self):
        return {"available": True, "open_uri": True, "screenshot": False, "remote_desktop": False}

    def open_uri(self, uri):
        return {"status": "opened", "uri": uri, "adapter": "fixture-portal"}


class FakeNotifications:
    def send(self, title, body=""):
        return {"status": "sent", "title": title, "adapter": "fixture-notify"}


class FakeClipboard:
    def write(self, text):
        return {"status": "written", "text_length": str(len(text)), "adapter": "fixture-clipboard"}


def make_broker(tmp_path: Path, capability_id: str, suffix: str) -> CapabilityBroker:
    return CapabilityBroker(
        intent_broker=OneIntentBroker(capability_id),
        apps=FakeApps(),
        windows=FakeWindows(),  # type: ignore[arg-type]
        portal=FakePortal(),  # type: ignore[arg-type]
        notifications=FakeNotifications(),  # type: ignore[arg-type]
        clipboard=FakeClipboard(),  # type: ignore[arg-type]
        database=CoreDatabase(tmp_path / f"{capability_id.replace('.', '_')}-{suffix}.sqlite3"),
        browser_site_catalog={"example": "https://example.com"},
        app_fixture_catalog={
            "chat": AppSearchFixture(
                app_name="chat",
                fixture_id="fixture-chat",
                visible_controls=("search_box",),
                shortcut_search_enabled=True,
                search_results={"hello": ("result",)},
            )
        },
    )


@pytest.mark.parametrize("capability_id", tuple(CAPABILITIES))
def test_all_19_capabilities_enter_only_through_durable_task_engine(tmp_path: Path, capability_id: str) -> None:
    broker = make_broker(tmp_path, capability_id, "dry")
    expected = CONTRACTS[capability_id]

    result = broker.handle(CommandRequest(f"exercise {capability_id}", dry_run=True))

    tasks = broker.task_repository.list()
    assert len(tasks) == 1
    task = tasks[0]
    assert task.status in {TaskStatus.DRY_RUN, TaskStatus.AWAITING_REVIEW, TaskStatus.AWAITING_CLARIFICATION}
    assert CAPABILITIES[capability_id].risk_level == expected.risk
    assert expected.invalid_error
    if capability_id in {"media.search", "media.play", "media.pause"}:
        assert isinstance(result.result, dict)
        synthesis = result.result["goal_synthesis"]
        assert capability_id in synthesis["goal_spec"]["required_capability_ids"]
    elif task.active_plan_revision_id is not None:
        payload = broker.task_repository.plan_payload(task.active_plan_revision_id)
        assert payload is not None
        plan = json.loads(payload)["plan"]
        steps = [step for step in plan["steps"] if step["capability_id"] == capability_id]
        assert len(steps) == 1
        normalized = steps[0]["target"]
        for key, value in TARGETS[capability_id].items():
            assert normalized[key] == value
    else:
        raise AssertionError(f"{capability_id} did not persist a selected plan")
    assert broker.task_repository.contract(task.task_id) is not None
    assert isinstance(result.result, dict)
    assert result.result["task_id"] == task.task_id
    assert result.result["task"]["status"] == task.status.value
    assert result.result["run"]["goal_id"] == task.contract_id
    assert isinstance(result.result["attempts"], list)


@pytest.mark.parametrize("capability_id", tuple(CAPABILITIES))
def test_all_19_capabilities_fix_real_or_unavailable_receipts_evidence_and_projection(tmp_path: Path, capability_id: str) -> None:
    broker = make_broker(tmp_path, capability_id, "real")
    expected = CONTRACTS[capability_id]

    result = broker.handle(CommandRequest(f"exercise {capability_id}"))
    task = broker.task_repository.list()[0]
    if expected.real_outcome.startswith("review_then_"):
        assert task.status is TaskStatus.AWAITING_REVIEW
        assert result.review_id == task.pending_interaction_id
        assert result.review_id is not None
        result = broker.approve_review(result.review_id)
        task = broker.task_repository.get(task.task_id)
        assert task is not None

    if expected.real_outcome in {"succeeded", "review_then_succeeded"}:
        assert task.status is TaskStatus.SUCCEEDED
        assert result.overall_status == "completed"
    else:
        assert task.status in {TaskStatus.FAILED, TaskStatus.PAUSED, TaskStatus.READY, TaskStatus.AWAITING_CLARIFICATION}
        assert result.overall_status in {"failed", "blocked", "incomplete", "needs_user_input"}

    if task.status not in {TaskStatus.AWAITING_REVIEW, TaskStatus.AWAITING_CLARIFICATION}:
        receipts = broker.task_repository.receipts(task.task_id)
        evidence = broker.task_repository.evidence(task.task_id)
        assert receipts
        assert evidence
        assert all(receipt.task_id == task.task_id for receipt in receipts)
        assert all(item.task_id == task.task_id for item in evidence)

    assert isinstance(result.result, dict)
    assert result.result["task_id"] == task.task_id
    assert result.result["task"]["status"] == task.status.value
    assert result.result["run"]["goal_id"] == task.contract_id
    assert isinstance(result.result["attempts"], list)
