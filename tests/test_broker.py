from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse
from uuid import uuid4

from vibeos.apps import AppRegistry
from vibeos.audit import AuditLog
from vibeos.browser_state import record_browser_observation
from vibeos.broker import CapabilityBroker
from vibeos.intent import IntentBroker, RuleIntentBroker
from vibeos.loop_models import LoopState
from vibeos.models import AppEntry, CommandRequest, Intent, PermissionReview, WindowEntry
from vibeos.portal import PortalAdapter
from vibeos.reviews import ReviewStore
from vibeos.task_models import DisplayFields, StepReviewRecord, TaskPlan, TaskRoute, TaskStep
from vibeos.understanding import UnderstandingArtifact, default_understanding_host_hint, validated_understanding_from_payload


class FakeApps(AppRegistry):
    def __init__(self):
        self.open_calls = 0

    def list_apps(self):
        return [AppEntry(desktop_id="firefox.desktop", name="Firefox", keywords=("browser",))]

    def open_app(self, app):
        self.open_calls += 1
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


class ObservedPortal(PortalAdapter):
    def __init__(self):
        self.open_calls = 0

    def open_uri(self, uri: str) -> dict[str, object]:
        self.open_calls += 1
        parsed = urlparse(uri)
        params = parse_qs(parsed.query)
        observed_query = ""
        for key in ("q", "query", "wd", "p", "text", "search_query"):
            values = params.get(key)
            if values:
                observed_query = str(values[0])
                break
        record_browser_observation(active_url=uri, query=observed_query or None, adapter="test-broker-browser")
        return {"status": "opened", "uri": uri, "adapter": "test-broker-browser"}


class RetryWindows(FakeWindows):
    def __init__(self):
        self.close_calls = 0

    def close(self, window):
        self.close_calls += 1
        if self.close_calls == 1:
            return {"status": "not_found", "window_id": window.window_id}
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
    assert result.transport is None


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
    assert result.status in {"rejected", "failed"}
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


def test_audit_records_request_transport() -> None:
    audit = AuditLog(make_review_path("audit-transport"))
    broker = CapabilityBroker(
        intent_broker=RuleIntentBroker(),
        apps=FakeApps(),
        audit=audit,
    )

    result = broker.handle(CommandRequest("打开浏览器", dry_run=True, transport="local"))
    entries = audit.tail(1)

    assert result.transport == "local"
    assert entries[-1]["transport"] == "local"


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


def test_failed_approved_review_is_not_consumed_and_can_retry() -> None:
    review_path = make_review_path("retry-approved-review")
    windows = RetryWindows()
    reviews = ReviewStore(review_path)
    broker = CapabilityBroker(
        intent_broker=RuleIntentBroker(),
        apps=FakeApps(),
        windows=windows,
        audit=AuditLog(),
        reviews=reviews,
    )
    pending = broker.handle(CommandRequest("关闭Firefox"))
    first = broker.handle(CommandRequest("", review_id=pending.review_id, approve=True))
    loaded = reviews.get(pending.review_id)
    second = broker.handle(CommandRequest("", review_id=pending.review_id, approve=True))

    assert first.status == "failed"
    assert loaded
    assert loaded.status == "approved"
    assert second.status == "executed"


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


def test_existing_capability_path_can_suspend_and_resume(monkeypatch) -> None:
    review_path = make_review_path("goal-loop-existing-capability")
    reviews = ReviewStore(review_path)
    portal = ObservedPortal()
    broker = CapabilityBroker(
        intent_broker=RuleIntentBroker(),
        portal=portal,
        audit=AuditLog(),
        reviews=reviews,
    )
    gate = {"needs_review": True}

    def review_step(plan, step):
        required = gate["needs_review"]
        reason = "approval required" if required else "allowed"
        return (
            PermissionReview("L2" if required else "L1", required, True, reason),
            StepReviewRecord(f"srev_{step.id}", step.id, step.action, "L2" if required else "L1", required, True, reason),
        )

    monkeypatch.setenv("VIBEOS_ENABLE_GOAL_LOOP", "1")
    monkeypatch.setattr(broker, "review_task_step", review_step)

    pending = broker.handle(CommandRequest("search web for hello"))
    stored = reviews.get(pending.review_id or "")

    gate["needs_review"] = False
    approved = broker.handle(CommandRequest("", review_id=pending.review_id, approve=True))

    assert pending.status == "review_required"
    assert stored is not None
    assert stored.snapshot_payload is not None
    assert stored.snapshot_payload["current_step_id"]
    assert approved.status == "executed"
    assert portal.open_calls == 1
    assert approved.result["attempts"]


