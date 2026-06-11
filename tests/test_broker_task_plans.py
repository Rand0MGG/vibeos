from pathlib import Path
from uuid import uuid4
from urllib.parse import parse_qs, urlparse

from vibeos.apps import AppRegistry
from vibeos.audit import AuditLog
from vibeos.browser_state import record_browser_observation
from vibeos.broker import CapabilityBroker
from vibeos.goal_models import GoalSpec, GoalSynthesisProvenance, GoalSynthesisResult, ProviderExchange
from vibeos.execution_graph import execute_plan_graph
from vibeos.intent import IntentBroker
from vibeos.models import AppEntry, CommandRequest, Intent
from vibeos.intent import RuleIntentBroker
from vibeos.planner import PlanningArtifacts, browser_media_plan
from vibeos.portal import PortalAdapter
from vibeos.reviews import ReviewStore
from vibeos.models import WindowEntry
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


class ObservedPortal(PortalAdapter):
    def open_uri(self, uri: str) -> dict[str, object]:
        parsed = urlparse(uri)
        params = parse_qs(parsed.query)
        observed_query = ""
        for key in ("q", "query", "wd", "p", "text", "search_query"):
            values = params.get(key)
            if values:
                observed_query = str(values[0])
                break
        record_browser_observation(active_url=uri, query=observed_query or None, adapter="fake-browser")
        return {"status": "opened", "uri": uri, "adapter": "fake-browser"}


class FakeNotifications:
    def send(self, title: str, body: str = "") -> dict[str, object]:
        return {"status": "sent", "title": title, "adapter": "/usr/bin/notify-send"}


class FakeWindows:
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
        return {"status": "closed", "window_id": window.window_id}


class TimeoutClipboard:
    def write(self, text: str) -> dict[str, object]:
        return {"status": "timeout", "error": "clipboard helper timed out", "adapter": "/usr/bin/wl-copy"}


class FakeClipboard:
    def write(self, text: str) -> dict[str, object]:
        return {"status": "written", "adapter": "/usr/bin/wl-copy", "text": text}


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


def test_browser_media_route_does_not_treat_requested_query_as_observed_without_harness() -> None:
    broker = CapabilityBroker(
        intent_broker=StaticIntentBroker(Intent.unknown("should not reparse")),
        portal=FakePortal(),
        audit=AuditLog(make_audit_path("browser-context-verifier-incomplete")),
        reviews=ReviewStore(make_review_path("browser-context-verifier-incomplete")),
    )

    execution = broker.execute_task_plan(browser_media_plan("play baby", media_span(), "baby"))

    assert execution.status == "succeeded"
    assert execution.verification_status == "failed"
    assert execution.verification_results[0]["status"] == "failed"
    assert execution.acceptance_status == "indeterminate"


def test_browser_media_route_uses_observed_browser_context_for_verification_without_harness() -> None:
    broker = CapabilityBroker(
        intent_broker=RuleIntentBroker(),
        portal=ObservedPortal(),
        audit=AuditLog(make_audit_path("browser-context-observed-pass")),
        reviews=ReviewStore(make_review_path("browser-context-observed-pass")),
    )

    result = broker.handle(CommandRequest("search web for hello"))

    assert result.status == "executed"
    assert result.result["execution"]["status"] == "succeeded"
    assert result.result["execution"]["verification_status"] == "passed"
    assert result.result["execution"]["verification_results"][0]["status"] == "passed"
    assert result.result["execution"]["acceptance_status"] == "passed"


def test_browser_requests_expose_v06_runtime_state_on_main_path() -> None:
    broker = CapabilityBroker(
        intent_broker=RuleIntentBroker(),
        portal=ObservedPortal(),
        audit=AuditLog(make_audit_path("browser-v06-runtime-state")),
        reviews=ReviewStore(make_review_path("browser-v06-runtime-state")),
    )

    result = broker.handle(CommandRequest("search web for hello", debug=True))

    assert result.status == "executed"
    assert result.result["goal_runtime"]["goal_id"].startswith("goal_")
    assert result.result["goal_runtime"]["status"] == "completed"
    assert result.result["environment_profile"]["search_policy"] == "browser_first"
    assert result.result["selected_strategy_id"] == "strategy_browser_search_web_route"
    assert result.result["strategy_candidates"]
    assert result.result["run_ledger"]["terminal_outcome"]["status"] == "completed"
    assert result.result["debug_trace"]["runtime_v0_6"]["goal_runtime"]["goal_id"] == result.result["goal_runtime"]["goal_id"]
    assert result.result["debug_trace"]["runtime_v0_6"]["environment_profile"]["search_policy"] == "browser_first"
    assert result.result["debug_trace"]["runtime_v0_6"]["provider_artifacts"]


