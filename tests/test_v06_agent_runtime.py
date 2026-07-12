from dataclasses import replace

from vibeos.agent_runtime import AgentRuntime, EnvironmentProfile
from vibeos.goal_models import GoalSpec, GoalSynthesisProvenance
from vibeos.strategy import StrategyCandidate, StrategyStep
from vibeos.task_models import DisplayFields, ExpectedState, StepPrecondition, StepProvenance, TaskPlan, TaskRoute, TaskStep
from vibeos.tool_protocol import ToolExecutionContext, ToolRegistry, ToolResult, ToolSpec


def test_goal_survives_strategy_replacement() -> None:
    runtime = AgentRuntime(tool_registry=build_tool_registry())
    session = runtime.create_session("session_v06_replacement")
    goal = runtime.start_goal(session.session_id, notion_goal())

    result = runtime.continue_goal(
        session_id=session.session_id,
        goal_id=goal.goal_id,
        utterance=goal.goal_spec.goal_text,
        strategies=notion_strategies(goal.goal_id),
        environment=desktop_environment(),
    )

    assert result.goal_runtime.goal_id == goal.goal_id
    assert result.terminal_outcome.status == "completed"
    assert [attempt.strategy_id for attempt in result.ledger.attempts] == ["desktop_open_notion", "browser_search_notion"]
    assert result.ledger.attempts[0].failure_class == "semantic_mismatch"
    assert result.ledger.attempts[1].outcome_status == "completed"


def test_recovery_policy_selects_second_strategy() -> None:
    runtime = AgentRuntime(tool_registry=build_tool_registry())
    session = runtime.create_session("session_v06_recovery")
    goal = runtime.start_goal(session.session_id, notion_goal())

    result = runtime.continue_goal(
        session_id=session.session_id,
        goal_id=goal.goal_id,
        utterance=goal.goal_spec.goal_text,
        strategies=notion_strategies(goal.goal_id),
        environment=desktop_environment(),
    )

    history = result.ledger.strategy_history
    assert history[0].strategy_id == "desktop_open_notion"
    assert history[1].strategy_id == "browser_search_notion"
    assert history[0].strategy_decision_id.startswith("sdec_")
    assert history[1].failure_class == "semantic_mismatch"
    assert history[1].reason.startswith("selected replacement strategy")


def test_tool_families_participate_in_one_run() -> None:
    runtime = AgentRuntime(tool_registry=build_tool_registry())
    session = runtime.create_session("session_v06_tool_families")
    goal = runtime.start_goal(session.session_id, notion_goal())

    result = runtime.continue_goal(
        session_id=session.session_id,
        goal_id=goal.goal_id,
        utterance=goal.goal_spec.goal_text,
        strategies=notion_strategies(goal.goal_id),
        environment=desktop_environment(),
    )

    families = [invocation.family for attempt in result.ledger.attempts for invocation in attempt.tool_invocations]
    assert "resolver" in families
    assert "action" in families
    assert "observer" in families
    assert "verifier" in families


