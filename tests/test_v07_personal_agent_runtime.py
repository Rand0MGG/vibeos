from __future__ import annotations

from pathlib import Path
from urllib.parse import parse_qs, urlparse
from uuid import uuid4

from vibeos.agent_runtime import AgentRuntime, EnvironmentProfile
from vibeos.app_fixtures import AppSearchFixture
from vibeos.assistant_semantics import (
    AssistantCompletionSemantics,
    AssistantIntent,
    AssistantIntentTarget,
)
from vibeos.browser_state import record_browser_observation
from vibeos.broker import CapabilityBroker
from vibeos.goal_models import GoalSpec, GoalSynthesisProvenance
from vibeos.goal_synthesizer import GoalSynthesizer
from vibeos.nlu import analyze_utterance
from vibeos.portal import PortalAdapter
from vibeos.reviews import ReviewStore
from vibeos.strategy import (
    RecoveryPolicy,
    StrategyCandidate,
    StrategyConstraint,
    StrategySelectionProvider,
    StrategySelectionResult,
    StrategyStep,
    make_strategy_decision,
)
from vibeos.task_models import DisplayFields, ExpectedState, StepPrecondition, StepProvenance, TaskPlan, TaskRoute, TaskStep
from vibeos.tool_protocol import ToolRegistry, ToolResult, ToolSpec


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
        record_browser_observation(active_url=uri, query=observed_query or None, adapter="v07-test-browser")
        return {"status": "opened", "uri": uri, "adapter": "v07-test-browser"}


class FixedSelectionProvider(StrategySelectionProvider):
    provider_name = "fake_strategy_selector"
    model_name = "fake-structured"

    def __init__(self, selected_strategy_id: str) -> None:
        self.selected_strategy_id = selected_strategy_id

    def decide(self, *, utterance: str, eligible, constraints, environment, attempts, last_failure_class: str):
        return StrategySelectionResult(
            decision=make_strategy_decision(
                action="select",
                reason="fake provider selected an allowed strategy",
                selected_strategy_id=self.selected_strategy_id,
                constraints=constraints,
                failure_class=last_failure_class,
                provider_name=self.provider_name,
                model_name=self.model_name,
            ),
            request_payload={"utterance": utterance, "eligible": [candidate.strategy_id for _, candidate in eligible]},
            response_payload={"action": "select", "selected_strategy_id": self.selected_strategy_id, "reason": "fake provider selected an allowed strategy"},
        )


def test_goal_synthesizer_emits_assistant_intent_for_official_site_request() -> None:
    utterance = "open Baidu official website"

    result = GoalSynthesizer().synthesize(utterance, analyze_utterance(utterance))

    assert result.status == "ready"
    assert result.goal_spec is not None
    assert result.goal_spec.assistant_intent is not None
    assert result.goal_spec.assistant_intent.objective_kind == "open_named_website"
    assert result.goal_spec.assistant_intent.completion.kind == "page_identity"
    assert "direct-open" in result.goal_spec.assistant_intent.interaction_hints


def test_goal_synthesizer_recovers_in_app_search_even_when_analysis_is_misrouted() -> None:
    utterance = "search chat history in WeChat for Alice"

    analysis = analyze_utterance(utterance)
    result = GoalSynthesizer().synthesize(utterance, analysis)

    assert analysis.type in {"task", "rejected"}
    assert result.status == "ready"
    assert result.goal_spec is not None
    assert result.goal_spec.assistant_intent is not None
    assert result.goal_spec.assistant_intent.objective_kind == "in_app_search"
    assert result.goal_spec.required_capability_ids == ("app.search_history",)


def test_recovery_policy_prefers_more_structured_interaction_surface() -> None:
    policy = RecoveryPolicy()
    goal = make_goal("goal_surface", "open Baidu official website", official_site_intent("open Baidu official website"))
    native = make_strategy(goal, "native", "native_route", "native_action")
    structured = make_strategy(goal, "structured", "structured_route", "structured_ui_action")
    computer_use = make_strategy(goal, "computer", "computer_route", "computer_use_action")

    decision = policy.select_strategy(
        strategies=(computer_use, structured, native),
        constraints=StrategyConstraint(
            do_not_repeat_strategy_ids=("native",),
            do_not_repeat_route_ids=("native_route",),
            candidate_interaction_surfaces=("structured_ui_action", "computer_use_action"),
        ),
        environment=make_environment(),
        attempts=(),
        last_failure_class="semantic_mismatch",
    )

    assert decision.selected_strategy_id == "structured"
    assert decision.strategy_decision_id.startswith("sdec_")
    assert decision.provider_name == "local"
    assert decision.fallback_used is True