def test_app_requests_expose_v06_runtime_state_on_main_path() -> None:
    broker = CapabilityBroker(
        intent_broker=RuleIntentBroker(),
        apps=FakeApps(),
        audit=AuditLog(make_audit_path("app-v06-runtime-state")),
        reviews=ReviewStore(make_review_path("app-v06-runtime-state")),
    )

    result = broker.handle(CommandRequest("open browser", debug=True))

    assert result.status == "executed"
    assert result.result["goal_runtime"]["goal_id"].startswith("goal_")
    assert result.result["goal_runtime"]["status"] == "completed"
    assert result.result["environment_profile"]["search_policy"] == "balanced"
    assert result.result["selected_strategy_id"] == "strategy_apps_open_route"
    assert result.result["strategy_candidates"]
    assert result.result["run_ledger"]["terminal_outcome"]["status"] == "completed"
    assert result.result["execution"]["acceptance_status"] == "passed"
    assert result.result["debug_trace"]["runtime_v0_6"]["goal_runtime"]["goal_id"] == result.result["goal_runtime"]["goal_id"]
    assert result.result["debug_trace"]["runtime_v0_6"]["environment_profile"]["search_policy"] == "balanced"
    assert result.result["debug_trace"]["runtime_v0_6"]["provider_artifacts"]


def test_broker_reuses_goal_runtime_across_repeated_browser_turns() -> None:
    broker = CapabilityBroker(
        intent_broker=RuleIntentBroker(),
        portal=ObservedPortal(),
        audit=AuditLog(make_audit_path("browser-v06-repeated-turns")),
        reviews=ReviewStore(make_review_path("browser-v06-repeated-turns")),
    )

    first = broker.handle(CommandRequest("search web for hello"))
    second = broker.handle(CommandRequest("search web for hello"))

    assert first.result["goal_runtime"]["goal_id"] == second.result["goal_runtime"]["goal_id"]
    assert len(first.result["goal_runtime"]["turn_ids"]) == 1
    assert len(second.result["goal_runtime"]["turn_ids"]) == 2
    assert first.result["run"]["run_id"] != second.result["run"]["run_id"]


def test_broker_reuses_goal_runtime_across_repeated_app_turns() -> None:
    broker = CapabilityBroker(
        intent_broker=RuleIntentBroker(),
        apps=FakeApps(),
        audit=AuditLog(make_audit_path("app-v06-repeated-turns")),
        reviews=ReviewStore(make_review_path("app-v06-repeated-turns")),
    )

    first = broker.handle(CommandRequest("open browser"))
    second = broker.handle(CommandRequest("open browser"))

    assert first.result["goal_runtime"]["goal_id"] == second.result["goal_runtime"]["goal_id"]
    assert len(first.result["goal_runtime"]["turn_ids"]) == 1
    assert len(second.result["goal_runtime"]["turn_ids"]) == 2
    assert first.result["run"]["run_id"] != second.result["run"]["run_id"]


