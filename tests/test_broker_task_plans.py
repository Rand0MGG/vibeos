from pathlib import Path
from uuid import uuid4

from vibeos.apps import AppRegistry
from vibeos.audit import AuditLog
from vibeos.broker import CapabilityBroker
from vibeos.execution_graph import execute_plan_graph
from vibeos.intent import IntentBroker
from vibeos.models import AppEntry, CommandRequest, Intent
from vibeos.intent import RuleIntentBroker
from vibeos.planner import PlanningArtifacts, browser_media_plan
from vibeos.portal import PortalAdapter
from vibeos.reviews import ReviewStore
from vibeos.task_models import DisplayFields, ExpectedState, StepExecutionResult, StepPrecondition, StepProvenance, TaskPlan, TaskRoute, TaskSpan, TaskStep, UtteranceAnalysis
from vibeos.verifiers import VerifierHarness


class FakeApps(AppRegistry):
    def list_apps(self):
        return [AppEntry(desktop_id="firefox.desktop", name="Firefox", keywords=("browser",))]

    def open_app(self, app):
        return {"status": "opened", "desktop_id": app.desktop_id}


class FakePortal(PortalAdapter):
    def open_uri(self, uri: str) -> dict[str, object]:
        return {"status": "opened", "uri": uri}


class FakeNotifications:
    def send(self, title: str, body: str = "") -> dict[str, object]:
        return {"status": "sent", "title": title, "adapter": "/usr/bin/notify-send"}


class TimeoutClipboard:
    def write(self, text: str) -> dict[str, object]:
        return {"status": "timeout", "error": "clipboard helper timed out", "adapter": "/usr/bin/wl-copy"}


class StaticIntentBroker(IntentBroker):
    def __init__(self, intent):
        self.intent = intent

    def parse(self, utterance):
        return self.intent


def test_review_task_plan_allows_browser_media_route_without_l2_review() -> None:
    broker = CapabilityBroker(
        intent_broker=StaticIntentBroker(Intent.unknown("should not reparse")),
        apps=FakeApps(),
        portal=FakePortal(),
        audit=AuditLog(make_audit_path("plan-review")),
        reviews=ReviewStore(make_review_path("plan-review")),
    )
    plan = browser_media_plan("play baby", media_span(), "baby")

    review = broker.review_task_plan(plan)

    assert review.status == "allowed"
    assert review.review_id is None
    assert review.max_risk_level == "L1"
    assert [item.action for item in review.step_reviews] == ["browser.open_site_search"]


def test_browser_media_route_executes_with_verification_metadata() -> None:
    audit = AuditLog(make_audit_path("approve-plan-review"))
    reviews = ReviewStore(make_review_path("approve-plan-review"))
    broker = CapabilityBroker(
        intent_broker=StaticIntentBroker(Intent.unknown("approval must execute stored task plan")),
        apps=FakeApps(),
        portal=FakePortal(),
        audit=audit,
        reviews=reviews,
        verifier_harness=VerifierHarness({"browser_search_route_completed": {"query": "baby"}}),
    )
    plan = browser_media_plan("play baby", media_span(), "baby")
    result = broker.execute_task_plan(plan)
    entries = audit.tail(10)
    step_entries = [item for item in entries if item.get("step_id")]

    assert result.status == "succeeded"
    assert result.plan_id == plan.plan_id
    assert [item.status for item in result.step_results] == ["succeeded"]
    assert result.step_results[0].adapter == "browser.semantic"
    assert result.step_results[0].capability_id == "browser.open_site_search"
    assert result.step_results[0].adapter_status == "succeeded"
    assert result.step_results[0].duration_ms is not None
    assert result.step_results[0].diagnostics["site"] == "youtube.com"
    assert result.verification_status == "passed"
    assert result.verification_results[0]["verifier_id"] == "browser_search_route_completed"
    assert len(step_entries) == 1
    assert {item["step_id"] for item in step_entries} == {"open_media_search_uri"}
    assert all(item["plan_id"] == plan.plan_id for item in step_entries)
    assert all(item["layer"] == "adapter_execute" for item in step_entries)