def test_broker_provide_input_resumes_user_input_review(monkeypatch) -> None:
    review_path = make_review_path("goal-loop-user-input")
    reviews = ReviewStore(review_path)
    portal = ObservedPortal()
    broker = CapabilityBroker(
        intent_broker=RuleIntentBroker(),
        portal=portal,
        audit=AuditLog(),
        reviews=reviews,
    )
    state = LoopState(
        loop_snapshot_id="lsnap_user_input",
        trace_run_id="run_user_input",
        goal_id="goal_user_input",
        primary_understanding_id="und_user_input",
        candidate_set_id=None,
        selected_route_decision_id=None,
        current_step_id=None,
        stage="needs_user_input",
    )
    review = reviews.create_loop_review(
        "open that site we discussed yesterday",
        plan_payload={"analysis": {"type": "clarification", "confidence": 0.5, "domains": ["browser"], "explanation": "need more detail", "chat_response": "which site?"}},
        snapshot_payload=asdict(state),
        pending_reason="which site?",
        step_id=None,
        review_kind="user_input",
    )

    def fake_plan_turn(utterance, *args, **kwargs):
        plan = TaskPlan(
            schema_version="v0.5",
            plan_id="plan_user_input_resumed",
            utterance="search web for browser",
            display=DisplayFields(goal="search browser"),
            selected_route_id="browser_search_web_route",
            routes=(TaskRoute(id="browser_search_web_route", score=1.0, domain_id="browser"),),
            steps=(TaskStep(id="search_browser", action="browser.search_web", capability_id="browser.search_web", target={"query": "browser"}),),
        )
        analysis = validated_understanding_from_payload(
            utterance=utterance,
            payload={"type": "task", "confidence": 0.9, "domains": ["browser"], "explanation": "resolved target"},
            host_hint=default_understanding_host_hint(utterance),
        )
        understanding = UnderstandingArtifact(
            understanding_id="und_resumed",
            utterance=utterance,
            analysis=analysis,
            primary_understanding_id="und_resumed",
        )
        return SimpleNamespace(
            understanding=understanding,
            analysis=analysis,
            goal_synthesis=None,
            plan=plan,
            candidates=(plan,),
            understanding_refinement=None,
            understanding_supersession=None,
            candidate_set=None,
            route_decision=None,
            domain_routing=None,
            observation_request=None,
            observation_receipt=None,
            capability_exposure=None,
            trace=None,
            debug_trace=None,
        )

    monkeypatch.setenv("VIBEOS_ENABLE_GOAL_LOOP", "1")
    monkeypatch.setattr("vibeos.broker.plan_turn", fake_plan_turn)

    result = broker.handle(CommandRequest("", review_id=review.review_id, supplemental_input="browser"))
    loaded = reviews.get(review.review_id)

    assert result.status == "executed"
    assert result.result["attempts"]
    assert portal.open_calls == 1
    assert loaded is not None
    assert loaded.status == "consumed"


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


def test_clipboard_write_accepts_content_alias() -> None:
    broker = CapabilityBroker(
        intent_broker=StaticIntentBroker(Intent(action="clipboard.write", target={"content": "hello"})),
        apps=FakeApps(),
        windows=FakeWindows(),
        audit=AuditLog(),
        reviews=ReviewStore(make_review_path("clipboard-content-alias")),
    )

    result = broker.handle(CommandRequest("clipboard hello", dry_run=True))

    assert result.status == "review_required"
    assert result.review_id
    assert result.result["plan"]["steps"][0]["target"]["text"] == "hello"
    assert result.result["plan_review"]["status"] == "review_required"


def make_review_path(name: str) -> Path:
    return Path(".vibeos") / f"test-{name}-{uuid4().hex}.jsonl"