def test_wait_poll_tools_can_participate_in_main_loop() -> None:
    runtime = AgentRuntime(tool_registry=build_tool_registry())
    session = runtime.create_session("session_v06_wait_poll")
    goal = runtime.start_goal(session.session_id, browser_goal())
    plan = TaskPlan(
        schema_version="v0.6",
        plan_id="plan_browser_wait_poll",
        utterance="search docs for runtime",
        display=DisplayFields(goal="search docs for runtime"),
        selected_route_id="browser_wait_poll_route",
        routes=(TaskRoute(id="browser_wait_poll_route", score=1.0, domain_id="browser", required_capabilities=("browser.search_web",)),),
        steps=(
            TaskStep(
                id="browser_wait_poll_search",
                action="browser.search_web",
                capability_id="browser.search_web",
                target={"query": "runtime docs"},
                expected_state=ExpectedState(kind="search_results_available", fields={"query": "runtime docs"}),
                preconditions=(StepPrecondition(kind="capability_available", capability_id="browser.search_web"),),
                provenance=StepProvenance(source_span_id="span_1", planner="v0.6-test"),
            ),
        ),
    )
    strategy = StrategyCandidate(
        strategy_id="browser_wait_poll_strategy",
        goal_id=goal.goal_id,
        title="Search docs in browser with wait polling",
        route_id="browser_wait_poll_route",
        capability_surface="browser",
        task_plan=plan,
        steps=(
            StrategyStep(tool_id="browser.search_web", input_payload={"query": "runtime docs"}, task_step_id="browser_wait_poll_search"),
            StrategyStep(tool_id="wait.until_ready", input_payload={"query": "runtime docs"}),
            StrategyStep(tool_id="browser.observe_context", input_payload={"query": "runtime docs"}),
            StrategyStep(tool_id="browser.verify_query", input_payload={"query": "runtime docs"}),
        ),
        priority=2.0,
    )

    result = runtime.continue_goal(
        session_id=session.session_id,
        goal_id=goal.goal_id,
        utterance=goal.goal_spec.goal_text,
        strategies=(strategy,),
        environment=browser_first_environment(),
    )

    families = [invocation.family for invocation in result.ledger.attempts[0].tool_invocations]
    assert result.terminal_outcome.status == "completed"
    assert "wait_poll" in families


def test_run_ledger_records_strategy_attempts_and_evidence() -> None:
    runtime = AgentRuntime(tool_registry=build_tool_registry())
    session = runtime.create_session("session_v06_ledger")
    goal = runtime.start_goal(session.session_id, notion_goal())

    result = runtime.continue_goal(
        session_id=session.session_id,
        goal_id=goal.goal_id,
        utterance=goal.goal_spec.goal_text,
        strategies=notion_strategies(goal.goal_id),
        environment=desktop_environment(),
    )

    ledger = result.ledger
    assert ledger.session_id == session.session_id
    assert ledger.goal_id == goal.goal_id
    assert ledger.terminal_outcome["status"] == "completed"
    assert ledger.strategy_history
    assert ledger.attempts[0].tool_invocations
    assert any(item["kind"] == "observation" for item in ledger.attempts[1].evidence)
    assert any(item["kind"] == "verification" for item in ledger.attempts[1].evidence)


def test_minimal_vertical_slice_completes_with_fake_browser_observation() -> None:
    runtime = AgentRuntime(tool_registry=build_tool_registry())
    session = runtime.create_session("session_v06_slice")
    goal = runtime.start_goal(session.session_id, notion_goal())

    result = runtime.continue_goal(
        session_id=session.session_id,
        goal_id=goal.goal_id,
        utterance=goal.goal_spec.goal_text,
        strategies=notion_strategies(goal.goal_id),
        environment=desktop_environment(),
    )

    assert result.terminal_outcome.status == "completed"
    assert result.terminal_outcome.verifier_confirmed is True
    assert result.ledger.attempts[1].tool_invocations[-1].tool_id == "browser.verify_query"
    assert result.ledger.attempts[1].message == "verifier evidence accepted the goal outcome"


def test_minimal_vertical_slice_does_not_require_vm_shell_files_or_network() -> None:
    runtime = AgentRuntime(tool_registry=build_tool_registry())
    session = runtime.create_session("session_v06_offline")
    goal = runtime.start_goal(session.session_id, notion_goal())
    environment = replace(desktop_environment(), connectivity_limitations="offline")

    result = runtime.continue_goal(
        session_id=session.session_id,
        goal_id=goal.goal_id,
        utterance=goal.goal_spec.goal_text,
        strategies=notion_strategies(goal.goal_id),
        environment=environment,
    )

    tool_ids = {invocation.tool_id for attempt in result.ledger.attempts for invocation in attempt.tool_invocations}
    assert result.terminal_outcome.status == "completed"
    assert all(not tool_id.startswith("shell.") for tool_id in tool_ids)
    assert all(not tool_id.startswith("file.") for tool_id in tool_ids)
    assert all(not tool_id.startswith("network.") for tool_id in tool_ids)


