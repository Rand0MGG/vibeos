from dataclasses import asdict, replace
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from uuid import uuid4

from vibeos.apps import AppRegistry
from vibeos.audit import AuditLog
from vibeos.browser_state import record_browser_observation
from vibeos.broker import CapabilityBroker
from vibeos.candidate_selection import CandidateSelectionDecision, CandidateSet
from vibeos.intent import IntentBroker
from vibeos.loop_models import GoalLoopResult, LoopObservation, LoopPolicy, LoopState
from vibeos.models import AppEntry, CommandRequest, Intent, PermissionReview, WindowEntry
from vibeos.planner import PlanningArtifacts
from vibeos.portal import PortalAdapter
from vibeos.reviews import ReviewStore
from vibeos.task_models import DisplayFields, PlanAttempt, PlanExecutionResult, StepExecutionResult, StepReviewRecord, TaskPlan, TaskRoute, TaskStep
from vibeos.understanding import default_understanding_host_hint, validated_understanding_from_payload
from tests.support_intent_broker import FixtureIntentBroker


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
        intent_broker=FixtureIntentBroker(),
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
        intent_broker=FixtureIntentBroker(),
        apps=FakeApps(),
        audit=AuditLog(),
    )
    result = broker.handle(CommandRequest("删除下载目录"))
    assert result.status == "rejected"
    assert result.intent.action == "unknown"


def test_l2_window_close_requires_review() -> None:
    review_path = make_review_path("review-required")
    broker = CapabilityBroker(
        intent_broker=FixtureIntentBroker(),
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
        intent_broker=FixtureIntentBroker(),
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
        intent_broker=FixtureIntentBroker(),
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
        intent_broker=FixtureIntentBroker(),
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
        intent_broker=FixtureIntentBroker(),
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
        intent_broker=FixtureIntentBroker(),
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
        intent_broker=FixtureIntentBroker(),
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
        intent_broker=FixtureIntentBroker(),
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
        intent_broker=FixtureIntentBroker(),
        portal=portal,
        audit=AuditLog(),
        reviews=reviews,
    )
    gate = {"needs_review": True}

    def review_step(plan, step, pre_observation=None):
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


def test_review_required_still_originates_from_step_safety_boundary(monkeypatch) -> None:
    review_path = make_review_path("goal-loop-rereview-boundary")
    reviews = ReviewStore(review_path)
    portal = ObservedPortal()
    broker = CapabilityBroker(
        intent_broker=FixtureIntentBroker(),
        portal=portal,
        audit=AuditLog(),
        reviews=reviews,
    )
    review_calls = {"count": 0}

    def review_step(plan, step, pre_observation=None):
        review_calls["count"] += 1
        if review_calls["count"] == 1:
            return (
                PermissionReview("L2", True, True, "approval required"),
                StepReviewRecord("srev_initial", step.id, step.action, "L2", True, True, "approval required"),
            )
        return (
            PermissionReview("L2", True, True, "context changed; renewed approval required"),
            StepReviewRecord("srev_changed", step.id, step.action, "L2", True, True, "context changed; renewed approval required"),
        )

    monkeypatch.setenv("VIBEOS_ENABLE_GOAL_LOOP", "1")
    monkeypatch.setattr(broker, "review_task_step", review_step)

    first = broker.handle(CommandRequest("search web for hello"))
    stored_first = reviews.get(first.review_id or "")
    approved = broker.handle(CommandRequest("", review_id=first.review_id, approve=True))
    stored_second = reviews.get(approved.review_id or "")

    assert first.status == "review_required"
    assert stored_first is not None
    assert stored_first.snapshot_payload is not None
    assert stored_first.snapshot_payload["pending_step_safety_review_id"] == "srev_initial"
    assert approved.status == "review_required"
    assert approved.review_id != first.review_id
    assert portal.open_calls == 0
    assert stored_second is not None
    assert stored_second.snapshot_payload is not None
    assert stored_second.snapshot_payload["pending_step_safety_review_id"] == "srev_changed"