def test_broker_v06_bridge_replaces_app_strategy_with_browser_strategy_without_changing_goal(monkeypatch) -> None:
    broker = CapabilityBroker(
        intent_broker=RuleIntentBroker(),
        apps=FakeApps(),
        portal=ObservedPortal(),
        audit=AuditLog(make_audit_path("v06-main-path-strategy-replacement")),
        reviews=ReviewStore(make_review_path("v06-main-path-strategy-replacement")),
    )

    apps_plan = TaskPlan(
        schema_version="v0.5",
        plan_id="plan_apps_notion",
        utterance="open Notion",
        display=DisplayFields(goal="open Notion as an installed app"),
        selected_route_id="apps_open_route",
        routes=(TaskRoute(id="apps_open_route", score=3.0, domain_id="apps", required_capabilities=("app.open",)),),
        steps=(
            TaskStep(
                id="open_notion_app",
                action="app.open",
                capability_id="app.open",
                target={"name": "Notion"},
                expected_state=ExpectedState(kind="app_opened_or_focused", fields={"app": "Notion"}),
                preconditions=(StepPrecondition(kind="capability_available", capability_id="app.open"),),
                provenance=StepProvenance(source_span_id="span_1", planner="test"),
            ),
        ),
        provenance={"planner": "test"},
    )
    browser_plan = TaskPlan(
        schema_version="v0.5",
        plan_id="plan_browser_notion",
        utterance="open Notion",
        display=DisplayFields(goal="search Notion in the browser"),
        selected_route_id="browser_search_web_route",
        routes=(TaskRoute(id="browser_search_web_route", score=1.0, domain_id="browser", required_capabilities=("browser.search_web",), default_verifier_ids=("browser_search_route_completed",)),),
        steps=(
            TaskStep(
                id="search_notion",
                action="browser.search_web",
                capability_id="browser.search_web",
                target={"query": "Notion"},
                expected_state=ExpectedState(kind="search_results_available", fields={"query": "Notion"}),
                preconditions=(StepPrecondition(kind="capability_available", capability_id="browser.search_web"),),
                provenance=StepProvenance(source_span_id="span_1", planner="test"),
            ),
        ),
        provenance={"planner": "test"},
    )
    goal_spec = GoalSpec(
        goal_id="goal_open_notion_main_path",
        goal_text="open Notion",
        goal_type="app_open",
        candidate_domain_ids=("apps", "browser"),
        required_capability_ids=("app.open", "browser.search_web"),
        synthesis_provenance=GoalSynthesisProvenance(provider_name="test", provider_version="v0.6"),
    )
    planning = PlanningArtifacts(
        analysis=UtteranceAnalysis(
            utterance="open Notion",
            type="task",
            confidence=1.0,
            domains=("apps", "browser"),
            explanation="try app first and then browser fallback",
            task_spans=(TaskSpan(id="span_1", text="open Notion", start=0, end=11, domain="apps", confidence=1.0),),
        ),
        goal_synthesis=GoalSynthesisResult(
            status="ready",
            goal_spec=goal_spec,
            message="goal synthesis completed",
            exchange=ProviderExchange(provider_name="test", model_name="test", normalized_output={"status": "ready"}),
        ),
        plan=apps_plan,
        candidates=(apps_plan, browser_plan),
    )

    monkeypatch.setattr("vibeos.broker.plan_turn", lambda *args, **kwargs: planning)

    result = broker.handle(CommandRequest("open Notion", debug=True))

    assert result.status == "executed"
    assert result.overall_status == "completed"
    assert result.result["goal_runtime"]["goal_id"] == "goal_open_notion_main_path"
    assert result.result["selected_strategy_id"] == "strategy_browser_search_web_route"
    assert len(result.result["attempts"]) == 2
    assert result.result["attempts"][0]["failure"]["failure_class"] == "semantic_mismatch"
    assert result.result["attempts"][1]["selected_route_id"] == "browser_search_web_route"
    assert result.result["run_ledger"]["terminal_outcome"]["status"] == "completed"


def test_window_list_requests_expose_v06_runtime_state_on_main_path() -> None:
    broker = CapabilityBroker(
        intent_broker=RuleIntentBroker(),
        windows=FakeWindows(),
        audit=AuditLog(make_audit_path("window-list-v06-runtime-state")),
        reviews=ReviewStore(make_review_path("window-list-v06-runtime-state")),
    )

    result = broker.handle(CommandRequest("list windows", debug=True))

    assert result.status == "executed"
    assert result.result["goal_runtime"]["status"] == "completed"
    assert result.result["selected_strategy_id"] == "strategy_window_list_route"
    assert result.result["execution"]["step_results"][0]["adapter"] == "windows.registry"
    assert result.result["run_ledger"]["terminal_outcome"]["status"] == "completed"