def test_browser_media_route_reports_verification_failure_when_harness_disagrees() -> None:
    broker = CapabilityBroker(
        intent_broker=StaticIntentBroker(Intent.unknown("should not reparse")),
        portal=FakePortal(),
        audit=AuditLog(make_audit_path("browser-verifier-fail")),
        reviews=ReviewStore(make_review_path("browser-verifier-fail")),
        verifier_harness=VerifierHarness({"browser_search_route_completed": {"query": "other"}}),
    )

    execution = broker.execute_task_plan(browser_media_plan("play baby", media_span(), "baby"))

    assert execution.status == "succeeded"
    assert execution.verification_status == "failed"
    assert execution.verification_results[0]["status"] == "failed"


def test_browser_media_route_uses_browser_context_for_verification_without_harness() -> None:
    broker = CapabilityBroker(
        intent_broker=StaticIntentBroker(Intent.unknown("should not reparse")),
        portal=FakePortal(),
        audit=AuditLog(make_audit_path("browser-context-verifier-pass")),
        reviews=ReviewStore(make_review_path("browser-context-verifier-pass")),
    )

    execution = broker.execute_task_plan(browser_media_plan("play baby", media_span(), "baby"))

    assert execution.status == "succeeded"
    assert execution.verification_status == "passed"
    assert execution.verification_results[0]["status"] == "passed"
    assert execution.acceptance_status == "passed"


def test_execute_plan_graph_blocks_downstream_step_after_failure() -> None:
    plan = TaskPlan(
        schema_version="v0.3",
        plan_id="plan_blocked_graph",
        utterance="two step test",
        display=DisplayFields(goal="test blocked execution"),
        selected_route_id="test_route",
        routes=(TaskRoute(id="test_route", score=1.0, required_capabilities=("app.open", "portal.open_uri")),),
        steps=(
            TaskStep(
                id="open_browser",
                action="app.open",
                capability_id="app.open",
                target={"name": "browser"},
                expected_state=ExpectedState(kind="app_opened_or_focused", fields={"app": "browser"}),
                preconditions=(StepPrecondition(kind="capability_available", capability_id="app.open"),),
                provenance=StepProvenance(source_span_id="span_1", planner="test"),
            ),
            TaskStep(
                id="open_media_search_uri",
                action="portal.open_uri",
                capability_id="portal.open_uri",
                target={"uri": "https://example.com"},
                depends_on=("open_browser",),
                risk_level="L2",
                expected_state=ExpectedState(kind="uri_open_requested", fields={"uri": "https://example.com"}),
                preconditions=(StepPrecondition(kind="capability_available", capability_id="portal.open_uri"),),
                provenance=StepProvenance(source_span_id="span_1", planner="test"),
            ),
        ),
        provenance={"planner": "test"},
    )

    def execute_step(step: TaskStep) -> StepExecutionResult:
        if step.id == "open_browser":
            return StepExecutionResult(step_id=step.id, layer="adapter_execute", status="failed", error="browser failed")
        return StepExecutionResult(step_id=step.id, layer="adapter_execute", status="succeeded")

    execution = execute_plan_graph(plan, execute_step)

    assert execution.status == "failed"
    assert execution.step_results[0].status == "failed"
    assert execution.step_results[1].status == "blocked"
    assert execution.step_results[1].result["blocked_by"] == "open_browser"


def test_normal_ask_executes_allowed_task_plan() -> None:
    broker = CapabilityBroker(
        intent_broker=RuleIntentBroker(),
        apps=FakeApps(),
        portal=FakePortal(),
        audit=AuditLog(make_audit_path("normal-ask-plan-exec")),
        reviews=ReviewStore(make_review_path("normal-ask-plan-exec")),
    )

    result = broker.handle(CommandRequest("open browser"))

    assert result.status == "executed"
    assert result.selected_target == "firefox.desktop"
    assert result.result["analysis"]["type"] == "task"
    assert result.result["plan"]["schema_version"] == "v0.5"
    assert result.result["execution"]["status"] == "succeeded"
    assert result.result["execution"]["acceptance_status"] == "passed"
    assert result.overall_status == "completed"
    assert result.result["execution"]["step_results"][0]["status"] == "succeeded"
    assert result.result["execution"]["step_results"][0]["adapter"] == "apps.registry"
    assert result.result["execution"]["step_results"][0]["capability_id"] == "app.open"


