from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

from vibeos.agent_runtime import AgentRuntime, EnvironmentProfile
from vibeos.failure_classifier import FailureClassifier
from vibeos.goal_loop import GoalLoop
from vibeos.loop_models import LoopObservation, LoopState
from vibeos.models import CommandRequest, PermissionReview
from vibeos.reviews import ReviewStore
from vibeos.strategy import StrategyStep
from vibeos.task_models import (
    DisplayFields,
    PlanExecutionResult,
    ReplanDecision,
    StepExecutionResult,
    StepReviewRecord,
    TaskPlan,
    TaskRoute,
    TaskStep,
)
from vibeos.tool_protocol import ToolRegistry, ToolResult, ToolSpec


class FakeObservationService:
    def __init__(self, sequence):
        self._sequence = list(sequence)

    def observe(self, *, plan, step, phase, level):
        payload = self._sequence.pop(0)
        return LoopObservation(
            observation_id=payload["observation_id"],
            level=level,
            phase="pre" if phase == "pre" else "post",
            packages=payload["packages"],
            route_id=plan.selected_route_id,
            step_id=step.id if step is not None else None,
        )


def test_review_store_persists_loop_snapshot_and_user_input() -> None:
    path = make_review_path("loop")
    store = ReviewStore(path)
    request = store.create_loop_review(
        "open that site",
        plan_payload={"plan_id": "plan_loop"},
        snapshot_payload={"loop_snapshot_id": "lsnap_1", "current_step_id": "step_1"},
        pending_reason="need approval",
        step_id="step_1",
        review_kind="user_input",
    )

    provided = store.provide_input(request.review_id, "open baidu.com")
    loaded = store.get(request.review_id)

    assert provided is not None
    assert provided.status == "provided"
    assert loaded is not None
    assert loaded.snapshot_payload == {"loop_snapshot_id": "lsnap_1", "current_step_id": "step_1"}
    assert loaded.supplemental_input == "open baidu.com"


def test_goal_loop_suspends_for_review_with_real_review_store() -> None:
    plan = make_plan("plan_review", ("step_1",))
    store = ReviewStore(make_review_path("goal-loop-review"))

    loop = GoalLoop(
        observation_service=FakeObservationService(
            [{"observation_id": "obs_pre", "packages": {"browser_context": {"active_url": ""}}}]
        ),
        planning_payload=lambda planning: {"plan": asdict(plan)},
        resolve_understanding_transition=lambda planning, trigger: planning,
        apply_replan_transition=lambda planning, decision, failure: planning,
        plan_again=lambda planning, request, excluded_routes, excluded_capabilities, candidate_domains: planning,
        review_step=lambda plan, step: (
            PermissionReview("L2", True, True, "approval required"),
            StepReviewRecord("srev_1", step.id, step.action, "L2", True, True, "approval required"),
        ),
        execute_step=lambda plan, step, request, attempt_id: (_ for _ in ()).throw(AssertionError("step must not execute before approval")),
        assess_plan_execution=lambda plan, step_results, request, run_id, understanding_id, candidate_set_id, route_decision_id: (_ for _ in ()).throw(AssertionError("final assessment should not run")),
        classify_failure=FailureClassifier().classify,
        decide_replan=lambda utterance, current_plan, attempts, failure, understanding_id, candidate_set_id, available_domain_ids: (_ for _ in ()).throw(AssertionError("replanner should not run")),
        persist_review=lambda utterance, plan, loop_state, step, reason: store.create_loop_review(
            utterance,
            plan_payload={"plan_id": plan.plan_id, "plan": asdict(plan)},
            snapshot_payload=asdict(loop_state),
            pending_reason=reason,
            step_id=step.id,
            review_kind="loop",
        ),
        persist_user_input=lambda utterance, planning, loop_state, reason: (_ for _ in ()).throw(AssertionError("user-input path should not run")),
    )

    result = loop.run(
        request=CommandRequest("close firefox"),
        planning=make_planning(plan),
        run_id="run_review",
        goal_id="goal_review",
    )
    stored = store.get(result.review_id or "")

    assert result.decision == "needs_review"
    assert result.overall_status == "needs_review"
    assert stored is not None
    assert stored.snapshot_payload is not None
    assert stored.snapshot_payload["current_step_id"] == "step_1"