def test_sensitive_search_review_overrides_default_allow() -> None:
    broker = CapabilityBroker(
        intent_broker=FixtureIntentBroker(),
        audit=AuditLog(),
        reviews=ReviewStore(),
    )
    plan = TaskPlan(
        schema_version="v0.5",
        plan_id="plan_sensitive_search",
        utterance="search web for account balance",
        display=DisplayFields(goal="search the web"),
        selected_route_id="browser_search_web_route",
        routes=(TaskRoute(id="browser_search_web_route", score=1.0, domain_id="browser"),),
        steps=(TaskStep(id="search_sensitive", action="browser.search_web", capability_id="browser.search_web", target={"query": "account balance"}),),
    )
    pre_observation = LoopObservation(
        observation_id="obs_sensitive",
        level="L1",
        phase="pre",
        packages={"browser_context": {"sensitivity_tags": ("financial",), "contains_sensitive_content": True}},
        route_id=plan.selected_route_id,
        step_id="search_sensitive",
    )

    review, record = broker.review_task_step(plan, plan.steps[0], pre_observation)

    assert review.review_required is True
    assert review.allowed is True
    assert record.review_required is True
    assert "sensitive content" in review.reason


def test_loop_policy_can_disable_contextual_search_review_escalation() -> None:
    broker = CapabilityBroker(
        intent_broker=FixtureIntentBroker(),
        audit=AuditLog(),
        reviews=ReviewStore(),
        loop_policy=LoopPolicy(review_escalation_enabled=False),
    )
    plan = TaskPlan(
        schema_version="v0.5",
        plan_id="plan_sensitive_search_disabled",
        utterance="search web for account balance",
        display=DisplayFields(goal="search the web"),
        selected_route_id="browser_search_web_route",
        routes=(TaskRoute(id="browser_search_web_route", score=1.0, domain_id="browser"),),
        steps=(TaskStep(id="search_sensitive", action="browser.search_web", capability_id="browser.search_web", target={"query": "account balance"}),),
    )
    pre_observation = LoopObservation(
        observation_id="obs_sensitive",
        level="L1",
        phase="pre",
        packages={"browser_context": {"sensitivity_tags": ("financial",), "contains_sensitive_content": True}},
        route_id=plan.selected_route_id,
        step_id="search_sensitive",
    )

    review, record = broker.review_task_step(plan, plan.steps[0], pre_observation)

    assert review.review_required is False
    assert record.review_required is False


def test_loop_decision_maps_back_to_public_runtime_statuses() -> None:
    broker = CapabilityBroker(
        intent_broker=FixtureIntentBroker(),
        audit=AuditLog(),
        reviews=ReviewStore(),
    )
    plan = TaskPlan(
        schema_version="v0.5",
        plan_id="plan_status_mapping",
        utterance="search web for hello",
        display=DisplayFields(goal="search the web"),
        selected_route_id="browser_search_web_route",
        routes=(TaskRoute(id="browser_search_web_route", score=1.0, domain_id="browser"),),
        steps=(TaskStep(id="search_web", action="browser.search_web", capability_id="browser.search_web", target={"query": "hello"}),),
    )
    planning = PlanningArtifacts(
        understanding=None,
        analysis=None,
        goal_synthesis=None,
        plan=plan,
        candidates=(plan,),
        candidate_set=None,
        route_decision=None,
    )
    base_state = LoopState(
        loop_snapshot_id="lsnap_status_mapping",
        trace_run_id="run_status_mapping",
        goal_id="goal_status_mapping",
        primary_understanding_id=None,
        candidate_set_id=None,
        selected_route_decision_id=None,
        current_step_id=None,
    )

    completed = broker._finalize_goal_loop_result(
        request=CommandRequest("search web for hello"),
        planning=planning,
        run_id="run_status_mapping",
        goal_id="goal_status_mapping",
        loop_result=GoalLoopResult(
            decision="complete",
            state=base_state,
            message="done",
            execution_status="succeeded",
            acceptance_status="passed",
            overall_status="completed",
        ),
    )
    needs_review = broker._finalize_goal_loop_result(
        request=CommandRequest("search web for hello"),
        planning=planning,
        run_id="run_status_mapping",
        goal_id="goal_status_mapping",
        loop_result=GoalLoopResult(
            decision="needs_review",
            state=base_state,
            message="review required",
            review_id="rev_status_mapping",
            execution_status="not_started",
            acceptance_status="skipped",
            overall_status="needs_review",
        ),
    )
    needs_user_input = broker._finalize_goal_loop_result(
        request=CommandRequest("search web for hello"),
        planning=planning,
        run_id="run_status_mapping",
        goal_id="goal_status_mapping",
        loop_result=GoalLoopResult(
            decision="needs_user_input",
            state=base_state,
            message="need more detail",
            review_id="rev_user_input_mapping",
            execution_status="not_started",
            acceptance_status="skipped",
            overall_status="needs_user_input",
        ),
    )
    blocked = broker._finalize_goal_loop_result(
        request=CommandRequest("search web for hello"),
        planning=planning,
        run_id="run_status_mapping",
        goal_id="goal_status_mapping",
        loop_result=GoalLoopResult(
            decision="blocked",
            state=base_state,
            message="blocked",
            execution_status="failed",
            acceptance_status="skipped",
            overall_status="blocked",
        ),
    )

    assert completed.status == "executed"
    assert completed.overall_status == "completed"
    assert needs_review.status == "review_required"
    assert needs_review.overall_status == "needs_review"
    assert needs_user_input.status == "ambiguous"
    assert needs_user_input.overall_status == "needs_user_input"
    assert blocked.status == "failed"
    assert blocked.overall_status == "blocked"