def test_session_can_own_multiple_goals() -> None:
    runtime = AgentRuntime(tool_registry=build_tool_registry())
    session = runtime.create_session("session_v06_multi_goal")
    first = runtime.start_goal(session.session_id, notion_goal())
    second = runtime.start_goal(session.session_id, browser_goal())

    assert set(session.goals) == {first.goal_id, second.goal_id}


def test_one_goal_can_survive_multiple_turns() -> None:
    runtime = AgentRuntime(tool_registry=build_tool_registry())
    session = runtime.create_session("session_v06_turns")
    goal = runtime.start_goal(session.session_id, browser_goal())
    environment = browser_first_environment()

    first = runtime.continue_goal(
        session_id=session.session_id,
        goal_id=goal.goal_id,
        utterance=goal.goal_spec.goal_text,
        strategies=browser_only_strategy(goal.goal_id, verifier=False),
        environment=environment,
    )
    second = runtime.continue_goal(
        session_id=session.session_id,
        goal_id=goal.goal_id,
        utterance=goal.goal_spec.goal_text,
        strategies=browser_only_strategy(goal.goal_id, verifier=True),
        environment=environment,
    )

    assert first.terminal_outcome.status == "incomplete"
    assert first.goal_runtime.goal_id == goal.goal_id
    assert second.goal_runtime.goal_id == goal.goal_id
    assert len(second.goal_runtime.turn_ids) == 2
    assert second.terminal_outcome.status == "completed"


def test_environment_profile_can_prefer_browser_strategy() -> None:
    runtime = AgentRuntime(tool_registry=build_tool_registry())
    session = runtime.create_session("session_v06_env_pref")
    goal = runtime.start_goal(session.session_id, notion_goal())

    result = runtime.continue_goal(
        session_id=session.session_id,
        goal_id=goal.goal_id,
        utterance=goal.goal_spec.goal_text,
        strategies=notion_strategies(goal.goal_id),
        environment=browser_first_environment(),
    )

    assert result.selected_strategy_id == "browser_search_notion"
    assert len(result.ledger.attempts) == 1
    assert result.ledger.attempts[0].strategy_id == "browser_search_notion"


def test_environment_profile_constrains_tool_availability() -> None:
    runtime = AgentRuntime(tool_registry=build_tool_registry())
    session = runtime.create_session("session_v06_env_block")
    goal = runtime.start_goal(session.session_id, notion_goal())
    desktop_only = (notion_strategies(goal.goal_id)[0],)
    environment = replace(desktop_environment(), desktop_integration_available=False)

    result = runtime.continue_goal(
        session_id=session.session_id,
        goal_id=goal.goal_id,
        utterance=goal.goal_spec.goal_text,
        strategies=desktop_only,
        environment=environment,
    )

    assert result.terminal_outcome.status == "blocked"
    assert result.terminal_outcome.failure_class == "environment_unreachable"


def test_action_success_does_not_imply_goal_completion_when_verification_is_missing() -> None:
    runtime = AgentRuntime(tool_registry=build_tool_registry())
    session = runtime.create_session("session_v06_incomplete")
    goal = runtime.start_goal(session.session_id, browser_goal())

    result = runtime.continue_goal(
        session_id=session.session_id,
        goal_id=goal.goal_id,
        utterance=goal.goal_spec.goal_text,
        strategies=browser_only_strategy(goal.goal_id, verifier=False),
        environment=browser_first_environment(),
    )

    assert result.ledger.attempts[0].outcome_status == "incomplete"
    assert result.terminal_outcome.status == "incomplete"
    assert result.terminal_outcome.failure_class == "acceptance_unverified"