def test_review_approval_resumes_from_pending_step_without_reexecuting_completed_steps() -> None:
    plan = make_plan("plan_resume", ("step_1", "step_2"), depends_on={"step_2": ("step_1",)})
    store = ReviewStore(make_review_path("goal-loop-resume"))
    review_gate = {"approved": False}
    execution_counts = {"step_1": 0, "step_2": 0}

    loop = GoalLoop(
        observation_service=FakeObservationService(
            [
                {"observation_id": "obs1_pre", "packages": {"browser_context": {"active_url": ""}}},
                {"observation_id": "obs1_post", "packages": {"browser_context": {"active_url": "https://example.com/step1"}}},
                {"observation_id": "obs2_pre", "packages": {"browser_context": {"active_url": "https://example.com/step1"}}},
                {"observation_id": "obs2_pre_resume", "packages": {"browser_context": {"active_url": "https://example.com/step1"}}},
                {"observation_id": "obs2_post_resume", "packages": {"browser_context": {"active_url": "https://example.com/done"}}},
            ]
        ),
        planning_payload=lambda planning: {"plan": asdict(plan)},
        resolve_understanding_transition=lambda planning, trigger: planning,
        apply_replan_transition=lambda planning, decision, failure: planning,
        plan_again=lambda planning, request, excluded_routes, excluded_capabilities, candidate_domains: planning,
        review_step=lambda plan, step: (
            PermissionReview("L2", step.id == "step_2" and not review_gate["approved"], True, "approval required" if step.id == "step_2" else "allowed"),
            StepReviewRecord(
                f"srev_{step.id}",
                step.id,
                step.action,
                "L2" if step.id == "step_2" else "L1",
                step.id == "step_2" and not review_gate["approved"],
                True,
                "approval required" if step.id == "step_2" else "allowed",
            ),
        ),
        execute_step=lambda plan, step, request, attempt_id: execute_success_step(step, execution_counts),
        assess_plan_execution=lambda plan, step_results, request, run_id, understanding_id, candidate_set_id, route_decision_id: PlanExecutionResult(
            plan_id=plan.plan_id,
            status="succeeded",
            step_results=step_results,
            execution_status="succeeded",
            acceptance_status="passed",
            overall_status="completed",
            acceptance_result={"message": "goal completed", "semantic_acceptance_decision_id": "sacc_test"},
        ),
        classify_failure=FailureClassifier().classify,
        decide_replan=lambda utterance, current_plan, attempts, failure, understanding_id, candidate_set_id, available_domain_ids: (_ for _ in ()).throw(AssertionError("replanner should not run")),
        persist_review=lambda utterance, plan, loop_state, step, reason: store.create_loop_review(
            utterance,
            plan_payload={"plan_id": plan.plan_id, "plan": asdict(plan)},
            snapshot_payload=asdict(loop_state),
            pending_reason=reason,
            step_id=step.id,
            review_kind="loop",
        ),
        persist_user_input=lambda utterance, planning, loop_state, reason: (_ for _ in ()).throw(AssertionError("user-input path should not run")),
    )

    first = loop.run(
        request=CommandRequest("close firefox"),
        planning=make_planning(plan),
        run_id="run_resume",
        goal_id="goal_resume",
    )
    stored = store.get(first.review_id or "")
    assert first.decision == "needs_review"
    assert first.state.completed_step_ids == ("step_1",)
    assert stored is not None
    assert stored.snapshot_payload is not None

    review_gate["approved"] = True
    resumed = loop.resume_from_review(
        request=CommandRequest("close firefox"),
        planning=make_planning(plan),
        state=LoopState(**stored.snapshot_payload),
        run_id="run_resume",
        goal_id="goal_resume",
    )

    assert resumed.decision == "complete"
    assert resumed.overall_status == "completed"
    assert execution_counts == {"step_1": 1, "step_2": 1}