def test_recovery_policy_selects_weaker_surface_when_stronger_surface_is_unavailable() -> None:
    policy = RecoveryPolicy()
    goal = make_goal("goal_surface_blocked", "search chat history in WeChat for Alice", app_search_intent("WeChat", "Alice"))
    structured = make_strategy(goal, "structured", "structured_route", "structured_ui_action")
    computer_use = make_strategy(goal, "computer", "computer_route", "computer_use_action")

    decision = policy.select_strategy(
        strategies=(structured, computer_use),
        constraints=StrategyConstraint(
            do_not_repeat_strategy_ids=("structured",),
            do_not_repeat_route_ids=("structured_route",),
            candidate_interaction_surfaces=("computer_use_action",),
        ),
        environment=make_environment(available_interaction_surfaces=("computer_use_action",)),
        attempts=(),
        last_failure_class="semantic_mismatch",
    )

    assert decision.selected_strategy_id == "computer"


def test_recovery_policy_allows_bounded_provider_override_within_eligible_candidates() -> None:
    goal = make_goal("goal_model_choice", "open Baidu official website", official_site_intent("open Baidu official website"))
    native = make_strategy(goal, "native", "native_route", "native_action")
    structured = make_strategy(goal, "structured", "structured_route", "structured_ui_action")
    policy = RecoveryPolicy(provider=FixedSelectionProvider("structured"))

    decision = policy.select_strategy(
        utterance=goal.goal_text,
        strategies=(native, structured),
        constraints=StrategyConstraint(),
        environment=make_environment(),
        attempts=(),
        last_failure_class="none",
    )

    assert decision.selected_strategy_id == "structured"
    assert decision.provider_name == "fake_strategy_selector"
    assert decision.fallback_used is False


def test_recovery_policy_semantic_mismatch_no_longer_forces_browser_surface_for_app_open() -> None:
    policy = RecoveryPolicy()
    goal = make_goal(
        "goal_app_open",
        "open Notion",
        AssistantIntent(
            objective_kind="open_application",
            target=AssistantIntentTarget(entity_type="application", display_name="Notion", app_name="Notion"),
            completion=AssistantCompletionSemantics(kind="application_state", success_signal="application is opened"),
            interaction_hints=("native-open",),
            preferred_domains=("apps",),
        ),
    )
    plan = TaskPlan(
        schema_version="v0.5",
        plan_id="plan_app_open",
        utterance="open Notion",
        display=DisplayFields(goal="open Notion"),
        selected_route_id="apps_open_route",
        routes=(TaskRoute(id="apps_open_route", score=1.0, domain_id="apps", required_capabilities=("app.open",)),),
        steps=(
            TaskStep(
                id="open_notion",
                action="app.open",
                capability_id="app.open",
                target={"name": "Notion"},
                expected_state=ExpectedState(kind="app_opened_or_focused", fields={"app": "Notion"}),
                preconditions=(StepPrecondition(kind="capability_available", capability_id="app.open"),),
                provenance=StepProvenance(source_span_id="span_1", planner="test"),
            ),
        ),
    )
    strategy = StrategyCandidate(
        strategy_id="native_open_notion",
        goal_id=goal.goal_id,
        title="open Notion natively",
        route_id="apps_open_route",
        capability_surface="desktop-linux",
        task_plan=plan,
        steps=(StrategyStep(tool_id="app.open", input_payload={"name": "Notion"}, task_step_id="open_notion"),),
        interaction_surface="native_action",
        priority=1.0,
    )

    constraints = policy.next_constraints(strategy, "semantic_mismatch")

    assert constraints.candidate_capability_surfaces == ()
    assert constraints.do_not_repeat_capability_ids == ("app.open",)


def test_browser_goal_direct_open_strategy_is_accepted_with_page_identity_evidence() -> None:
    broker = CapabilityBroker(
        portal=ObservedPortal(),
        reviews=ReviewStore(make_local_path("reviews-direct-open")),
        browser_site_catalog={"baidu official website": "https://www.baidu.com"},
    )

    result = broker.handle(command("open Baidu official website", debug=True))

    assert result.status == "executed"
    assert result.result["assistant_intent"]["objective_kind"] == "open_named_website"
    assert result.result["selected_strategy_id"] == "strategy_browser_named_direct_open_route"
    assert result.result["execution"]["acceptance_status"] == "passed"
    assert result.result["attempts"][0]["interaction_surface"] == "native_action"
    assert result.result["strategy_candidates"][0]["interaction_surface"] == "native_action"


