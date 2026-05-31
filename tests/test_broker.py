from pathlib import Path
from uuid import uuid4

from vibeos.apps import AppRegistry
from vibeos.audit import AuditLog
from vibeos.broker import CapabilityBroker
from vibeos.intent import IntentBroker, RuleIntentBroker
from vibeos.models import AppEntry, CommandRequest, Intent, WindowEntry
from vibeos.reviews import ReviewStore


class FakeApps(AppRegistry):
    def list_apps(self):
        return [AppEntry(desktop_id="firefox.desktop", name="Firefox", keywords=("browser",))]

    def open_app(self, app):
        return {"status": "opened", "desktop_id": app.desktop_id}


class FakeWindows:
    def list_windows(self):
        return [WindowEntry(window_id="1", app_id="firefox.desktop", title="Firefox", focused=True)]

    def resolve(self, query):
        return self.list_windows() if query.lower() in {"firefox", "browser", "current", "当前窗口"} else []

    def focus(self, window):
        return {"status": "focused", "window_id": window.window_id}

    def minimize(self, window):
        return {"status": "minimized", "window_id": window.window_id}

    def maximize(self, window):
        return {"status": "maximized", "window_id": window.window_id}

    def close(self, window):
        return {"status": "closed", "window_id": window.window_id}


class StaticIntentBroker(IntentBroker):
    def __init__(self, intent):
        self.intent = intent

    def parse(self, utterance):
        return self.intent


def test_broker_dry_run_open_app() -> None:
    broker = CapabilityBroker(
        intent_broker=RuleIntentBroker(),
        apps=FakeApps(),
        audit=AuditLog(),
    )
    result = broker.handle(CommandRequest("打开浏览器", dry_run=True))
    assert result.status == "dry_run"
    assert result.intent.action == "app.open"
    assert result.selected_target == "firefox.desktop"
    assert result.audit_id


def test_broker_rejects_delete_request() -> None:
    broker = CapabilityBroker(
        intent_broker=RuleIntentBroker(),
        apps=FakeApps(),
        audit=AuditLog(),
    )
    result = broker.handle(CommandRequest("删除下载目录"))
    assert result.status == "rejected"
    assert result.intent.action == "unknown"


def test_l2_window_close_requires_review() -> None:
    review_path = make_review_path("review-required")
    broker = CapabilityBroker(
        intent_broker=RuleIntentBroker(),
        apps=FakeApps(),
        windows=FakeWindows(),
        audit=AuditLog(),
        reviews=ReviewStore(review_path),
    )
    result = broker.handle(CommandRequest("关闭Firefox"))
    assert result.status == "review_required"
    assert result.review_id
    assert result.review
    assert result.review.risk_level == "L2"


def test_l2_direct_approval_without_review_id_is_rejected() -> None:
    broker = CapabilityBroker(
        intent_broker=RuleIntentBroker(),
        apps=FakeApps(),
        windows=FakeWindows(),
        audit=AuditLog(),
    )
    result = broker.handle(CommandRequest("关闭Firefox", approve=True))
    assert result.status == "rejected"
    assert "review id" in result.message


def test_invalid_l2_target_is_rejected_without_review_id() -> None:
    broker = CapabilityBroker(
        intent_broker=StaticIntentBroker(Intent(action="portal.open_uri", target={"uri": "file:///etc/passwd"})),
        apps=FakeApps(),
        windows=FakeWindows(),
        audit=AuditLog(),
        reviews=ReviewStore(make_review_path("invalid-target")),
    )
    result = broker.handle(CommandRequest("打开 file:///etc/passwd"))
    assert result.status == "rejected"
    assert result.review_id is None
    assert result.review
    assert result.review.risk_level == "L3"