def test_normal_ask_uses_unique_run_ids_for_same_utterance() -> None:
    broker = CapabilityBroker(
        intent_broker=RuleIntentBroker(),
        apps=FakeApps(),
        portal=FakePortal(),
        audit=AuditLog(make_audit_path("unique-run-id")),
        reviews=ReviewStore(make_review_path("unique-run-id")),
    )

    first = broker.handle(CommandRequest("open browser"))
    second = broker.handle(CommandRequest("open browser"))

    assert first.result["run"]["run_id"] != second.result["run"]["run_id"]


def test_task_planning_uses_configured_intent_broker_for_normal_requests() -> None:
    broker = CapabilityBroker(
        intent_broker=StaticIntentBroker(Intent(action="window.list", reason="semantic route selection")),
        audit=AuditLog(make_audit_path("semantic-broker-plan")),
        reviews=ReviewStore(make_review_path("semantic-broker-plan")),
    )

    result = broker.handle(CommandRequest("show the active surfaces", dry_run=True))

    assert result.status == "dry_run"
    assert tuple(result.result["analysis"]["domains"]) == ("window_management",)
    assert result.result["plan"]["selected_route_id"] == "window_list_route"
    assert result.result["plan"]["steps"][0]["action"] == "window.list"


def test_normal_ask_routes_named_web_targets_to_browser_semantics() -> None:
    broker = CapabilityBroker(
        intent_broker=RuleIntentBroker(),
        portal=FakePortal(),
        audit=AuditLog(make_audit_path("named-web-browser-plan")),
        reviews=ReviewStore(make_review_path("named-web-browser-plan")),
        verifier_harness=VerifierHarness({"browser_search_route_completed": {"query": "\u767e\u5ea6\u5b98\u7f51"}}),
    )

    result = broker.handle(CommandRequest("\u6253\u5f00\u767e\u5ea6\u5b98\u7f51"))

    assert result.status == "executed"
    assert result.result["plan"]["selected_route_id"] == "browser_search_web_route"
    assert result.result["execution"]["step_results"][0]["capability_id"] == "browser.search_web"
    assert result.result["execution"]["step_results"][0]["diagnostics"]["query"] == "\u767e\u5ea6\u5b98\u7f51"