def test_goal_loop_classifies_same_action_no_progress() -> None:
    plan = make_plan("plan_no_progress", ("step_1",))
    loop = GoalLoop(
        observation_service=FakeObservationService(
            [
                {"observation_id": "obs_pre", "packages": {"browser_context": {"active_url": "https://example.com"}}},
                {"observation_id": "obs_post", "packages": {"browser_context": {"active_url": "https://example.com"}}},
            ]
        ),
        planning_payload=lambda planning: {"plan": asdict(plan)},
        resolve_understanding_transition=lambda planning, trigger: planning,
        apply_replan_transition=lambda planning, decision, failure: planning,
        plan_again=lambda planning, request, excluded_routes, excluded_capabilities, candidate_domains: planning,
        review_step=lambda plan, step: (
            PermissionReview("L1", False, True, "allowed"),
            StepReviewRecord("srev_1", step.id, step.action, "L1", False, True, "allowed"),
        ),
        execute_step=lambda plan, step, request, attempt_id: StepExecutionResult(
            step_id=step.id,
            layer="adapter_execute",
            status="succeeded",
            capability_id=step.capability_id,
            result={"status": "succeeded"},
        ),
        assess_plan_execution=lambda plan, step_results, request, run_id, understanding_id, candidate_set_id, route_decision_id: (_ for _ in ()).throw(AssertionError("final assessment should not run")),
        classify_failure=FailureClassifier().classify,
        decide_replan=lambda utterance, current_plan, attempts, failure, understanding_id, candidate_set_id, available_domain_ids: ReplanDecision(
            action="stop",
            reason=failure.message,
        ),
        persist_review=lambda utterance, plan, loop_state, step, reason: (_ for _ in ()).throw(AssertionError("review path should not run")),
        persist_user_input=lambda utterance, planning, loop_state, reason: (_ for _ in ()).throw(AssertionError("user-input path should not run")),
    )

    result = loop.run(
        request=CommandRequest("open example"),
        planning=make_planning(plan),
        run_id="run_no_progress",
        goal_id="goal_no_progress",
    )

    assert result.decision == "blocked"
    assert result.acceptance_status == "failed"
    assert result.overall_status == "blocked"
    assert result.message == "step executed successfully but the observed state did not change"


def test_agent_runtime_execute_strategy_step_updates_state() -> None:
    registry = ToolRegistry(
        (
            ToolSpec(
                "browser.observe_context",
                "observer",
                "browser",
                lambda payload, context: ToolResult(
                    status="succeeded",
                    output={"observed_url": "https://example.com"},
                    evidence={"active_url": "https://example.com"},
                    state_updates={"observed_url": "https://example.com"},
                ),
            ),
        )
    )
    runtime = AgentRuntime(registry)
    result = runtime.execute_strategy_step(
        session_id="session_1",
        goal_id="goal_1",
        turn_id="turn_1",
        attempt_id="attempt_1",
        strategy_id="strategy_1",
        step=StrategyStep(tool_id="browser.observe_context"),
        environment=EnvironmentProfile(
            platform="linux",
            transport_mode="local",
            daemon_available=False,
            desktop_integration_available=True,
            connectivity_limitations="offline",
            deployment_profile="test",
            region="local",
            search_policy="browser_first",
        ),
        state={},
    )

    assert result.state["observed_url"] == "https://example.com"
    assert result.evidence_entry["kind"] == "observation"


def execute_success_step(step: TaskStep, execution_counts: dict[str, int]) -> StepExecutionResult:
    execution_counts[step.id] += 1
    return StepExecutionResult(
        step_id=step.id,
        layer="adapter_execute",
        status="succeeded",
        capability_id=step.capability_id,
        result={"selected_target": step.id},
    )


def make_plan(plan_id: str, step_ids: tuple[str, ...], *, depends_on: dict[str, tuple[str, ...]] | None = None) -> TaskPlan:
    deps = depends_on or {}
    steps = tuple(
        TaskStep(
            id=step_id,
            action="browser.search_web",
            capability_id="browser.search_web",
            target={"query": step_id},
            depends_on=deps.get(step_id, ()),
        )
        for step_id in step_ids
    )
    return TaskPlan(
        schema_version="v0.5",
        plan_id=plan_id,
        utterance="test",
        display=DisplayFields(goal="test"),
        selected_route_id="browser_search_web_route",
        routes=(TaskRoute(id="browser_search_web_route", score=1.0, domain_id="browser"),),
        steps=steps,
    )


def make_planning(plan: TaskPlan):
    understanding = SimpleNamespace(understanding_id="und_1", primary_understanding_id="und_1")
    candidate_set = SimpleNamespace(candidate_set_id="cset_1")
    route_decision = SimpleNamespace(route_decision_id="rdec_1")
    analysis = SimpleNamespace(type="task", explanation="test analysis", chat_response=None)
    return SimpleNamespace(
        plan=plan,
        understanding=understanding,
        candidate_set=candidate_set,
        route_decision=route_decision,
        analysis=analysis,
    )


def make_review_path(name: str) -> Path:
    return Path(".vibeos") / f"test-{name}-{uuid4().hex}.jsonl"