def test_notification_requests_expose_v06_runtime_state_on_main_path() -> None:
    broker = CapabilityBroker(
        intent_broker=RuleIntentBroker(),
        notifications=FakeNotifications(),
        audit=AuditLog(make_audit_path("notification-v06-runtime-state")),
        reviews=ReviewStore(make_review_path("notification-v06-runtime-state")),
    )

    result = broker.handle(CommandRequest("notify hello", debug=True))

    assert result.status == "executed"
    assert result.result["goal_runtime"]["status"] == "completed"
    assert result.result["selected_strategy_id"] == "strategy_notification_send_route"
    assert result.result["execution"]["step_results"][0]["adapter"] == "notifications.send"
    assert result.result["run_ledger"]["terminal_outcome"]["status"] == "completed"


def test_system_status_requests_expose_v06_runtime_state_on_main_path() -> None:
    broker = CapabilityBroker(
        intent_broker=RuleIntentBroker(),
        portal=FakePortal(),
        audit=AuditLog(make_audit_path("system-status-v06-runtime-state")),
        reviews=ReviewStore(make_review_path("system-status-v06-runtime-state")),
    )

    result = broker.handle(CommandRequest("system status", debug=True))

    assert result.status == "executed"
    assert result.result["goal_runtime"]["status"] == "completed"
    assert result.result["selected_strategy_id"] == "strategy_system_status_route"
    assert result.result["execution"]["step_results"][0]["adapter"] == "system.status"
    assert result.result["run_ledger"]["terminal_outcome"]["status"] == "completed"


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
    assert result.result["goal_runtime"]["status"] == "needs_review"
    assert result.result["run_ledger"]["terminal_outcome"]["status"] == "needs_review"


def test_approved_clipboard_plan_continues_same_v06_goal_runtime() -> None:
    broker = CapabilityBroker(
        intent_broker=RuleIntentBroker(),
        clipboard=FakeClipboard(),
        audit=AuditLog(make_audit_path("clipboard-v06-approve")),
        reviews=ReviewStore(make_review_path("clipboard-v06-approve")),
    )

    pending = broker.handle(CommandRequest("clipboard hello"))
    approved = broker.handle(CommandRequest("", review_id=pending.review_id, approve=True))

    assert pending.status == "review_required"
    assert approved.status == "executed"
    assert pending.result["goal_runtime"]["goal_id"] == approved.result["goal_runtime"]["goal_id"]
    assert approved.result["goal_runtime"]["status"] == "completed"
    assert approved.result["selected_strategy_id"] == "strategy_clipboard_write_route"
    assert approved.result["run_ledger"]["terminal_outcome"]["status"] == "completed"
    assert approved.result["step_results"][0]["adapter"] == "/usr/bin/wl-copy"


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


def test_window_close_request_enters_v06_needs_review_state() -> None:
    broker = CapabilityBroker(
        intent_broker=RuleIntentBroker(),
        windows=FakeWindows(),
        audit=AuditLog(make_audit_path("window-close-v06-review")),
        reviews=ReviewStore(make_review_path("window-close-v06-review")),
    )

    result = broker.handle(CommandRequest("close firefox"))

    assert result.status == "review_required"
    assert result.review_id
    assert result.result["plan_review"]["status"] == "review_required"
    assert result.result["goal_runtime"]["status"] == "needs_review"
    assert result.result["selected_strategy_id"] == "strategy_window_close_route"
    assert result.result["run_ledger"]["terminal_outcome"]["status"] == "needs_review"


def test_approved_window_close_plan_continues_same_v06_goal_runtime() -> None:
    broker = CapabilityBroker(
        intent_broker=RuleIntentBroker(),
        windows=FakeWindows(),
        audit=AuditLog(make_audit_path("window-close-v06-approve")),
        reviews=ReviewStore(make_review_path("window-close-v06-approve")),
    )

    pending = broker.handle(CommandRequest("close firefox"))
    approved = broker.handle(CommandRequest("", review_id=pending.review_id, approve=True))

    assert pending.status == "review_required"
    assert approved.status == "executed"
    assert pending.result["goal_runtime"]["goal_id"] == approved.result["goal_runtime"]["goal_id"]
    assert approved.result["goal_runtime"]["status"] == "completed"
    assert approved.result["selected_strategy_id"] == "strategy_window_close_route"
    assert approved.result["run_ledger"]["terminal_outcome"]["status"] == "completed"
    assert approved.result["step_results"][0]["adapter"] == "windows.registry"
    assert approved.result["step_results"][0]["capability_id"] == "window.close"
    assert approved.result["step_results"][0]["result"]["status"] == "closed"


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