def test_browser_goal_replaces_direct_open_with_search_followup_without_changing_goal() -> None:
    broker = CapabilityBroker(
        portal=ObservedPortal(),
        reviews=ReviewStore(make_local_path("reviews-search-followup")),
        browser_search_catalog={"baidu official website": {"official_url": "https://www.baidu.com"}},
    )

    result = broker.handle(command("open Baidu official website", debug=True))

    assert result.status == "executed"
    assert result.result["goal_runtime"]["goal_id"] == result.result["run"]["goal_id"]
    assert len(result.result["attempts"]) == 2
    assert result.result["attempts"][0]["failure"]["failure_class"] == "semantic_mismatch"
    assert result.result["attempts"][1]["selected_route_id"] == "browser_search_followup_route"
    assert result.result["attempts"][1]["interaction_surface"] == "structured_ui_action"
    assert result.result["execution"]["acceptance_status"] == "passed"
    assert result.result["run_ledger"]["strategy_history"][1]["failure_class"] == "semantic_mismatch"


def test_search_page_open_alone_is_not_accepted_as_official_site_completion() -> None:
    broker = CapabilityBroker(portal=ObservedPortal())
    runtime = AgentRuntime(broker._build_v06_tool_registry())
    session = runtime.create_session("search_only")
    goal = runtime.start_goal(session.session_id, make_goal("goal_search_only", "open Baidu official website", official_site_intent("Baidu official website")))
    strategy = make_official_site_search_only_strategy(goal.goal_id, "Baidu official website")

    result = runtime.continue_goal(
        session_id=session.session_id,
        goal_id=goal.goal_id,
        utterance=goal.goal_spec.goal_text,
        strategies=(strategy,),
        environment=make_environment(),
    )

    assert result.terminal_outcome.status == "failed"
    assert result.ledger.attempts[0].failure_class == "acceptance_failed"
    assert result.ledger.attempts[0].interaction_surface == "native_action"


def test_app_fixture_structured_search_is_preferred_and_accepted() -> None:
    fixture = AppSearchFixture(
        app_name="WeChat",
        fixture_id="wechat-structured",
        visible_controls=("search_box",),
        shortcut_search_enabled=True,
        search_results={"alice": ("Alice",)},
    )
    broker = CapabilityBroker(
        reviews=ReviewStore(make_local_path("reviews-app-structured")),
        app_fixture_catalog={"wechat": fixture},
    )

    result = broker.handle(command("search chat history in WeChat for Alice", debug=True))

    assert result.status == "executed"
    assert result.result["assistant_intent"]["objective_kind"] == "in_app_search"
    assert result.result["selected_strategy_id"] == "strategy_app_structured_search_route"
    assert result.result["attempts"][0]["interaction_surface"] == "structured_ui_action"
    assert result.result["execution"]["acceptance_status"] == "passed"


def test_app_fixture_shortcut_fallback_replaces_structured_search() -> None:
    fixture = AppSearchFixture(
        app_name="WeChat",
        fixture_id="wechat-shortcut",
        visible_controls=(),
        shortcut_search_enabled=True,
        search_results={"alice": ("Alice",)},
    )
    broker = CapabilityBroker(
        reviews=ReviewStore(make_local_path("reviews-app-shortcut")),
        app_fixture_catalog={"wechat": fixture},
    )

    result = broker.handle(command("search chat history in WeChat for Alice", debug=True))

    assert result.status == "executed"
    assert len(result.result["attempts"]) == 2
    assert result.result["attempts"][0]["failure"]["failure_class"] == "semantic_mismatch"
    assert result.result["attempts"][1]["selected_route_id"] == "app_shortcut_search_route"
    assert result.result["attempts"][1]["interaction_surface"] == "computer_use_action"
    assert result.result["execution"]["acceptance_status"] == "passed"


def test_app_fixture_missing_controls_does_not_false_accept_goal() -> None:
    fixture = AppSearchFixture(
        app_name="WeChat",
        fixture_id="wechat-missing",
        visible_controls=(),
        shortcut_search_enabled=False,
        search_results={"alice": ("Alice",)},
    )
    broker = CapabilityBroker(
        reviews=ReviewStore(make_local_path("reviews-app-missing")),
        app_fixture_catalog={"wechat": fixture},
    )

    result = broker.handle(command("search chat history in WeChat for Alice", debug=True))

    assert result.status == "failed"
    assert result.result["execution"]["acceptance_status"] == "failed"
    assert result.result["run_ledger"]["terminal_outcome"]["status"] in {"failed", "blocked"}


def command(utterance: str, *, debug: bool = False):
    from vibeos.models import CommandRequest

    return CommandRequest(utterance, debug=debug)


def make_environment(
    *,
    available_interaction_surfaces=("native_action", "structured_ui_action", "computer_use_action"),
) -> EnvironmentProfile:
    return EnvironmentProfile(
        platform="linux",
        transport_mode="local",
        daemon_available=False,
        desktop_integration_available=True,
        connectivity_limitations="offline",
        deployment_profile="test",
        region="local",
        search_policy="balanced",
        available_interaction_surfaces=available_interaction_surfaces,
    )