def test_debug_payload_exposes_goal_runtime_strategy_candidates_and_recovery_state() -> None:
    runtime = AgentRuntime(tool_registry=build_tool_registry())
    session = runtime.create_session("session_v06_debug")
    goal = runtime.start_goal(session.session_id, notion_goal())

    result = runtime.continue_goal(
        session_id=session.session_id,
        goal_id=goal.goal_id,
        utterance=goal.goal_spec.goal_text,
        strategies=notion_strategies(goal.goal_id),
        environment=desktop_environment(),
    )

    debug_payload = result.debug_payload
    assert debug_payload["goal_runtime"]["goal_id"] == goal.goal_id
    assert debug_payload["strategy_candidates"]
    assert debug_payload["selected_strategy_id"] == "browser_search_notion"
    assert debug_payload["action_plan_provenance"]
    assert len(debug_payload["recovery_decisions"]) == 2
    assert debug_payload["provider_artifacts"]
    assert debug_payload["provider_artifacts"][0]["strategy_decision_id"].startswith("sdec_")


def test_tool_protocol_supports_future_capability_surface_placeholders_and_wait_tools() -> None:
    registry = ToolRegistry(
        (
            ToolSpec(
                "workspace.describe", "environment", "workspace-local", lambda payload, context: ToolResult(status="succeeded", output={"scope": "workspace"})
            ),
            ToolSpec("shell.describe", "environment", "shell-local", lambda payload, context: ToolResult(status="succeeded", output={"scope": "shell"})),
            ToolSpec("wait.until_ready", "wait_poll", "browser", lambda payload, context: ToolResult(status="succeeded", output={"ready": True})),
        )
    )
    context = ToolExecutionContext(
        session_id="session_tool_protocol",
        goal_id="goal_tool_protocol",
        turn_id="turn_tool_protocol",
        attempt_id="attempt_tool_protocol",
        strategy_id="strategy_tool_protocol",
        environment=desktop_environment(),
        state={},
    )

    workspace_envelope, workspace_result = registry.invoke("workspace.describe", {}, context)
    shell_envelope, shell_result = registry.invoke("shell.describe", {}, context)
    wait_envelope, wait_result = registry.invoke("wait.until_ready", {}, context)

    assert workspace_envelope.family == "environment"
    assert workspace_envelope.capability_surface == "workspace-local"
    assert workspace_result.output["scope"] == "workspace"
    assert shell_envelope.family == "environment"
    assert shell_envelope.capability_surface == "shell-local"
    assert shell_result.output["scope"] == "shell"
    assert wait_envelope.family == "wait_poll"
    assert wait_envelope.capability_surface == "browser"
    assert wait_result.output["ready"] is True


def test_invalid_capability_surface_is_rejected() -> None:
    try:
        ToolSpec("invalid.surface", "environment", "desktop-macos", lambda payload, context: ToolResult(status="succeeded"))  # type: ignore[arg-type]
    except ValueError as exc:
        assert "unsupported capability surface" in str(exc)
    else:
        raise AssertionError("invalid capability surface should be rejected")


def notion_goal() -> GoalSpec:
    return GoalSpec(
        goal_id="goal_open_notion",
        goal_text="open Notion",
        goal_type="app_open",
        candidate_domain_ids=("apps", "browser"),
        required_capability_ids=("app.open", "browser.search_web"),
        synthesis_provenance=GoalSynthesisProvenance(provider_name="test", provider_version="v0.6"),
    )


def browser_goal() -> GoalSpec:
    return GoalSpec(
        goal_id="goal_search_docs",
        goal_text="search docs for runtime",
        goal_type="browser_search",
        candidate_domain_ids=("browser",),
        required_capability_ids=("browser.search_web",),
        synthesis_provenance=GoalSynthesisProvenance(provider_name="test", provider_version="v0.6"),
    )