def test_audit_records_review_and_approval() -> None:
    audit = AuditLog()
    reviews = ReviewStore(make_review_path("audit-review"))
    broker = CapabilityBroker(
        intent_broker=RuleIntentBroker(),
        apps=FakeApps(),
        windows=FakeWindows(),
        audit=audit,
        reviews=reviews,
    )
    pending = broker.handle(CommandRequest("关闭Firefox"))
    result = broker.handle(CommandRequest("", review_id=pending.review_id, approve=True))
    entries = audit.tail(1)

    assert result.status == "executed"
    assert entries[-1]["approved"] is True
    assert entries[-1]["review"]["risk_level"] == "L2"


def test_approve_review_executes_stored_intent_without_reparse() -> None:
    review_path = make_review_path("approve-review")
    broker = CapabilityBroker(
        intent_broker=RuleIntentBroker(),
        apps=FakeApps(),
        windows=FakeWindows(),
        audit=AuditLog(),
        reviews=ReviewStore(review_path),
    )
    pending = broker.handle(CommandRequest("关闭Firefox"))
    approved = broker.handle(CommandRequest("", review_id=pending.review_id, approve=True))

    assert pending.status == "review_required"
    assert approved.status == "executed"
    assert approved.review_id == pending.review_id
    assert approved.intent.action == "window.close"


def test_approve_review_is_consumed_after_execution() -> None:
    review_path = make_review_path("consume-review")
    broker = CapabilityBroker(
        intent_broker=RuleIntentBroker(),
        apps=FakeApps(),
        windows=FakeWindows(),
        audit=AuditLog(),
        reviews=ReviewStore(review_path),
    )
    pending = broker.handle(CommandRequest("关闭Firefox"))
    first = broker.handle(CommandRequest("", review_id=pending.review_id, approve=True))
    second = broker.handle(CommandRequest("", review_id=pending.review_id, approve=True))

    assert first.status == "executed"
    assert second.status == "rejected"
    assert "consumed" in second.message


def test_approve_review_dry_run_does_not_consume() -> None:
    review_path = make_review_path("dry-run-review")
    reviews = ReviewStore(review_path)
    broker = CapabilityBroker(
        intent_broker=RuleIntentBroker(),
        apps=FakeApps(),
        windows=FakeWindows(),
        audit=AuditLog(),
        reviews=reviews,
    )
    pending = broker.handle(CommandRequest("关闭Firefox"))
    preview = broker.handle(CommandRequest("", review_id=pending.review_id, dry_run=True, approve=True))
    loaded = reviews.get(pending.review_id)
    approved = broker.handle(CommandRequest("", review_id=pending.review_id, approve=True))

    assert preview.status == "dry_run"
    assert loaded
    assert loaded.status == "pending"
    assert approved.status == "executed"


def test_reject_review_blocks_later_approval() -> None:
    review_path = make_review_path("reject-review")
    broker = CapabilityBroker(
        intent_broker=RuleIntentBroker(),
        apps=FakeApps(),
        windows=FakeWindows(),
        audit=AuditLog(),
        reviews=ReviewStore(review_path),
    )
    pending = broker.handle(CommandRequest("关闭Firefox"))
    rejected = broker.reject_review(pending.review_id)
    approved = broker.handle(CommandRequest("", review_id=pending.review_id, approve=True))

    assert rejected.status == "rejected"
    assert rejected.message == "review request rejected by user"
    assert approved.status == "rejected"
    assert "rejected" in approved.message


def test_expired_review_cannot_be_approved() -> None:
    review_path = make_review_path("expired-review")
    broker = CapabilityBroker(
        intent_broker=RuleIntentBroker(),
        apps=FakeApps(),
        windows=FakeWindows(),
        audit=AuditLog(),
        reviews=ReviewStore(review_path, ttl_seconds=-1),
    )
    pending = broker.handle(CommandRequest("关闭Firefox"))
    approved = broker.handle(CommandRequest("", review_id=pending.review_id, approve=True))

    assert pending.status == "review_required"
    assert approved.status == "rejected"
    assert "expired" in approved.message


def make_review_path(name: str) -> Path:
    return Path(".vibeos") / f"test-{name}-{uuid4().hex}.jsonl"