def make_local_path(name: str) -> Path:
    return Path(".vibeos") / f"{name}-{uuid4().hex}.jsonl"


def make_goal(goal_id: str, text: str, assistant_intent: AssistantIntent) -> GoalSpec:
    required_capability = "app.search_history" if assistant_intent.objective_kind == "in_app_search" else "browser.search_web"
    return GoalSpec(
        goal_id=goal_id,
        goal_text=text,
        goal_type=assistant_intent.objective_kind,
        candidate_domain_ids=assistant_intent.preferred_domains,
        required_capability_ids=(required_capability,),
        assistant_intent=assistant_intent,
        synthesis_provenance=GoalSynthesisProvenance(provider_name="test", provider_version="v0.7"),
    )


def official_site_intent(name: str) -> AssistantIntent:
    return AssistantIntent(
        objective_kind="open_named_website",
        target=AssistantIntentTarget(entity_type="website", display_name=name),
        completion=AssistantCompletionSemantics(
            kind="page_identity",
            success_signal="final page identity matches the intended official site",
            requires_follow_up_navigation=True,
        ),
        interaction_hints=("direct-open", "lookup", "follow-up-navigation"),
        preferred_domains=("browser",),
    )


def app_search_intent(app_name: str, query: str) -> AssistantIntent:
    return AssistantIntent(
        objective_kind="in_app_search",
        target=AssistantIntentTarget(entity_type="chat_history", display_name=query, app_name=app_name, query_text=query),
        completion=AssistantCompletionSemantics(kind="target_presence", success_signal="target appears in app search results"),
        interaction_hints=("structured-search", "shortcut-fallback"),
        preferred_domains=("app_interaction",),
    )


def make_strategy(goal: GoalSpec, strategy_id: str, route_id: str, interaction_surface: str) -> StrategyCandidate:
    plan = TaskPlan(
        schema_version="v0.5",
        plan_id=f"plan_{strategy_id}",
        utterance=goal.goal_text,
        display=DisplayFields(goal=goal.goal_text),
        selected_route_id=route_id,
        routes=(TaskRoute(id=route_id, score=1.0, domain_id="browser", required_capabilities=("browser.search_web",)),),
        steps=(
            TaskStep(
                id=f"step_{strategy_id}",
                action="browser.search_web",
                capability_id="browser.search_web",
                target={"query": goal.goal_text},
                expected_state=ExpectedState(kind="search_results_available", fields={"query": goal.goal_text}),
                preconditions=(StepPrecondition(kind="capability_available", capability_id="browser.search_web"),),
                provenance=StepProvenance(source_span_id="span_1", planner="test"),
            ),
        ),
    )
    return StrategyCandidate(
        strategy_id=strategy_id,
        goal_id=goal.goal_id,
        title=strategy_id,
        route_id=route_id,
        capability_surface="browser",
        task_plan=plan,
        steps=(),
        interaction_surface=interaction_surface,
        priority=1.0,
        metadata={"enable_surface_downgrade": True},
    )


def make_official_site_search_only_strategy(goal_id: str, query: str) -> StrategyCandidate:
    plan = TaskPlan(
        schema_version="v0.5",
        plan_id="plan_search_only_official_site",
        utterance=f"open {query}",
        display=DisplayFields(goal=f"search {query}"),
        selected_route_id="browser_search_only_route",
        routes=(TaskRoute(id="browser_search_only_route", score=1.0, domain_id="browser", required_capabilities=("browser.search_web",)),),
        steps=(
            TaskStep(
                id="browser_search_only_step",
                action="browser.search_web",
                capability_id="browser.search_web",
                target={"query": query},
                expected_state=ExpectedState(kind="search_results_available", fields={"query": query}),
                preconditions=(StepPrecondition(kind="capability_available", capability_id="browser.search_web"),),
                provenance=StepProvenance(source_span_id="span_1", planner="test"),
            ),
        ),
    )
    return StrategyCandidate(
        strategy_id="browser_search_only_strategy",
        goal_id=goal_id,
        title="search-only official site",
        route_id="browser_search_only_route",
        capability_surface="browser",
        task_plan=plan,
        steps=(
            StrategyStep(tool_id="browser.search_web", input_payload={"query": query}, task_step_id="browser_search_only_step"),
            StrategyStep(tool_id="browser.observe_context", input_payload={}),
            StrategyStep(tool_id="browser.verify_goal_page_identity", input_payload={"name": query}),
        ),
        interaction_surface="native_action",
        priority=1.0,
    )