def notion_strategies(goal_id: str) -> tuple[StrategyCandidate, ...]:
    desktop_plan = TaskPlan(
        schema_version="v0.6",
        plan_id="plan_desktop_open_notion",
        utterance="open Notion",
        display=DisplayFields(goal="open Notion via desktop app"),
        selected_route_id="desktop_open_route",
        routes=(TaskRoute(id="desktop_open_route", score=1.0, domain_id="apps", required_capabilities=("app.open",)),),
        steps=(
            TaskStep(
                id="desktop_open_step",
                action="app.open",
                capability_id="app.open",
                target={"name": "Notion"},
                expected_state=ExpectedState(kind="app_opened_or_focused", fields={"app": "Notion"}),
                preconditions=(StepPrecondition(kind="capability_available", capability_id="app.open"),),
                provenance=StepProvenance(source_span_id="span_1", planner="v0.6-test"),
            ),
        ),
    )
    browser_plan = TaskPlan(
        schema_version="v0.6",
        plan_id="plan_browser_search_notion",
        utterance="open Notion",
        display=DisplayFields(goal="find Notion in the browser"),
        selected_route_id="browser_search_route",
        routes=(TaskRoute(id="browser_search_route", score=1.0, domain_id="browser", required_capabilities=("browser.search_web",)),),
        steps=(
            TaskStep(
                id="browser_search_step",
                action="browser.search_web",
                capability_id="browser.search_web",
                target={"query": "Notion"},
                expected_state=ExpectedState(kind="search_results_available", fields={"query": "Notion"}),
                preconditions=(StepPrecondition(kind="capability_available", capability_id="browser.search_web"),),
                provenance=StepProvenance(source_span_id="span_1", planner="v0.6-test"),
            ),
        ),
    )
    return (
        StrategyCandidate(
            strategy_id="desktop_open_notion",
            goal_id=goal_id,
            title="Resolve the desktop app and open it",
            route_id="desktop_open_route",
            capability_surface="desktop-linux",
            task_plan=desktop_plan,
            steps=(
                StrategyStep(tool_id="apps.resolve_installed", input_payload={"name": "Notion"}, task_step_id="desktop_open_step"),
                StrategyStep(tool_id="app.open", input_payload={"name": "Notion"}, task_step_id="desktop_open_step"),
            ),
            priority=2.0,
            requires_desktop_integration=True,
        ),
        StrategyCandidate(
            strategy_id="browser_search_notion",
            goal_id=goal_id,
            title="Search for Notion in the browser",
            route_id="browser_search_route",
            capability_surface="browser",
            task_plan=browser_plan,
            steps=(
                StrategyStep(tool_id="browser.search_web", input_payload={"query": "Notion"}, task_step_id="browser_search_step"),
                StrategyStep(tool_id="browser.observe_context", input_payload={"query": "Notion"}),
                StrategyStep(tool_id="browser.verify_query", input_payload={"query": "Notion"}),
            ),
            priority=1.0,
        ),
    )


def browser_only_strategy(goal_id: str, *, verifier: bool) -> tuple[StrategyCandidate, ...]:
    plan = TaskPlan(
        schema_version="v0.6",
        plan_id="plan_browser_runtime_docs",
        utterance="search docs for runtime",
        display=DisplayFields(goal="search docs for runtime"),
        selected_route_id="browser_search_docs_route",
        routes=(TaskRoute(id="browser_search_docs_route", score=1.0, domain_id="browser", required_capabilities=("browser.search_web",)),),
        steps=(
            TaskStep(
                id="browser_docs_search",
                action="browser.search_web",
                capability_id="browser.search_web",
                target={"query": "runtime docs"},
                expected_state=ExpectedState(kind="search_results_available", fields={"query": "runtime docs"}),
                preconditions=(StepPrecondition(kind="capability_available", capability_id="browser.search_web"),),
                provenance=StepProvenance(source_span_id="span_1", planner="v0.6-test"),
            ),
        ),
    )
    steps = [
        StrategyStep(tool_id="browser.search_web", input_payload={"query": "runtime docs"}, task_step_id="browser_docs_search"),
        StrategyStep(tool_id="browser.observe_context", input_payload={"query": "runtime docs"}),
    ]
    if verifier:
        steps.append(StrategyStep(tool_id="browser.verify_query", input_payload={"query": "runtime docs"}))
    return (
        StrategyCandidate(
            strategy_id="browser_search_docs",
            goal_id=goal_id,
            title="Search docs in browser",
            route_id="browser_search_docs_route",
            capability_surface="browser",
            task_plan=plan,
            steps=tuple(steps),
            priority=2.0,
        ),
    )


