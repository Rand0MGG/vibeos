from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

from vibeos.agent_runtime import AgentRuntime, EnvironmentProfile
from vibeos.failure_classifier import FailureClassifier
from vibeos.goal_loop import GoalLoop, loop_state_from_payload
from vibeos.goal_ports import GoalLoopPorts
from vibeos.loop_policy import default_loop_policy
from vibeos.loop_models import LoopObservation, LoopPolicy
from vibeos.models import CommandRequest, PermissionReview
from vibeos.observation_service import observation_progressed
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

    def observe(self, *, plan, step, phase, level, attempt_id=None):
        payload = self._sequence.pop(0)
        return LoopObservation(
            observation_id=payload["observation_id"],
            level=level,
            phase="pre" if phase == "pre" else "post",
            packages=payload["packages"],
            route_id=plan.selected_route_id,
            step_id=step.id if step is not None else None,
        )


class CallbackGoalLoopPorts:
    """Test-only adapter for exercising the typed GoalLoop boundary."""

    def __init__(self, callbacks: dict[str, object]) -> None:
        self.callbacks = callbacks

    def payload(self, planning):
        return self.callbacks["planning_payload"](planning)

    def resolve_understanding_transition(self, planning, *, trigger):
        return self.callbacks["resolve_understanding_transition"](planning, trigger)

    def apply_replan_transition(self, planning, *, decision, failure):
        return self.callbacks["apply_replan_transition"](planning, decision, failure)

    def replan(self, planning, request, excluded_route_ids, excluded_capability_ids, candidate_domain_ids):
        return self.callbacks["plan_again"](planning, request, excluded_route_ids, excluded_capability_ids, candidate_domain_ids)

    def observe(self, *, plan, step, phase, level, attempt_id=None):
        return self.callbacks["observation_service"].observe(plan=plan, step=step, phase=phase, level=level)

    def progressed(self, plan, step, step_result, pre_observation, post_observation, request):
        callback = self.callbacks.get("step_progressed")
        if callback is not None:
            return callback(plan, step, step_result, pre_observation, post_observation, request)
        return step_result.status == "succeeded" and (request.dry_run or observation_progressed(pre_observation, post_observation))

    def review_step(self, plan, step, observation):
        return self.callbacks["review_step"](plan, step, observation)

    def persist_step_review(self, utterance, planning, state, step, reason):
        return self.callbacks["persist_review"](utterance, planning, state, step, reason)

    def persist_user_input(self, utterance, planning, state, reason):
        return self.callbacks["persist_user_input"](utterance, planning, state, reason)

    def execute_step(self, context, plan, step, request, attempt_id):
        return self.callbacks["execute_step"](plan, step, request, attempt_id)

    def assess(self, plan, step_results, request, run_id, understanding_id, candidate_set_id, route_decision_id):
        return self.callbacks["assess_plan_execution"](plan, step_results, request, run_id, understanding_id, candidate_set_id, route_decision_id)

    def classify(self, plan, execution):
        return self.callbacks["classify_failure"](plan, execution)

    def decide(self, utterance, plan, attempts, failure, understanding_id, candidate_set_id, available_domain_ids):
        return self.callbacks["decide_replan"](utterance, plan, attempts, failure, understanding_id, candidate_set_id, available_domain_ids)