def test_task_plan_loop_replans_semantic_mismatch_into_browser_route(monkeypatch) -> None:
    broker = CapabilityBroker(
        intent_broker=RuleIntentBroker(),
        apps=FakeApps(),
        portal=FakePortal(),
        audit=AuditLog(make_audit_path("semantic-replan-browser")),
        reviews=ReviewStore(make_review_path("semantic-replan-browser")),
        verifier_harness=VerifierHarness({"browser_search_route_completed": {"query": "\u767e\u5ea6\u5b98\u7f51"}}),
    )

    apps_plan = TaskPlan(
        schema_version="v0.5",
        plan_id="plan_apps_baidu",
        utterance="\u6253\u5f00\u767e\u5ea6\u5b98\u7f51",
        display=DisplayFields(goal="open an application"),
        selected_route_id="apps_open_route",
        routes=(TaskRoute(id="apps_open_route", score=1.0, domain_id="apps", required_capabilities=("app.open",)),),
        steps=(
            TaskStep(
                id="open_app",
                action="app.open",
                capability_id="app.open",
                target={"name": "\u767e\u5ea6\u5b98\u7f51"},
                expected_state=ExpectedState(kind="app_opened_or_focused", fields={"app": "\u767e\u5ea6\u5b98\u7f51"}),
                preconditions=(StepPrecondition(kind="capability_available", capability_id="app.open"),),
                provenance=StepProvenance(source_span_id="span_1", planner="test"),
            ),
        ),
        provenance={"planner": "test"},
    )
    browser_plan = TaskPlan(
        schema_version="v0.5",
        plan_id="plan_browser_baidu",
        utterance="\u6253\u5f00\u767e\u5ea6\u5b98\u7f51",
        display=DisplayFields(goal="search in the browser"),
        selected_route_id="browser_search_web_route",
        routes=(TaskRoute(id="browser_search_web_route", score=1.0, domain_id="browser", required_capabilities=("browser.search_web",), default_verifier_ids=("browser_search_route_completed",)),),
        steps=(
            TaskStep(
                id="search_baidu",
                action="browser.search_web",
                capability_id="browser.search_web",
                target={"query": "\u767e\u5ea6\u5b98\u7f51"},
                expected_state=ExpectedState(kind="search_results_available", fields={"query": "\u767e\u5ea6\u5b98\u7f51"}),
                preconditions=(StepPrecondition(kind="capability_available", capability_id="browser.search_web"),),
                provenance=StepProvenance(source_span_id="span_1", planner="test"),
            ),
        ),
        provenance={"planner": "test"},
    )

    plans = [
        PlanningArtifacts(
            analysis=UtteranceAnalysis(
                utterance="\u6253\u5f00\u767e\u5ea6\u5b98\u7f51",
                type="task",
                confidence=1.0,
                domains=("apps",),
                explanation="misclassified as app.open",
                task_spans=(TaskSpan(id="span_1", text="\u6253\u5f00\u767e\u5ea6\u5b98\u7f51", start=0, end=6, domain="apps", confidence=1.0),),
            ),
            goal_synthesis=None,
            plan=apps_plan,
            candidates=(apps_plan,),
        ),
        PlanningArtifacts(
            analysis=UtteranceAnalysis(
                utterance="\u6253\u5f00\u767e\u5ea6\u5b98\u7f51",
                type="task",
                confidence=1.0,
                domains=("browser",),
                explanation="replanned into browser route",
                task_spans=(TaskSpan(id="span_1", text="\u6253\u5f00\u767e\u5ea6\u5b98\u7f51", start=0, end=6, domain="browser", confidence=1.0),),
            ),
            goal_synthesis=None,
            plan=browser_plan,
            candidates=(browser_plan,),
        ),
    ]

    def fake_plan_turn(*args, **kwargs):
        return plans.pop(0)

    monkeypatch.setattr("vibeos.broker.plan_turn", fake_plan_turn)

    result = broker.handle(CommandRequest("\u6253\u5f00\u767e\u5ea6\u5b98\u7f51"))

    assert result.status == "executed"
    assert result.overall_status == "completed"
    assert result.result["plan"]["selected_route_id"] == "browser_search_web_route"
    assert len(result.result["attempts"]) == 2
    assert result.result["attempts"][0]["failure"]["failure_class"] == "semantic_mismatch"
    assert result.result["attempts"][0]["replan_decision"]["action"] == "replan_with_constraints"
    assert result.result["attempts"][1]["selected_route_id"] == "browser_search_web_route"