def desktop_environment() -> EnvironmentProfile:
    return EnvironmentProfile(
        platform="linux",
        transport_mode="local",
        daemon_available=False,
        desktop_integration_available=True,
        connectivity_limitations="offline",
        deployment_profile="local-test",
        region="cn",
        search_policy="balanced",
    )


def browser_first_environment() -> EnvironmentProfile:
    return EnvironmentProfile(
        platform="linux",
        transport_mode="local",
        daemon_available=False,
        desktop_integration_available=False,
        connectivity_limitations="offline",
        deployment_profile="local-test",
        region="cn",
        search_policy="browser_first",
    )


def build_tool_registry() -> ToolRegistry:
    def desktop_available(environment: EnvironmentProfile) -> bool:
        return environment.desktop_integration_available

    def resolve_installed(payload, context):
        name = str(payload.get("name", ""))
        catalog = {"Firefox": "firefox.desktop"}
        match = catalog.get(name)
        return ToolResult(
            status="succeeded",
            output={"resolved_desktop_id": match},
            evidence={"requested_name": name, "match": match, "fixture_source": "local"},
            state_updates={"resolved_desktop_id": match},
        )

    def app_open(payload, context):
        name = str(payload.get("name", ""))
        resolved = context.state.get("resolved_desktop_id")
        if not resolved:
            return ToolResult(
                status="failed",
                message="no installed app matches the requested desktop target",
                failure_class="semantic_mismatch",
                evidence={"requested_name": name, "resolved_desktop_id": resolved, "fixture_source": "local"},
            )
        return ToolResult(
            status="succeeded",
            output={"opened_desktop_id": resolved},
            evidence={"desktop_id": resolved, "fixture_source": "local"},
        )

    def browser_search(payload, context):
        query = str(payload.get("query", ""))
        return ToolResult(
            status="succeeded",
            output={"active_url": f"https://search.local/?q={query}", "browser_query": query},
            state_updates={"active_url": f"https://search.local/?q={query}", "browser_query": query},
            evidence={"query": query, "fixture_source": "local"},
        )

    def browser_observe(payload, context):
        return ToolResult(
            status="succeeded",
            output={
                "observed_query": context.state.get("browser_query", ""),
                "observed_url": context.state.get("active_url", ""),
            },
            evidence={
                "query": context.state.get("browser_query", ""),
                "active_url": context.state.get("active_url", ""),
                "fixture_source": "local",
            },
        )

    def browser_verify(payload, context):
        expected = str(payload.get("query", ""))
        observed = str(context.state.get("observed_query") or context.state.get("browser_query") or "")
        accepted = observed == expected and bool(observed)
        return ToolResult(
            status="succeeded" if accepted else "failed",
            message="browser verifier accepted the observed query" if accepted else "browser verifier did not observe the expected query",
            evidence={"expected_query": expected, "observed_query": observed, "fixture_source": "local"},
            accepted=accepted,
            failure_class="acceptance_failed" if not accepted else "none",
        )

    def wait_until_ready(payload, context):
        return ToolResult(
            status="succeeded",
            output={"wait_complete": True},
            state_updates={"wait_complete": True},
            evidence={"query": payload.get("query"), "fixture_source": "local"},
        )

    return ToolRegistry(
        (
            ToolSpec("apps.resolve_installed", "resolver", "desktop-linux", resolve_installed, availability=desktop_available),
            ToolSpec("app.open", "action", "desktop-linux", app_open, availability=desktop_available),
            ToolSpec("browser.search_web", "action", "browser", browser_search),
            ToolSpec("wait.until_ready", "wait_poll", "browser", wait_until_ready),
            ToolSpec("browser.observe_context", "observer", "browser", browser_observe),
            ToolSpec("browser.verify_query", "verifier", "browser", browser_verify),
        )
    )