def test_broker_provide_input_resumes_user_input_review(monkeypatch) -> None:
    record_browser_observation(active_url="about:blank", query=None, adapter="test-reset")
    review_path = make_review_path("goal-loop-user-input")
    reviews = ReviewStore(review_path)
    portal = ObservedPortal()
    broker = CapabilityBroker(
        intent_broker=FixtureIntentBroker(),
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
        current_step_id="open_app",
        completed_step_ids=("open_app",),
        attempt_records=(
            PlanAttempt(
                attempt_id="attempt_prior_app",
                run_id="run_user_input",
                attempt_index=1,
                trigger="initial_plan",
                selected_route_id="apps_open_route",
                task_plan=TaskPlan(
                    schema_version="v0.5",
                    plan_id="plan_prior_app",
                    utterance="open browser",
                    display=DisplayFields(goal="open browser"),
                    selected_route_id="apps_open_route",
                    routes=(TaskRoute(id="apps_open_route", score=1.0, domain_id="apps"),),
                    steps=(TaskStep(id="open_app", action="app.open", capability_id="app.open", target={"name": "browser"}),),
                ),
                execution_result=PlanExecutionResult(
                    plan_id="plan_prior_app",
                    status="succeeded",
                    step_results=(
                        StepExecutionResult(
                            step_id="open_app",
                            layer="adapter_execute",
                            status="succeeded",
                            capability_id="app.open",
                            result={"selected_target": "firefox.desktop"},
                        ),
                    ),
                    execution_status="succeeded",
                    acceptance_status="skipped",
                    overall_status="incomplete",
                ),
            ),
        ),
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
        provided_understanding = kwargs.get("understanding")
        assert provided_understanding is not None
        assert provided_understanding.primary_understanding_id == "und_user_input"
        assert provided_understanding.source_understanding_id == "und_user_input"
        assert provided_understanding.artifact_role == "supersession"
        candidate_set = CandidateSet(
            candidate_set_id="cset_resumed",
            understanding_id=provided_understanding.primary_understanding_id,
            generated_by="test",
            candidates=(),
        )
        route_decision = CandidateSelectionDecision(
            route_decision_id="rdec_resumed",
            candidate_set_id="cset_resumed",
            understanding_id=provided_understanding.primary_understanding_id,
            action="select",
            selected_candidate_id="cand_resumed",
            reason="resume with supplemental input",
            provider_name="test",
            model_name="test",
        )
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
        return PlanningArtifacts(
            understanding=replace(provided_understanding, analysis=analysis),
            analysis=analysis,
            goal_synthesis=None,
            plan=plan,
            candidates=(plan,),
            understanding_refinement=None,
            understanding_supersession=None,
            candidate_set=candidate_set,
            route_decision=route_decision,
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
    assert len(result.result["attempts"]) == 2
    assert result.result["attempts"][0]["selected_route_id"] == "apps_open_route"
    assert result.result["attempts"][1]["selected_route_id"] == "browser_search_web_route"
    assert result.result["attempts"][1]["understanding_id"] == "und_user_input"
    assert result.result["attempts"][1]["candidate_set_id"] == "cset_resumed"
    assert result.result["attempts"][1]["route_decision_id"] == "rdec_resumed"
    assert result.result["understanding"]["primary_understanding_id"] == "und_user_input"
    assert result.result["understanding"]["source_understanding_id"] == "und_user_input"
    assert result.result["understanding"]["artifact_role"] == "supersession"
    assert portal.open_calls == 1
    assert loaded is not None
    assert loaded.status == "consumed"


def test_user_input_resumes_loop_snapshot(monkeypatch) -> None:
    record_browser_observation(active_url="about:blank", query=None, adapter="test-reset")
    test_broker_provide_input_resumes_user_input_review(monkeypatch)


def test_reject_review_blocks_later_approval() -> None:
    review_path = make_review_path("reject-review")
    broker = CapabilityBroker(
        intent_broker=FixtureIntentBroker(),
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
        intent_broker=FixtureIntentBroker(),
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