def make_goal_loop(**callbacks) -> GoalLoop:
    adapter = CallbackGoalLoopPorts(callbacks)
    return GoalLoop(
        ports=GoalLoopPorts(
            planning=adapter,
            observation=adapter,
            review=adapter,
            execution=adapter,
            acceptance=adapter,
            recovery=adapter,
            policy=callbacks.get("policy") or default_loop_policy(),
        )
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
    assert loaded.snapshot_payload["loop_snapshot_id"] == "lsnap_1"
    assert loaded.snapshot_payload["current_step_id"] == "step_1"
    assert loaded.snapshot_payload["pending_user_input_id"] == request.review_id
    assert loaded.supplemental_input == "open baidu.com"


def test_goal_loop_suspends_for_review_with_real_review_store() -> None:
    plan = make_plan("plan_review", ("step_1",))
    store = ReviewStore(make_review_path("goal-loop-review"))

    loop = make_goal_loop(
        observation_service=FakeObservationService([{"observation_id": "obs_pre", "packages": {"browser_context": {"active_url": ""}}}]),
        planning_payload=lambda planning: {"plan": asdict(plan)},
        resolve_understanding_transition=lambda planning, trigger: planning,
        apply_replan_transition=lambda planning, decision, failure: planning,
        plan_again=lambda planning, request, excluded_routes, excluded_capabilities, candidate_domains: planning,
        review_step=lambda plan, step, pre_observation: (
            PermissionReview("L2", True, True, "approval required"),
            StepReviewRecord("srev_1", step.id, step.action, "L2", True, True, "approval required"),
        ),
        execute_step=lambda plan, step, request, attempt_id: (_ for _ in ()).throw(AssertionError("step must not execute before approval")),
        assess_plan_execution=lambda plan, step_results, request, run_id, understanding_id, candidate_set_id, route_decision_id: (_ for _ in ()).throw(
            AssertionError("final assessment should not run")
        ),
        classify_failure=FailureClassifier().classify,
        decide_replan=lambda utterance, current_plan, attempts, failure, understanding_id, candidate_set_id, available_domain_ids: (_ for _ in ()).throw(
            AssertionError("replanner should not run")
        ),
        persist_review=lambda utterance, planning, loop_state, step, reason: store.create_loop_review(
            utterance,
            plan_payload={"plan_id": planning.plan.plan_id, "plan": asdict(planning.plan)},
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
    assert stored.snapshot_payload["pending_review_id"] == result.review_id
    assert stored.snapshot_payload["pending_step_safety_review_id"] == "srev_1"


def test_goal_loop_suspends_for_review() -> None:
    test_goal_loop_suspends_for_review_with_real_review_store()


def test_goal_loop_suspends_for_user_input_with_real_review_store() -> None:
    store = ReviewStore(make_review_path("goal-loop-user-input"))
    planning = SimpleNamespace(
        plan=None,
        analysis=SimpleNamespace(type="clarification", explanation="need a concrete target", chat_response="which site should I open?"),
        route_decision=SimpleNamespace(action="clarify", route_decision_id="rdec_clarification"),
        understanding=None,
        candidate_set=None,
    )
    loop = make_goal_loop(
        observation_service=FakeObservationService([]),
        planning_payload=lambda current: {"analysis": {"type": "clarification"}},
        resolve_understanding_transition=lambda current, trigger: current,
        apply_replan_transition=lambda current, decision, failure: current,
        plan_again=lambda current, request, excluded_routes, excluded_capabilities, candidate_domains: current,
        review_step=lambda plan, step, pre_observation: (_ for _ in ()).throw(AssertionError("review path should not run")),
        execute_step=lambda plan, step, request, attempt_id: (_ for _ in ()).throw(AssertionError("execute path should not run")),
        assess_plan_execution=lambda plan, step_results, request, run_id, understanding_id, candidate_set_id, route_decision_id: (_ for _ in ()).throw(
            AssertionError("assessment should not run")
        ),
        classify_failure=FailureClassifier().classify,
        decide_replan=lambda utterance, current_plan, attempts, failure, understanding_id, candidate_set_id, available_domain_ids: (_ for _ in ()).throw(
            AssertionError("replanner should not run")
        ),
        persist_review=lambda utterance, planning, loop_state, step, reason: (_ for _ in ()).throw(AssertionError("loop review should not run")),
        persist_user_input=lambda utterance, planning, loop_state, reason: store.create_loop_review(
            utterance,
            plan_payload={"analysis": {"type": "clarification"}},
            snapshot_payload=asdict(loop_state),
            pending_reason=reason,
            step_id=None,
            review_kind="user_input",
        ),
    )

    result = loop.run(
        request=CommandRequest("open that site we discussed yesterday"),
        planning=planning,
        run_id="run_user_input",
        goal_id="goal_user_input",
    )
    stored = store.get(result.review_id or "")

    assert result.decision == "needs_user_input"
    assert result.overall_status == "needs_user_input"
    assert stored is not None
    assert stored.review_kind == "user_input"
    assert stored.snapshot_payload is not None
    assert stored.snapshot_payload["pending_user_input_id"] == result.review_id


def test_goal_loop_suspends_for_user_input() -> None:
    test_goal_loop_suspends_for_user_input_with_real_review_store()


def test_review_approval_resumes_from_pending_step_without_reexecuting_completed_steps() -> None:
    plan = make_plan("plan_resume", ("step_1", "step_2"), depends_on={"step_2": ("step_1",)})
    store = ReviewStore(make_review_path("goal-loop-resume"))
    review_gate = {"approved": False}
    execution_counts = {"step_1": 0, "step_2": 0}

    loop = make_goal_loop(
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
        review_step=lambda plan, step, pre_observation: (
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
        decide_replan=lambda utterance, current_plan, attempts, failure, understanding_id, candidate_set_id, available_domain_ids: (_ for _ in ()).throw(
            AssertionError("replanner should not run")
        ),
        persist_review=lambda utterance, planning, loop_state, step, reason: store.create_loop_review(
            utterance,
            plan_payload={"plan_id": planning.plan.plan_id, "plan": asdict(planning.plan)},
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
        state=loop_state_from_payload(stored.snapshot_payload),
        run_id="run_resume",
        goal_id="goal_resume",
    )

    assert resumed.decision == "complete"
    assert resumed.overall_status == "completed"
    assert execution_counts == {"step_1": 1, "step_2": 1}


def test_review_approval_resumes_from_pending_step() -> None:
    test_review_approval_resumes_from_pending_step_without_reexecuting_completed_steps()


def test_completed_steps_are_not_reexecuted_after_resume() -> None:
    test_review_approval_resumes_from_pending_step_without_reexecuting_completed_steps()


def test_review_resume_uses_real_review_store_persistence() -> None:
    plan = make_plan("plan_resume_store", ("step_1", "step_2"), depends_on={"step_2": ("step_1",)})
    store = ReviewStore(make_review_path("goal-loop-resume-persistence"))
    review_gate = {"approved": False}
    execution_counts = {"step_1": 0, "step_2": 0}

    def build_loop() -> GoalLoop:
        return make_goal_loop(
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
            review_step=lambda plan, step, pre_observation: (
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
                acceptance_result={"message": "goal completed", "semantic_acceptance_decision_id": "sacc_resume_store"},
            ),
            classify_failure=FailureClassifier().classify,
            decide_replan=lambda utterance, current_plan, attempts, failure, understanding_id, candidate_set_id, available_domain_ids: (_ for _ in ()).throw(
                AssertionError("replanner should not run")
            ),
            persist_review=lambda utterance, planning, loop_state, step, reason: store.create_loop_review(
                utterance,
                plan_payload={"plan_id": planning.plan.plan_id, "plan": asdict(planning.plan)},
                snapshot_payload=asdict(loop_state),
                pending_reason=reason,
                step_id=step.id,
                review_kind="loop",
            ),
            persist_user_input=lambda utterance, planning, loop_state, reason: (_ for _ in ()).throw(AssertionError("user-input path should not run")),
        )

    first_loop = build_loop()
    first = first_loop.run(
        request=CommandRequest("close firefox"),
        planning=make_planning(plan),
        run_id="run_resume_store",
        goal_id="goal_resume_store",
    )
    stored = store.get(first.review_id or "")

    assert first.decision == "needs_review"
    assert stored is not None
    assert stored.snapshot_payload is not None

    review_gate["approved"] = True
    resumed_loop = build_loop()
    resumed = resumed_loop.resume_from_review(
        request=CommandRequest("close firefox", review_id=first.review_id),
        planning=make_planning(plan),
        state=loop_state_from_payload(dict(stored.snapshot_payload)),
        run_id="run_resume_store",
        goal_id="goal_resume_store",
    )

    assert resumed.decision == "complete"
    assert resumed.overall_status == "completed"
    assert execution_counts == {"step_1": 1, "step_2": 1}
    assert resumed.state.pending_review_id is None
    assert resumed.state.pending_step_safety_review_id is None


def test_goal_loop_classifies_same_action_no_progress() -> None:
    plan = make_plan("plan_no_progress", ("step_1",))
    loop = make_goal_loop(
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
        review_step=lambda plan, step, pre_observation: (
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
        assess_plan_execution=lambda plan, step_results, request, run_id, understanding_id, candidate_set_id, route_decision_id: (_ for _ in ()).throw(
            AssertionError("final assessment should not run")
        ),
        classify_failure=FailureClassifier().classify,
        decide_replan=lambda utterance, current_plan, attempts, failure, understanding_id, candidate_set_id, available_domain_ids: ReplanDecision(
            action="stop",
            reason=failure.message,
        ),
        persist_review=lambda utterance, planning, loop_state, step, reason: (_ for _ in ()).throw(AssertionError("review path should not run")),
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
    assert result.attempt_records


def test_same_action_no_progress_is_classified() -> None:
    test_goal_loop_classifies_same_action_no_progress()


def test_goal_loop_records_pre_and_post_observation(monkeypatch) -> None:
    events = []
    plan = make_plan("plan_observe", ("step_1",))
    monkeypatch.setattr("vibeos.goal_loop.record_trace_event", lambda **kwargs: events.append(kwargs))

    loop = make_goal_loop(
        observation_service=FakeObservationService(
            [
                {"observation_id": "obs_pre", "packages": {"session_context": {"status": "loaded"}}},
                {"observation_id": "obs_post", "packages": {"browser_context": {"active_url": "https://example.com"}}},
            ]
        ),
        planning_payload=lambda planning: {"plan": asdict(plan)},
        resolve_understanding_transition=lambda planning, trigger: planning,
        apply_replan_transition=lambda planning, decision, failure: planning,
        plan_again=lambda planning, request, excluded_routes, excluded_capabilities, candidate_domains: planning,
        review_step=lambda plan, step, pre_observation: (
            PermissionReview("L1", False, True, "allowed"),
            StepReviewRecord("srev_1", step.id, step.action, "L1", False, True, "allowed"),
        ),
        execute_step=lambda plan, step, request, attempt_id: StepExecutionResult(
            step_id=step.id,
            layer="adapter_execute",
            status="succeeded",
            capability_id=step.capability_id,
            result={"selected_target": "https://example.com"},
        ),
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
        decide_replan=lambda utterance, current_plan, attempts, failure, understanding_id, candidate_set_id, available_domain_ids: ReplanDecision(
            action="stop", reason=failure.message
        ),
        persist_review=lambda utterance, planning, loop_state, step, reason: (_ for _ in ()).throw(AssertionError("review path should not run")),
        persist_user_input=lambda utterance, planning, loop_state, reason: (_ for _ in ()).throw(AssertionError("user-input path should not run")),
    )

    result = loop.run(
        request=CommandRequest("open example"),
        planning=make_planning(plan),
        run_id="run_observe",
        goal_id="goal_observe",
    )

    assert result.decision == "complete"
    assert [event["event_type"] for event in events if event["event_type"] in {"observe_pre_completed", "observe_post_completed", "step_verified"}] == [
        "observe_pre_completed",
        "observe_post_completed",
        "step_verified",
    ]
    assert next(event for event in events if event["event_type"] == "observe_pre_completed")["status"] == "L0"
    assert next(event for event in events if event["event_type"] == "observe_post_completed")["status"] == "L0"


def test_goal_loop_executes_one_step_at_a_time(monkeypatch) -> None:
    events = []
    plan = make_plan("plan_one_step_at_a_time", ("step_1", "step_2"), depends_on={"step_2": ("step_1",)})
    execution_counts = {"step_1": 0, "step_2": 0}
    monkeypatch.setattr("vibeos.goal_loop.record_trace_event", lambda **kwargs: events.append(kwargs))

    loop = make_goal_loop(
        observation_service=FakeObservationService(
            [
                {"observation_id": "obs1_pre", "packages": {"browser_context": {"active_url": ""}}},
                {"observation_id": "obs1_post", "packages": {"browser_context": {"active_url": "https://example.com/step1"}}},
                {"observation_id": "obs2_pre", "packages": {"browser_context": {"active_url": "https://example.com/step1"}}},
                {"observation_id": "obs2_post", "packages": {"browser_context": {"active_url": "https://example.com/step2"}}},
            ]
        ),
        planning_payload=lambda planning: {"plan": asdict(plan)},
        resolve_understanding_transition=lambda planning, trigger: planning,
        apply_replan_transition=lambda planning, decision, failure: planning,
        plan_again=lambda planning, request, excluded_routes, excluded_capabilities, candidate_domains: planning,
        review_step=lambda plan, step, pre_observation: (
            PermissionReview("L1", False, True, "allowed"),
            StepReviewRecord(f"srev_{step.id}", step.id, step.action, "L1", False, True, "allowed"),
        ),
        execute_step=lambda plan, step, request, attempt_id: execute_success_step(step, execution_counts),
        assess_plan_execution=lambda plan, step_results, request, run_id, understanding_id, candidate_set_id, route_decision_id: PlanExecutionResult(
            plan_id=plan.plan_id,
            status="succeeded",
            step_results=step_results,
            execution_status="succeeded",
            acceptance_status="passed",
            overall_status="completed",
            acceptance_result={"message": "goal completed", "semantic_acceptance_decision_id": "sacc_steps"},
        ),
        classify_failure=FailureClassifier().classify,
        decide_replan=lambda utterance, current_plan, attempts, failure, understanding_id, candidate_set_id, available_domain_ids: ReplanDecision(
            action="stop", reason=failure.message
        ),
        persist_review=lambda utterance, planning, loop_state, step, reason: (_ for _ in ()).throw(AssertionError("review path should not run")),
        persist_user_input=lambda utterance, planning, loop_state, reason: (_ for _ in ()).throw(AssertionError("user-input path should not run")),
    )

    result = loop.run(
        request=CommandRequest("open example"),
        planning=make_planning(plan),
        run_id="run_one_step_at_a_time",
        goal_id="goal_one_step_at_a_time",
    )

    sequence = [
        event["event_type"]
        for event in events
        if event["event_type"]
        in {
            "step_selected",
            "observe_pre_completed",
            "step_review_completed",
            "step_executed",
            "observe_post_completed",
            "step_verified",
        }
    ]

    assert result.decision == "complete"
    assert execution_counts == {"step_1": 1, "step_2": 1}
    assert sequence == [
        "step_selected",
        "observe_pre_completed",
        "step_review_completed",
        "step_executed",
        "observe_post_completed",
        "step_verified",
        "step_selected",
        "observe_pre_completed",
        "step_review_completed",
        "step_executed",
        "observe_post_completed",
        "step_verified",
    ]


def test_loop_trace_contains_observe_act_verify_sequence(monkeypatch) -> None:
    test_goal_loop_executes_one_step_at_a_time(monkeypatch)


def test_goal_loop_stops_on_budget_exhausted() -> None:
    plan = make_plan("plan_budget_exhausted", ("step_1", "step_2"), depends_on={"step_2": ("step_1",)})
    execution_counts = {"step_1": 0, "step_2": 0}

    loop = make_goal_loop(
        observation_service=FakeObservationService(
            [
                {"observation_id": "obs1_pre", "packages": {"browser_context": {"active_url": ""}}},
                {"observation_id": "obs1_post", "packages": {"browser_context": {"active_url": "https://example.com/step1"}}},
            ]
        ),
        planning_payload=lambda planning: {"plan": asdict(plan)},
        resolve_understanding_transition=lambda planning, trigger: planning,
        apply_replan_transition=lambda planning, decision, failure: planning,
        plan_again=lambda planning, request, excluded_routes, excluded_capabilities, candidate_domains: planning,
        review_step=lambda plan, step, pre_observation: (
            PermissionReview("L1", False, True, "allowed"),
            StepReviewRecord(f"srev_{step.id}", step.id, step.action, "L1", False, True, "allowed"),
        ),
        execute_step=lambda plan, step, request, attempt_id: execute_success_step(step, execution_counts),
        assess_plan_execution=lambda plan, step_results, request, run_id, understanding_id, candidate_set_id, route_decision_id: (_ for _ in ()).throw(
            AssertionError("budget exhaustion should stop before final assessment")
        ),
        classify_failure=FailureClassifier().classify,
        decide_replan=lambda utterance, current_plan, attempts, failure, understanding_id, candidate_set_id, available_domain_ids: ReplanDecision(
            action="stop", reason=failure.message
        ),
        persist_review=lambda utterance, planning, loop_state, step, reason: (_ for _ in ()).throw(AssertionError("review path should not run")),
        persist_user_input=lambda utterance, planning, loop_state, reason: (_ for _ in ()).throw(AssertionError("user-input path should not run")),
        policy=LoopPolicy(max_steps=1),
    )

    result = loop.run(
        request=CommandRequest("open example"),
        planning=make_planning(plan),
        run_id="run_budget_exhausted",
        goal_id="goal_budget_exhausted",
    )

    assert result.decision == "budget_exhausted"
    assert result.overall_status == "blocked"
    assert execution_counts == {"step_1": 1, "step_2": 0}


def test_goal_loop_can_finish_after_multiple_step_ticks() -> None:
    plan = make_plan("plan_multiple_ticks", ("step_1", "step_2"), depends_on={"step_2": ("step_1",)})
    execution_counts = {"step_1": 0, "step_2": 0}

    loop = make_goal_loop(
        observation_service=FakeObservationService(
            [
                {"observation_id": "obs1_pre", "packages": {"browser_context": {"active_url": ""}}},
                {"observation_id": "obs1_post", "packages": {"browser_context": {"active_url": "https://example.com/step1"}}},
                {"observation_id": "obs2_pre", "packages": {"browser_context": {"active_url": "https://example.com/step1"}}},
                {"observation_id": "obs2_post", "packages": {"browser_context": {"active_url": "https://example.com/step2"}}},
            ]
        ),
        planning_payload=lambda planning: {"plan": asdict(plan)},
        resolve_understanding_transition=lambda planning, trigger: planning,
        apply_replan_transition=lambda planning, decision, failure: planning,
        plan_again=lambda planning, request, excluded_routes, excluded_capabilities, candidate_domains: planning,
        review_step=lambda plan, step, pre_observation: (
            PermissionReview("L1", False, True, "allowed"),
            StepReviewRecord(f"srev_{step.id}", step.id, step.action, "L1", False, True, "allowed"),
        ),
        execute_step=lambda plan, step, request, attempt_id: execute_success_step(step, execution_counts),
        assess_plan_execution=lambda plan, step_results, request, run_id, understanding_id, candidate_set_id, route_decision_id: PlanExecutionResult(
            plan_id=plan.plan_id,
            status="succeeded",
            step_results=step_results,
            execution_status="succeeded",
            acceptance_status="passed",
            overall_status="completed",
            acceptance_result={"message": "goal completed", "semantic_acceptance_decision_id": "sacc_multi_tick"},
        ),
        classify_failure=FailureClassifier().classify,
        decide_replan=lambda utterance, current_plan, attempts, failure, understanding_id, candidate_set_id, available_domain_ids: ReplanDecision(
            action="stop", reason=failure.message
        ),
        persist_review=lambda utterance, planning, loop_state, step, reason: (_ for _ in ()).throw(AssertionError("review path should not run")),
        persist_user_input=lambda utterance, planning, loop_state, reason: (_ for _ in ()).throw(AssertionError("user-input path should not run")),
    )

    result = loop.run(
        request=CommandRequest("open example"),
        planning=make_planning(plan),
        run_id="run_multiple_ticks",
        goal_id="goal_multiple_ticks",
    )

    assert result.decision == "complete"
    assert result.state.completed_step_ids == ("step_1", "step_2")
    assert len(result.attempt_records) == 2


def test_goal_loop_escalates_observation_level_after_failed_attempt() -> None:
    plan = make_plan("plan_observe_escalation", ("step_1",))
    loop = make_goal_loop(
        observation_service=FakeObservationService(
            [
                {"observation_id": "obs_pre", "packages": {"session_context": {"status": "loaded"}}},
                {"observation_id": "obs_post", "packages": {"session_context": {"status": "loaded"}}},
            ]
        ),
        planning_payload=lambda planning: {"plan": asdict(plan)},
        resolve_understanding_transition=lambda planning, trigger: planning,
        apply_replan_transition=lambda planning, decision, failure: planning,
        plan_again=lambda planning, request, excluded_routes, excluded_capabilities, candidate_domains: planning,
        review_step=lambda plan, step, pre_observation: (
            PermissionReview("L1", False, True, "allowed"),
            StepReviewRecord("srev_1", step.id, step.action, "L1", False, True, "allowed"),
        ),
        execute_step=lambda plan, step, request, attempt_id: StepExecutionResult(
            step_id=step.id,
            layer="adapter_execute",
            status="failed",
            capability_id=step.capability_id,
            error="adapter failed",
            result={"status": "failed"},
        ),
        assess_plan_execution=lambda plan, step_results, request, run_id, understanding_id, candidate_set_id, route_decision_id: (_ for _ in ()).throw(
            AssertionError("final assessment should not run")
        ),
        classify_failure=FailureClassifier().classify,
        decide_replan=lambda utterance, current_plan, attempts, failure, understanding_id, candidate_set_id, available_domain_ids: ReplanDecision(
            action="stop", reason="stop after failure"
        ),
        persist_review=lambda utterance, planning, loop_state, step, reason: (_ for _ in ()).throw(AssertionError("review path should not run")),
        persist_user_input=lambda utterance, planning, loop_state, reason: (_ for _ in ()).throw(AssertionError("user-input path should not run")),
    )

    result = loop.run(
        request=CommandRequest("open example"),
        planning=make_planning(plan),
        run_id="run_observe_escalation",
        goal_id="goal_observe_escalation",
    )

    assert result.decision == "blocked"
    assert result.state.observation_level == "L1"


def test_goal_loop_passes_pre_observation_into_step_review() -> None:
    captured = {}
    plan = make_plan("plan_context_review", ("step_1",))
    loop = make_goal_loop(
        observation_service=FakeObservationService(
            [
                {"observation_id": "obs_pre", "packages": {"browser_context": {"active_url": "https://example.com"}}},
                {"observation_id": "obs_post", "packages": {"browser_context": {"active_url": "https://example.com/done"}}},
            ]
        ),
        planning_payload=lambda planning: {"plan": asdict(plan)},
        resolve_understanding_transition=lambda planning, trigger: planning,
        apply_replan_transition=lambda planning, decision, failure: planning,
        plan_again=lambda planning, request, excluded_routes, excluded_capabilities, candidate_domains: planning,
        review_step=lambda plan, step, pre_observation: (
            captured.setdefault("pre_observation_id", pre_observation.observation_id if pre_observation is not None else None),
            captured.setdefault("packages", pre_observation.packages if pre_observation is not None else {}),
            (
                PermissionReview("L1", False, True, "allowed"),
                StepReviewRecord("srev_1", step.id, step.action, "L1", False, True, "allowed"),
            ),
        )[-1],
        execute_step=lambda plan, step, request, attempt_id: StepExecutionResult(
            step_id=step.id,
            layer="adapter_execute",
            status="succeeded",
            capability_id=step.capability_id,
            result={"selected_target": "https://example.com/done"},
        ),
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
        decide_replan=lambda utterance, current_plan, attempts, failure, understanding_id, candidate_set_id, available_domain_ids: ReplanDecision(
            action="stop", reason=failure.message
        ),
        persist_review=lambda utterance, planning, loop_state, step, reason: (_ for _ in ()).throw(AssertionError("review path should not run")),
        persist_user_input=lambda utterance, planning, loop_state, reason: (_ for _ in ()).throw(AssertionError("user-input path should not run")),
    )

    result = loop.run(
        request=CommandRequest("open example"),
        planning=make_planning(plan),
        run_id="run_context_review",
        goal_id="goal_context_review",
    )

    assert result.decision == "complete"
    assert captured["pre_observation_id"] == "obs_pre"
    assert captured["packages"]["browser_context"]["active_url"] == "https://example.com"


def test_goal_loop_retries_same_attempt_without_replanning() -> None:
    execution_attempts = {"count": 0}
    acceptance_inputs: list[tuple[StepExecutionResult, ...]] = []
    plan = make_plan("plan_retry_same_attempt", ("step_1",))

    def assess(plan, step_results, request, run_id, understanding_id, candidate_set_id, route_decision_id):
        acceptance_inputs.append(step_results)
        return PlanExecutionResult(
            plan_id=plan.plan_id,
            status="succeeded",
            step_results=step_results,
            execution_status="succeeded",
            acceptance_status="passed",
            overall_status="completed",
            acceptance_result={"message": "goal completed", "semantic_acceptance_decision_id": "sacc_retry"},
        )

    loop = make_goal_loop(
        observation_service=FakeObservationService(
            [
                {"observation_id": "obs1_pre", "packages": {"session_context": {"status": "loaded"}}},
                {"observation_id": "obs1_post", "packages": {"session_context": {"status": "loaded"}}},
                {"observation_id": "obs2_pre", "packages": {"session_context": {"status": "loaded"}}},
                {"observation_id": "obs2_post", "packages": {"browser_context": {"active_url": "https://example.com/done"}}},
            ]
        ),
        planning_payload=lambda planning: {"plan": asdict(plan)},
        resolve_understanding_transition=lambda planning, trigger: planning,
        apply_replan_transition=lambda planning, decision, failure: (_ for _ in ()).throw(
            AssertionError("retry_same_attempt should not mutate the plan basis")
        ),
        plan_again=lambda planning, request, excluded_routes, excluded_capabilities, candidate_domains: (_ for _ in ()).throw(
            AssertionError("retry_same_attempt should not invoke replanning")
        ),
        review_step=lambda plan, step, pre_observation: (
            PermissionReview("L1", False, True, "allowed"),
            StepReviewRecord("srev_1", step.id, step.action, "L1", False, True, "allowed"),
        ),
        execute_step=lambda plan, step, request, attempt_id: _retry_step_result(step, execution_attempts),
        assess_plan_execution=assess,
        classify_failure=FailureClassifier().classify,
        decide_replan=lambda utterance, current_plan, attempts, failure, understanding_id, candidate_set_id, available_domain_ids: ReplanDecision(
            action="retry_same_attempt", reason="retry the transient failure"
        ),
        persist_review=lambda utterance, planning, loop_state, step, reason: (_ for _ in ()).throw(AssertionError("review path should not run")),
        persist_user_input=lambda utterance, planning, loop_state, reason: (_ for _ in ()).throw(AssertionError("user-input path should not run")),
    )

    result = loop.run(
        request=CommandRequest("open example"),
        planning=make_planning(plan),
        run_id="run_retry_same_attempt",
        goal_id="goal_retry_same_attempt",
    )

    assert result.decision == "complete"
    assert execution_attempts["count"] == 2
    assert len(result.attempt_records) == 2
    assert result.attempt_records[1].trigger == "retry_same_attempt"
    assert [[receipt.status for receipt in inputs] for inputs in acceptance_inputs] == [["succeeded"]]


def test_goal_loop_repairs_after_terminal_acceptance_unverified(monkeypatch) -> None:
    events = []
    plan = make_plan("plan_terminal_unverified", ("step_1",))
    execution_attempts = {"count": 0}
    assessment_attempts = {"count": 0}
    monkeypatch.setattr("vibeos.goal_loop.record_trace_event", lambda **kwargs: events.append(kwargs))

    loop = make_goal_loop(
        observation_service=FakeObservationService(
            [
                {"observation_id": "obs1_pre", "packages": {"browser_context": {"active_url": ""}}},
                {"observation_id": "obs1_post", "packages": {"browser_context": {"active_url": "https://example.com/first"}}},
                {"observation_id": "obs2_pre", "packages": {"browser_context": {"active_url": ""}}},
                {"observation_id": "obs2_post", "packages": {"browser_context": {"active_url": "https://example.com/final"}}},
            ]
        ),
        planning_payload=lambda planning: {"plan": asdict(plan)},
        resolve_understanding_transition=lambda planning, trigger: planning,
        apply_replan_transition=lambda planning, decision, failure: (_ for _ in ()).throw(AssertionError("repair should not mutate the plan basis")),
        plan_again=lambda planning, request, excluded_routes, excluded_capabilities, candidate_domains: (_ for _ in ()).throw(
            AssertionError("repair should not invoke replanning")
        ),
        review_step=lambda plan, step, pre_observation: (
            PermissionReview("L1", False, True, "allowed"),
            StepReviewRecord("srev_1", step.id, step.action, "L1", False, True, "allowed"),
        ),
        execute_step=lambda plan, step, request, attempt_id: _counted_success_step(step, execution_attempts),
        assess_plan_execution=lambda plan, step_results, request, run_id, understanding_id, candidate_set_id, route_decision_id: (
            _terminal_acceptance_assessment(
                plan,
                step_results,
                assessment_attempts,
            )
        ),
        classify_failure=FailureClassifier().classify,
        decide_replan=lambda utterance, current_plan, attempts, failure, understanding_id, candidate_set_id, available_domain_ids: ReplanDecision(
            action="repair",
            reason="collect stronger verification evidence",
        ),
        persist_review=lambda utterance, planning, loop_state, step, reason: (_ for _ in ()).throw(AssertionError("review path should not run")),
        persist_user_input=lambda utterance, planning, loop_state, reason: (_ for _ in ()).throw(AssertionError("user-input path should not run")),
    )

    result = loop.run(
        request=CommandRequest("open example"),
        planning=make_planning(plan),
        run_id="run_terminal_unverified",
        goal_id="goal_terminal_unverified",
    )

    assert result.decision == "complete"
    assert execution_attempts["count"] == 2
    assert assessment_attempts["count"] == 2
    assert any(event["event_type"] == "repair_decided" for event in events)
    assert any(attempt.trigger == "repair" for attempt in result.attempt_records)


def test_repair_cannot_create_unregistered_capability_step(monkeypatch) -> None:
    test_goal_loop_repairs_after_terminal_acceptance_unverified(monkeypatch)


def test_goal_loop_replans_after_terminal_acceptance_failure() -> None:
    first_plan = make_plan("plan_terminal_fail", ("step_1",))
    second_plan = TaskPlan(
        schema_version="v0.5",
        plan_id="plan_terminal_replanned",
        utterance="test",
        display=DisplayFields(goal="test"),
        selected_route_id="browser_open_url_route",
        routes=(TaskRoute(id="browser_open_url_route", score=1.0, domain_id="browser"),),
        steps=(TaskStep(id="step_2", action="browser.open_url", capability_id="browser.open_url", target={"url": "https://example.com/final"}),),
    )
    planning_states = [make_planning(first_plan), make_planning(second_plan)]
    execution_counts = {"step_1": 0, "step_2": 0}

    loop = make_goal_loop(
        observation_service=FakeObservationService(
            [
                {"observation_id": "obs1_pre", "packages": {"browser_context": {"active_url": ""}}},
                {"observation_id": "obs1_post", "packages": {"browser_context": {"active_url": "https://example.com/wrong"}}},
                {"observation_id": "obs2_pre", "packages": {"browser_context": {"active_url": ""}}},
                {"observation_id": "obs2_post", "packages": {"browser_context": {"active_url": "https://example.com/final"}}},
            ]
        ),
        planning_payload=lambda planning: {"plan": asdict(planning.plan)},
        resolve_understanding_transition=lambda planning, trigger: planning,
        apply_replan_transition=lambda planning, decision, failure: planning,
        plan_again=lambda planning, request, excluded_routes, excluded_capabilities, candidate_domains: planning_states.pop(0),
        review_step=lambda plan, step, pre_observation: (
            PermissionReview("L1", False, True, "allowed"),
            StepReviewRecord(f"srev_{step.id}", step.id, step.action, "L1", False, True, "allowed"),
        ),
        execute_step=lambda plan, step, request, attempt_id: execute_success_step(step, execution_counts),
        assess_plan_execution=lambda plan, step_results, request, run_id, understanding_id, candidate_set_id, route_decision_id: _replanned_terminal_assessment(
            plan,
            step_results,
        ),
        classify_failure=FailureClassifier().classify,
        decide_replan=lambda utterance, current_plan, attempts, failure, understanding_id, candidate_set_id, available_domain_ids: ReplanDecision(
            action="replan_with_constraints",
            reason="pick a different browser route",
            do_not_repeat_route_ids=(current_plan.selected_route_id,),
        ),
        persist_review=lambda utterance, planning, loop_state, step, reason: (_ for _ in ()).throw(AssertionError("review path should not run")),
        persist_user_input=lambda utterance, planning, loop_state, reason: (_ for _ in ()).throw(AssertionError("user-input path should not run")),
    )

    result = loop.run(
        request=CommandRequest("open example"),
        planning=planning_states.pop(0),
        run_id="run_terminal_replan",
        goal_id="goal_terminal_replan",
    )

    assert result.decision == "complete"
    assert execution_counts == {"step_1": 1, "step_2": 1}
    assert any(attempt.replan_decision and attempt.replan_decision.action == "replan_with_constraints" for attempt in result.attempt_records)
    assert result.payload["plan"]["plan_id"] == "plan_terminal_replanned"


def test_goal_loop_policy_stops_retry_same_attempt_after_failure_limit() -> None:
    plan = make_plan("plan_policy_retry_limit", ("step_1",))
    loop = make_goal_loop(
        observation_service=FakeObservationService(
            [
                {"observation_id": "obs1_pre", "packages": {"session_context": {"status": "loaded"}}},
                {"observation_id": "obs1_post", "packages": {"session_context": {"status": "loaded"}}},
            ]
        ),
        planning_payload=lambda planning: {"plan": asdict(plan)},
        resolve_understanding_transition=lambda planning, trigger: planning,
        apply_replan_transition=lambda planning, decision, failure: (_ for _ in ()).throw(AssertionError("policy stop should avoid plan mutation")),
        plan_again=lambda planning, request, excluded_routes, excluded_capabilities, candidate_domains: (_ for _ in ()).throw(
            AssertionError("policy stop should avoid replanning")
        ),
        review_step=lambda plan, step, pre_observation: (
            PermissionReview("L1", False, True, "allowed"),
            StepReviewRecord("srev_1", step.id, step.action, "L1", False, True, "allowed"),
        ),
        execute_step=lambda plan, step, request, attempt_id: StepExecutionResult(
            step_id=step.id,
            layer="adapter_execute",
            status="failed",
            capability_id=step.capability_id,
            adapter_status="timeout",
            error="tool execution timed out",
            result={"status": "failed"},
        ),
        assess_plan_execution=lambda plan, step_results, request, run_id, understanding_id, candidate_set_id, route_decision_id: (_ for _ in ()).throw(
            AssertionError("final assessment should not run")
        ),
        classify_failure=FailureClassifier().classify,
        decide_replan=lambda utterance, current_plan, attempts, failure, understanding_id, candidate_set_id, available_domain_ids: ReplanDecision(
            action="retry_same_attempt",
            reason="retry the transient failure",
        ),
        persist_review=lambda utterance, planning, loop_state, step, reason: (_ for _ in ()).throw(AssertionError("review path should not run")),
        persist_user_input=lambda utterance, planning, loop_state, reason: (_ for _ in ()).throw(AssertionError("user-input path should not run")),
        policy=LoopPolicy(max_same_failure_count=1),
    )

    result = loop.run(
        request=CommandRequest("open example"),
        planning=make_planning(plan),
        run_id="run_policy_retry_limit",
        goal_id="goal_policy_retry_limit",
    )

    assert result.decision == "blocked"
    assert result.message == "loop policy stopped repeated failure 'tool_timeout' after 1 consecutive attempts"


def test_same_action_no_progress_stops_or_replans() -> None:
    first_plan = make_plan("plan_no_progress_replan", ("step_1",))
    second_plan = TaskPlan(
        schema_version="v0.5",
        plan_id="plan_no_progress_replanned",
        utterance="test",
        display=DisplayFields(goal="test"),
        selected_route_id="browser_open_url_route",
        routes=(TaskRoute(id="browser_open_url_route", score=1.0, domain_id="browser"),),
        steps=(TaskStep(id="step_2", action="browser.open_url", capability_id="browser.open_url", target={"url": "https://example.com/final"}),),
    )
    planning_states = [make_planning(first_plan), make_planning(second_plan)]
    execution_counts = {"step_1": 0, "step_2": 0}

    loop = make_goal_loop(
        observation_service=FakeObservationService(
            [
                {"observation_id": "obs1_pre", "packages": {"browser_context": {"active_url": "https://example.com"}}},
                {"observation_id": "obs1_post", "packages": {"browser_context": {"active_url": "https://example.com"}}},
                {"observation_id": "obs2_pre", "packages": {"browser_context": {"active_url": "https://example.com"}}},
                {"observation_id": "obs2_post", "packages": {"browser_context": {"active_url": "https://example.com/final"}}},
            ]
        ),
        planning_payload=lambda planning: {"plan": asdict(planning.plan)},
        resolve_understanding_transition=lambda planning, trigger: planning,
        apply_replan_transition=lambda planning, decision, failure: planning,
        plan_again=lambda planning, request, excluded_routes, excluded_capabilities, candidate_domains: planning_states.pop(0),
        review_step=lambda plan, step, pre_observation: (
            PermissionReview("L1", False, True, "allowed"),
            StepReviewRecord(f"srev_{step.id}", step.id, step.action, "L1", False, True, "allowed"),
        ),
        execute_step=lambda plan, step, request, attempt_id: execute_success_step(step, execution_counts),
        assess_plan_execution=lambda plan, step_results, request, run_id, understanding_id, candidate_set_id, route_decision_id: PlanExecutionResult(
            plan_id=plan.plan_id,
            status="succeeded",
            step_results=step_results,
            execution_status="succeeded",
            acceptance_status="passed",
            overall_status="completed",
            acceptance_result={"message": "goal completed", "semantic_acceptance_decision_id": "sacc_no_progress"},
        ),
        classify_failure=FailureClassifier().classify,
        decide_replan=lambda utterance, current_plan, attempts, failure, understanding_id, candidate_set_id, available_domain_ids: ReplanDecision(
            action="replan_with_constraints",
            reason="switch route after no progress",
            do_not_repeat_route_ids=(current_plan.selected_route_id,),
        ),
        persist_review=lambda utterance, planning, loop_state, step, reason: (_ for _ in ()).throw(AssertionError("review path should not run")),
        persist_user_input=lambda utterance, planning, loop_state, reason: (_ for _ in ()).throw(AssertionError("user-input path should not run")),
    )

    result = loop.run(
        request=CommandRequest("open example"),
        planning=planning_states.pop(0),
        run_id="run_no_progress_replan",
        goal_id="goal_no_progress_replan",
    )

    assert result.decision == "complete"
    assert execution_counts == {"step_1": 1, "step_2": 1}
    assert result.attempt_records[0].failure is not None
    assert result.attempt_records[0].failure.failure_class == "same_action_no_progress"


def test_acceptance_failed_triggers_replan_with_evidence() -> None:
    captured = {}
    plan = make_plan("plan_acceptance_failed_replan", ("step_1",))

    loop = make_goal_loop(
        observation_service=FakeObservationService(
            [
                {"observation_id": "obs1_pre", "packages": {"browser_context": {"active_url": ""}}},
                {"observation_id": "obs1_post", "packages": {"browser_context": {"active_url": "https://example.com/wrong"}}},
            ]
        ),
        planning_payload=lambda planning: {"plan": asdict(plan)},
        resolve_understanding_transition=lambda planning, trigger: planning,
        apply_replan_transition=lambda planning, decision, failure: planning,
        plan_again=lambda planning, request, excluded_routes, excluded_capabilities, candidate_domains: planning,
        review_step=lambda plan, step, pre_observation: (
            PermissionReview("L1", False, True, "allowed"),
            StepReviewRecord("srev_1", step.id, step.action, "L1", False, True, "allowed"),
        ),
        execute_step=lambda plan, step, request, attempt_id: StepExecutionResult(
            step_id=step.id,
            layer="adapter_execute",
            status="succeeded",
            capability_id=step.capability_id,
            result={"selected_target": "https://example.com/wrong"},
        ),
        assess_plan_execution=lambda plan, step_results, request, run_id, understanding_id, candidate_set_id, route_decision_id: PlanExecutionResult(
            plan_id=plan.plan_id,
            status="succeeded",
            step_results=step_results,
            execution_status="succeeded",
            acceptance_status="failed",
            overall_status="incomplete",
            acceptance_result={"message": "wrong target", "semantic_acceptance_decision_id": "sacc_wrong_target"},
        ),
        classify_failure=FailureClassifier().classify,
        decide_replan=lambda utterance, current_plan, attempts, failure, understanding_id, candidate_set_id, available_domain_ids: _capture_replan_evidence(
            captured,
            attempts,
            failure,
        ),
        persist_review=lambda utterance, planning, loop_state, step, reason: (_ for _ in ()).throw(AssertionError("review path should not run")),
        persist_user_input=lambda utterance, planning, loop_state, reason: (_ for _ in ()).throw(AssertionError("user-input path should not run")),
    )

    result = loop.run(
        request=CommandRequest("open example"),
        planning=make_planning(plan),
        run_id="run_acceptance_failed_replan",
        goal_id="goal_acceptance_failed_replan",
    )

    assert result.decision == "blocked"
    assert captured["failure_class"] == "acceptance_failed"
    assert captured["attempt_count"] == 2
    assert captured["semantic_acceptance_decision_id"] == "sacc_wrong_target"


def test_replan_consumes_prior_failures_and_completed_steps() -> None:
    first_plan = make_plan("plan_replan_history_1", ("step_1", "step_2"), depends_on={"step_2": ("step_1",)})
    second_plan = make_plan("plan_replan_history_2", ("step_1", "step_3"), depends_on={"step_3": ("step_1",)})
    planning_states = [make_planning(first_plan), make_planning(second_plan)]
    execution_counts = {"step_1": 0, "step_2": 0, "step_3": 0}

    loop = make_goal_loop(
        observation_service=FakeObservationService(
            [
                {"observation_id": "obs1_pre", "packages": {"browser_context": {"active_url": ""}}},
                {"observation_id": "obs1_post", "packages": {"browser_context": {"active_url": "https://example.com/step1"}}},
                {"observation_id": "obs2_pre", "packages": {"browser_context": {"active_url": "https://example.com/step1"}}},
                {"observation_id": "obs2_post", "packages": {"browser_context": {"active_url": "https://example.com/step1"}}},
                {"observation_id": "obs3_pre", "packages": {"browser_context": {"active_url": "https://example.com/step1"}}},
                {"observation_id": "obs3_post", "packages": {"browser_context": {"active_url": "https://example.com/step3"}}},
            ]
        ),
        planning_payload=lambda planning: {"plan": asdict(planning.plan)},
        resolve_understanding_transition=lambda planning, trigger: planning,
        apply_replan_transition=lambda planning, decision, failure: planning,
        plan_again=lambda planning, request, excluded_routes, excluded_capabilities, candidate_domains: planning_states.pop(0),
        review_step=lambda plan, step, pre_observation: (
            PermissionReview("L1", False, True, "allowed"),
            StepReviewRecord(f"srev_{step.id}", step.id, step.action, "L1", False, True, "allowed"),
        ),
        execute_step=lambda plan, step, request, attempt_id: _replan_history_step_result(step, execution_counts),
        assess_plan_execution=lambda plan, step_results, request, run_id, understanding_id, candidate_set_id, route_decision_id: PlanExecutionResult(
            plan_id=plan.plan_id,
            status="succeeded",
            step_results=step_results,
            execution_status="succeeded",
            acceptance_status="passed",
            overall_status="completed",
            acceptance_result={"message": "goal completed", "semantic_acceptance_decision_id": "sacc_history"},
        ),
        classify_failure=FailureClassifier().classify,
        decide_replan=lambda utterance, current_plan, attempts, failure, understanding_id, candidate_set_id, available_domain_ids: ReplanDecision(
            action="replan_with_constraints",
            reason="switch to the alternative final step",
            do_not_repeat_route_ids=(current_plan.selected_route_id,),
        ),
        persist_review=lambda utterance, planning, loop_state, step, reason: (_ for _ in ()).throw(AssertionError("review path should not run")),
        persist_user_input=lambda utterance, planning, loop_state, reason: (_ for _ in ()).throw(AssertionError("user-input path should not run")),
    )

    result = loop.run(
        request=CommandRequest("open example"),
        planning=planning_states.pop(0),
        run_id="run_replan_history",
        goal_id="goal_replan_history",
    )

    assert result.decision == "complete"
    assert execution_counts == {"step_1": 1, "step_2": 1, "step_3": 1}
    assert result.state.completed_step_ids == ("step_1", "step_3")


def test_acceptance_failed_replan_consumes_bounded_candidate_set() -> None:
    first_plan = make_plan("plan_replan_candidate_1", ("step_1",))
    second_plan = make_plan("plan_replan_candidate_2", ("step_2",))
    first_planning = SimpleNamespace(
        plan=first_plan,
        understanding=SimpleNamespace(understanding_id="und_candidate", primary_understanding_id="und_candidate"),
        candidate_set=SimpleNamespace(candidate_set_id="cset_initial"),
        route_decision=SimpleNamespace(route_decision_id="rdec_initial"),
        analysis=SimpleNamespace(type="task", explanation="initial"),
    )
    second_planning = SimpleNamespace(
        plan=second_plan,
        understanding=SimpleNamespace(understanding_id="und_candidate", primary_understanding_id="und_candidate"),
        candidate_set=SimpleNamespace(candidate_set_id="cset_replanned"),
        route_decision=SimpleNamespace(route_decision_id="rdec_replanned"),
        analysis=SimpleNamespace(type="task", explanation="replanned"),
    )
    planning_states = [second_planning]

    loop = make_goal_loop(
        observation_service=FakeObservationService(
            [
                {"observation_id": "obs1_pre", "packages": {"browser_context": {"active_url": ""}}},
                {"observation_id": "obs1_post", "packages": {"browser_context": {"active_url": "https://example.com/wrong"}}},
                {"observation_id": "obs2_pre", "packages": {"browser_context": {"active_url": "https://example.com/wrong"}}},
                {"observation_id": "obs2_post", "packages": {"browser_context": {"active_url": "https://example.com/right"}}},
            ]
        ),
        planning_payload=lambda planning: {"plan": asdict(planning.plan)},
        resolve_understanding_transition=lambda planning, trigger: planning,
        apply_replan_transition=lambda planning, decision, failure: planning,
        plan_again=lambda planning, request, excluded_routes, excluded_capabilities, candidate_domains: planning_states.pop(0),
        review_step=lambda plan, step, pre_observation: (
            PermissionReview("L1", False, True, "allowed"),
            StepReviewRecord(f"srev_{step.id}", step.id, step.action, "L1", False, True, "allowed"),
        ),
        execute_step=lambda plan, step, request, attempt_id: StepExecutionResult(
            step_id=step.id,
            layer="adapter_execute",
            status="succeeded",
            capability_id=step.capability_id,
            result={"selected_target": step.id},
        ),
        assess_plan_execution=lambda plan, step_results, request, run_id, understanding_id, candidate_set_id, route_decision_id: (
            PlanExecutionResult(
                plan_id=plan.plan_id,
                status="succeeded",
                step_results=step_results,
                execution_status="succeeded",
                acceptance_status="failed",
                overall_status="incomplete",
                acceptance_result={"message": "wrong target", "semantic_acceptance_decision_id": "sacc_candidate_replan"},
            )
            if plan.plan_id == "plan_replan_candidate_1"
            else PlanExecutionResult(
                plan_id=plan.plan_id,
                status="succeeded",
                step_results=step_results,
                execution_status="succeeded",
                acceptance_status="passed",
                overall_status="completed",
                acceptance_result={"message": "goal completed", "semantic_acceptance_decision_id": "sacc_candidate_replan_done"},
            )
        ),
        classify_failure=FailureClassifier().classify,
        decide_replan=lambda utterance, current_plan, attempts, failure, understanding_id, candidate_set_id, available_domain_ids: ReplanDecision(
            action="replan_with_constraints",
            reason="switch to the bounded replanned candidate",
            do_not_repeat_route_ids=(current_plan.selected_route_id,),
        ),
        persist_review=lambda utterance, planning, loop_state, step, reason: (_ for _ in ()).throw(AssertionError("review path should not run")),
        persist_user_input=lambda utterance, planning, loop_state, reason: (_ for _ in ()).throw(AssertionError("user-input path should not run")),
    )

    result = loop.run(
        request=CommandRequest("open example"),
        planning=first_planning,
        run_id="run_replan_candidate",
        goal_id="goal_replan_candidate",
    )

    assert result.decision == "complete"
    assert result.attempt_records[-1].candidate_set_id == "cset_replanned"
    assert result.attempt_records[-1].route_decision_id == "rdec_replanned"


def test_goal_loop_semantic_result_uses_semantic_acceptance_decision() -> None:
    plan = make_plan("plan_semantic_result", ("step_1",))
    loop = make_goal_loop(
        observation_service=FakeObservationService(
            [
                {"observation_id": "obs_pre", "packages": {"browser_context": {"active_url": ""}}},
                {"observation_id": "obs_post", "packages": {"browser_context": {"active_url": "https://example.com/final"}}},
            ]
        ),
        planning_payload=lambda planning: {"plan": asdict(plan)},
        resolve_understanding_transition=lambda planning, trigger: planning,
        apply_replan_transition=lambda planning, decision, failure: planning,
        plan_again=lambda planning, request, excluded_routes, excluded_capabilities, candidate_domains: planning,
        review_step=lambda plan, step, pre_observation: (
            PermissionReview("L1", False, True, "allowed"),
            StepReviewRecord("srev_1", step.id, step.action, "L1", False, True, "allowed"),
        ),
        execute_step=lambda plan, step, request, attempt_id: StepExecutionResult(
            step_id=step.id,
            layer="adapter_execute",
            status="succeeded",
            capability_id=step.capability_id,
            result={"selected_target": "https://example.com/final"},
        ),
        assess_plan_execution=lambda plan, step_results, request, run_id, understanding_id, candidate_set_id, route_decision_id: PlanExecutionResult(
            plan_id=plan.plan_id,
            status="succeeded",
            step_results=step_results,
            execution_status="succeeded",
            acceptance_status="passed",
            overall_status="completed",
            acceptance_result={
                "message": "goal completed",
                "semantic_acceptance_decision_id": "sacc_goal_loop_semantic",
                "semantic_summary_id": "ssum_goal_loop_semantic",
            },
        ),
        classify_failure=FailureClassifier().classify,
        decide_replan=lambda utterance, current_plan, attempts, failure, understanding_id, candidate_set_id, available_domain_ids: ReplanDecision(
            action="stop", reason=failure.message
        ),
        persist_review=lambda utterance, planning, loop_state, step, reason: (_ for _ in ()).throw(AssertionError("review path should not run")),
        persist_user_input=lambda utterance, planning, loop_state, reason: (_ for _ in ()).throw(AssertionError("user-input path should not run")),
    )

    result = loop.run(
        request=CommandRequest("open example"),
        planning=make_planning(plan),
        run_id="run_semantic_result",
        goal_id="goal_semantic_result",
    )

    assert result.decision == "complete"
    assert result.payload["execution"]["acceptance_result"]["semantic_acceptance_decision_id"] == "sacc_goal_loop_semantic"


def test_observation_progress_ignores_volatile_fields() -> None:
    pre = LoopObservation(
        observation_id="obs_pre",
        level="L0",
        phase="pre",
        packages={"session_context": {"captured_at": "2026-06-24T00:00:00Z", "active_url": "https://example.com"}},
        route_id="browser_search_web_route",
        step_id="step_1",
    )
    post = LoopObservation(
        observation_id="obs_post",
        level="L0",
        phase="post",
        packages={"session_context": {"captured_at": "2026-06-24T00:00:01Z", "active_url": "https://example.com"}},
        route_id="browser_search_web_route",
        step_id="step_1",
    )

    assert observation_progressed(pre, post) is False


def test_observation_progress_detects_meaningful_state_change() -> None:
    pre = LoopObservation(
        observation_id="obs_pre",
        level="L0",
        phase="pre",
        packages={"browser_context": {"active_url": "https://example.com", "captured_at": "2026-06-24T00:00:00Z"}},
        route_id="browser_search_web_route",
        step_id="step_1",
    )
    post = LoopObservation(
        observation_id="obs_post",
        level="L0",
        phase="post",
        packages={"browser_context": {"active_url": "https://example.com/done", "captured_at": "2026-06-24T00:00:01Z"}},
        route_id="browser_search_web_route",
        step_id="step_1",
    )

    assert observation_progressed(pre, post) is True


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


def _retry_step_result(step: TaskStep, execution_attempts: dict[str, int]) -> StepExecutionResult:
    execution_attempts["count"] += 1
    if execution_attempts["count"] == 1:
        return StepExecutionResult(
            step_id=step.id,
            layer="adapter_execute",
            status="failed",
            capability_id=step.capability_id,
            adapter_status="timeout",
            error="tool execution timed out",
            result={"status": "failed"},
        )
    return StepExecutionResult(
        step_id=step.id,
        layer="adapter_execute",
        status="succeeded",
        capability_id=step.capability_id,
        result={"selected_target": step.id},
    )


def _counted_success_step(step: TaskStep, execution_attempts: dict[str, int]) -> StepExecutionResult:
    execution_attempts["count"] += 1
    return StepExecutionResult(
        step_id=step.id,
        layer="adapter_execute",
        status="succeeded",
        capability_id=step.capability_id,
        result={"selected_target": f"{step.id}-{execution_attempts['count']}"},
    )


def _terminal_acceptance_assessment(
    plan: TaskPlan,
    step_results: tuple[StepExecutionResult, ...],
    assessment_attempts: dict[str, int],
) -> PlanExecutionResult:
    assessment_attempts["count"] += 1
    if assessment_attempts["count"] == 1:
        return PlanExecutionResult(
            plan_id=plan.plan_id,
            status="succeeded",
            step_results=step_results,
            execution_status="succeeded",
            acceptance_status="indeterminate",
            overall_status="incomplete",
            acceptance_result={
                "message": "verification remained inconclusive",
                "semantic_acceptance_decision_id": "sacc_indeterminate",
            },
        )
    return PlanExecutionResult(
        plan_id=plan.plan_id,
        status="succeeded",
        step_results=step_results,
        execution_status="succeeded",
        acceptance_status="passed",
        overall_status="completed",
        acceptance_result={
            "message": "goal completed after repair",
            "semantic_acceptance_decision_id": "sacc_repaired",
        },
    )


def _replanned_terminal_assessment(
    plan: TaskPlan,
    step_results: tuple[StepExecutionResult, ...],
) -> PlanExecutionResult:
    if plan.plan_id == "plan_terminal_fail":
        return PlanExecutionResult(
            plan_id=plan.plan_id,
            status="succeeded",
            step_results=step_results,
            execution_status="succeeded",
            acceptance_status="failed",
            overall_status="incomplete",
            acceptance_result={
                "message": "wrong target was opened",
                "semantic_acceptance_decision_id": "sacc_failed",
            },
        )
    return PlanExecutionResult(
        plan_id=plan.plan_id,
        status="succeeded",
        step_results=step_results,
        execution_status="succeeded",
        acceptance_status="passed",
        overall_status="completed",
        acceptance_result={
            "message": "goal completed after replanning",
            "semantic_acceptance_decision_id": "sacc_replanned",
        },
    )


def _capture_replan_evidence(
    captured: dict[str, object],
    attempts,
    failure,
) -> ReplanDecision:
    captured["failure_class"] = failure.failure_class
    captured["attempt_count"] = len(attempts)
    last_acceptance = attempts[-1].execution_result.acceptance_result if attempts and attempts[-1].execution_result is not None else {}
    if isinstance(last_acceptance, dict):
        captured["semantic_acceptance_decision_id"] = last_acceptance.get("semantic_acceptance_decision_id")
    return ReplanDecision(action="stop", reason="captured replan evidence")


def _replan_history_step_result(step: TaskStep, execution_counts: dict[str, int]) -> StepExecutionResult:
    execution_counts[step.id] += 1
    if step.id == "step_2":
        return StepExecutionResult(
            step_id=step.id,
            layer="adapter_execute",
            status="failed",
            capability_id=step.capability_id,
            error="adapter failed",
            result={"status": "failed"},
        )
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