def test_task_plan_loop_preserves_attempt_history_when_replan_exhausts_candidates(monkeypatch) -> None:
    broker = CapabilityBroker(
        intent_broker=RuleIntentBroker(),
        apps=FakeApps(),
        portal=FakePortal(),
        audit=AuditLog(make_audit_path("semantic-replan-exhausted")),
        reviews=ReviewStore(make_review_path("semantic-replan-exhausted")),
    )

    apps_plan = TaskPlan(
        schema_version="v0.5",
        plan_id="plan_apps_exhausted",
        utterance="\u6253\u5f00\u767e\u5ea6\u5b98\u7f51",
        display=DisplayFields(goal="open an application"),
        selected_route_id="apps_open_route",
        routes=(TaskRoute(id="apps_open_route", score=1.0, domain_id="apps", required_capabilities=("app.open",)),),
        steps=(
            TaskStep(
                id="open_app",
                action="app.open",
                capability_id="app.open",
                target={"name": "\u767e\u5ea6\u5b98\u7f51"},
                expected_state=ExpectedState(kind="app_opened_or_focused", fields={"app": "\u767e\u5ea6\u5b98\u7f51"}),
                preconditions=(StepPrecondition(kind="capability_available", capability_id="app.open"),),
                provenance=StepProvenance(source_span_id="span_1", planner="test"),
            ),
        ),
        provenance={"planner": "test"},
    )

    plans = [
        PlanningArtifacts(
            analysis=UtteranceAnalysis(
                utterance="\u6253\u5f00\u767e\u5ea6\u5b98\u7f51",
                type="task",
                confidence=1.0,
                domains=("apps",),
                explanation="misclassified as app.open",
                task_spans=(TaskSpan(id="span_1", text="\u6253\u5f00\u767e\u5ea6\u5b98\u7f51", start=0, end=6, domain="apps", confidence=1.0),),
            ),
            goal_synthesis=None,
            plan=apps_plan,
            candidates=(apps_plan,),
        ),
        PlanningArtifacts(
            analysis=UtteranceAnalysis(
                utterance="\u6253\u5f00\u767e\u5ea6\u5b98\u7f51",
                type="rejected",
                confidence=1.0,
                domains=(),
                explanation="no replacement route available",
                task_spans=(),
            ),
            goal_synthesis=None,
            plan=None,
            candidates=(),
        ),
    ]

    def fake_plan_turn(*args, **kwargs):
        return plans.pop(0)

    monkeypatch.setattr("vibeos.broker.plan_turn", fake_plan_turn)

    result = broker.handle(CommandRequest("\u6253\u5f00\u767e\u5ea6\u5b98\u7f51"))

    assert result.status == "rejected"
    assert result.result["run"]["final_outcome"] == "failed"
    assert len(result.result["attempts"]) == 1
    assert result.result["attempts"][0]["failure"]["failure_class"] == "semantic_mismatch"


def test_normal_ask_returns_plan_review_for_mixed_clipboard_request() -> None:
    broker = CapabilityBroker(
        intent_broker=RuleIntentBroker(),
        apps=FakeApps(),
        portal=FakePortal(),
        audit=AuditLog(make_audit_path("normal-ask-mixed-review")),
        reviews=ReviewStore(make_review_path("normal-ask-mixed-review")),
    )

    result = broker.handle(CommandRequest("explain clipboard permissions and then copy hello to clipboard"))

    assert result.status == "review_required"
    assert result.review_id
    assert result.result["analysis"]["type"] == "mixed"
    assert result.result["plan_review"]["status"] == "review_required"


def test_approved_clipboard_plan_reports_typed_adapter_timeout() -> None:
    broker = CapabilityBroker(
        intent_broker=RuleIntentBroker(),
        clipboard=TimeoutClipboard(),
        audit=AuditLog(make_audit_path("clipboard-timeout-plan")),
        reviews=ReviewStore(make_review_path("clipboard-timeout-plan")),
    )

    pending = broker.handle(CommandRequest("clipboard hello"))
    approved = broker.handle(CommandRequest("", review_id=pending.review_id, approve=True))
    step = approved.result["step_results"][0]

    assert pending.status == "review_required"
    assert approved.status == "failed"
    assert step["adapter"] == "/usr/bin/wl-copy"
    assert step["adapter_status"] == "timeout"
    assert step["error_code"] == "adapter_timeout"
    assert step["diagnostics"]["adapter_result_status"] == "timeout"


def test_normal_ask_notification_plan_reports_typed_adapter_metadata() -> None:
    broker = CapabilityBroker(
        intent_broker=RuleIntentBroker(),
        notifications=FakeNotifications(),
        audit=AuditLog(make_audit_path("notification-plan")),
        reviews=ReviewStore(make_review_path("notification-plan")),
    )

    result = broker.handle(CommandRequest("notify hello"))
    step = result.result["execution"]["step_results"][0]

    assert result.status == "executed"
    assert step["adapter"] == "notifications.send"
    assert step["capability_id"] == "notification.send"
    assert step["adapter_status"] == "succeeded"
    assert step["diagnostics"]["title"] == "VibeOS"


def media_span() -> TaskSpan:
    return TaskSpan(id="span_1", text="play baby", start=0, end=9, domain="media", confidence=0.9)


def make_review_path(name: str) -> Path:
    return Path(".vibeos") / f"test-{name}-{uuid4().hex}.jsonl"


def make_audit_path(name: str) -> Path:
    return Path(".vibeos") / f"audit-{name}-{uuid4().hex}.jsonl"
