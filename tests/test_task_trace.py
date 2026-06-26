import json
from dataclasses import asdict, replace
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from uuid import uuid4

from vibeos.acceptance import AcceptanceEngine
from vibeos.audit import AuditLog
from vibeos.browser_state import record_browser_observation
from vibeos.broker import CapabilityBroker
from vibeos.candidate_selection import CandidateSelectionDecision, CandidateSet
from vibeos.cli import main
from vibeos.intent import RuleIntentBroker
from vibeos.loop_models import LoopState
from vibeos.models import CommandRequest, PermissionReview
from vibeos.planner import PlanningArtifacts
from vibeos.portal import PortalAdapter
from vibeos.reviews import ReviewStore
from vibeos.runtime import LocalRuntime
from vibeos.task_models import DisplayFields, ExpectedState, PlanAttempt, PlanExecutionResult, StepExecutionResult, StepPrecondition, StepProvenance, StepReviewRecord, TaskPlan, TaskRoute, TaskSpan, TaskStep, UtteranceAnalysis
from vibeos.task_trace import TaskTraceStore, bind_trace_session, make_trace_run_id
from vibeos.understanding import UnderstandingArtifact


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
        from vibeos.browser_state import record_browser_observation

        record_browser_observation(active_url=uri, query=observed_query or None, adapter="task-trace-test")
        return {"status": "opened", "uri": uri, "adapter": "task-trace-test"}


def test_successful_task_persists_unified_trace_bundle(monkeypatch) -> None:
    monkeypatch.setenv("VIBEOS_STATE_DIR", str(make_state_dir("success")))
    broker = CapabilityBroker(
        intent_broker=RuleIntentBroker(),
        portal=ObservedPortal(),
        audit=AuditLog(),
        reviews=ReviewStore(),
    )

    result = broker.handle(CommandRequest("search web for hello"))

    assert result.status == "executed"
    assert result.trace_run_id
    store = TaskTraceStore()
    summary = store.summary(result.trace_run_id)
    events = store.events(result.trace_run_id)
    model_io = store.model_io(result.trace_run_id)

    assert summary is not None
    assert summary["status"] == "executed"
    assert any(item["event_type"] == "request_received" for item in events)
    assert any(item["event_type"] == "understanding_created" for item in events)
    assert any(item["event_type"] == "review_decided" for item in events)
    assert any(item["event_type"] == "attempt_completed" for item in events)
    assert any(item["event_type"] == "verifier_completed" for item in events)
    assert any(item["actor"] == "understanding_classifier" and item["provider"] == "local" and item["fallback_used"] is True for item in model_io)
    assert any(item["actor"] == "goal_synthesizer" and item["provider"] == "local" and item["fallback_used"] is True for item in model_io)
    assert any(
        item["actor"] in {"route_selector", "strategy_selector"}
        and item["provider"] == "local"
        and item["fallback_used"] is True
        for item in model_io
    )
    assert summary["primary_understanding_call_count"] >= 1
    assert summary["full_context_call_count"] >= 1
    assert summary["model_reparse_count"] == 0
    assert summary["structured_followup_call_count"] >= 1
    assert summary["artifact_reuse_count"] >= 1
    assert summary["semantic_summary_cache_hit_count"] == 0
    assert summary["escalation_count"] == 0
    assert summary["model_call_kinds"]["full_context_understanding"] >= 1
    assert summary["model_call_kinds"]["structured_followup"] >= 1


def test_clarification_task_persists_clarification_provider_trace(monkeypatch) -> None:
    monkeypatch.setenv("VIBEOS_STATE_DIR", str(make_state_dir("clarification")))
    broker = CapabilityBroker(intent_broker=RuleIntentBroker(), audit=AuditLog(), reviews=ReviewStore())

    result = broker.handle(CommandRequest("open that site we discussed yesterday"))

    assert result.status == "ambiguous"
    assert result.trace_run_id
    store = TaskTraceStore()
    model_io = store.model_io(result.trace_run_id)

    assert any(item["actor"] == "clarification_generator" and item["provider"] == "local" and item["fallback_used"] is True for item in model_io)


def test_trace_cli_reads_persisted_run_bundle(monkeypatch, capsys) -> None:
    monkeypatch.setenv("VIBEOS_STATE_DIR", str(make_state_dir("cli")))
    runtime = LocalRuntime(
        CapabilityBroker(
            intent_broker=RuleIntentBroker(),
            portal=ObservedPortal(),
            audit=AuditLog(),
            reviews=ReviewStore(),
        )
    )
    monkeypatch.setattr("vibeos.cli.build_runtime", lambda: runtime)

    exit_code = main(["ask", "search web for hello", "--json"])
    ask_payload = json.loads(capsys.readouterr().out)
    trace_run_id = ask_payload["trace_run_id"]

    show_exit = main(["trace", "show", trace_run_id, "--json"])
    show_payload = json.loads(capsys.readouterr().out)
    events_exit = main(["trace", "events", trace_run_id, "--json"])
    events_payload = json.loads(capsys.readouterr().out)
    model_exit = main(["trace", "model", trace_run_id, "--json"])
    model_payload = json.loads(capsys.readouterr().out)
    latest_exit = main(["trace", "latest", "--json"])
    latest_payload = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert show_exit == 0
    assert events_exit == 0
    assert model_exit == 0
    assert latest_exit == 0
    assert show_payload["summary"]["run_id"] == trace_run_id
    assert any(item["event_type"] == "command_result_emitted" for item in events_payload)
    assert any(item["actor"] == "goal_synthesizer" and item["provider"] == "local" and item["fallback_used"] is True for item in model_payload)
    assert latest_payload[0]["run_id"] == trace_run_id


def test_review_rejection_creates_trace_bundle(monkeypatch, capsys) -> None:
    monkeypatch.setenv("VIBEOS_STATE_DIR", str(make_state_dir("reject")))
    runtime = LocalRuntime(
        CapabilityBroker(
            intent_broker=RuleIntentBroker(),
            audit=AuditLog(),
            reviews=ReviewStore(),
        )
    )
    monkeypatch.setattr("vibeos.cli.build_runtime", lambda: runtime)

    first_exit = main(["ask", "clipboard hello", "--json"])
    first_payload = json.loads(capsys.readouterr().out)
    reject_exit = main(["reviews", "reject", first_payload["review_id"], "--json"])
    reject_payload = json.loads(capsys.readouterr().out)

    assert first_exit == 1
    assert reject_exit == 0
    assert reject_payload["trace_run_id"]
    store = TaskTraceStore()
    events = store.events(reject_payload["trace_run_id"])
    summary = store.summary(reject_payload["trace_run_id"])

    assert summary is not None
    assert summary["review_id"] == first_payload["review_id"]
    assert any(item["event_type"] == "review_rejected" for item in events)


def test_trace_events_record_understanding_supersession(monkeypatch) -> None:
    monkeypatch.setenv("VIBEOS_STATE_DIR", str(make_state_dir("supersession")))
    broker = CapabilityBroker(intent_broker=RuleIntentBroker(), audit=AuditLog(), reviews=ReviewStore())

    initial_understanding = make_understanding(
        UtteranceAnalysis(
            utterance="open that site we discussed yesterday",
            type="task",
            confidence=1.0,
            domains=("apps",),
            explanation="misclassified as an app request",
            task_spans=(TaskSpan(id="span_1", text="open that site we discussed yesterday", start=0, end=37, domain="apps", confidence=1.0),),
        )
    )
    apps_plan = TaskPlan(
        schema_version="v0.5",
        plan_id="plan_trace_supersession_apps",
        utterance="open that site we discussed yesterday",
        display=DisplayFields(goal="open an application"),
        selected_route_id="apps_open_route",
        routes=(TaskRoute(id="apps_open_route", score=1.0, domain_id="apps", required_capabilities=("app.open",)),),
        steps=(
            TaskStep(
                id="open_app",
                action="app.open",
                capability_id="app.open",
                target={"name": "that site we discussed yesterday"},
                expected_state=ExpectedState(kind="app_opened_or_focused", fields={"app": "that site we discussed yesterday"}),
                preconditions=(StepPrecondition(kind="capability_available", capability_id="app.open"),),
                provenance=StepProvenance(source_span_id="span_1", planner="test"),
            ),
        ),
        provenance={"planner": "test"},
    )
    plans = [
        PlanningArtifacts(
            understanding=initial_understanding,
            analysis=initial_understanding.analysis,
            goal_synthesis=None,
            plan=apps_plan,
            candidates=(apps_plan,),
        ),
        PlanningArtifacts(
            understanding=initial_understanding,
            analysis=UtteranceAnalysis(
                utterance="open that site we discussed yesterday",
                type="clarification",
                confidence=0.95,
                domains=("browser",),
                explanation="the target is ambiguous without an exact site",
                task_spans=(),
                chat_response="Which exact site should I open?",
            ),
            goal_synthesis=None,
            plan=None,
            candidates=(),
        ),
    ]

    monkeypatch.setattr("vibeos.broker.plan_turn", lambda *args, **kwargs: plans.pop(0))

    result = broker.handle(CommandRequest("open that site we discussed yesterday"))

    assert result.status == "ambiguous"
    assert result.trace_run_id
    events = TaskTraceStore().events(result.trace_run_id)
    supersession_event = next(item for item in events if item["event_type"] == "understanding_superseded")

    assert supersession_event["data"]["artifact_role"] == "supersession"
    assert supersession_event["data"]["primary_understanding_id"] == initial_understanding.understanding_id
    assert initial_understanding.understanding_id in supersession_event["data"]["source_artifact_ids"]


def test_user_input_resume_trace_records_loop_resume_and_completion(monkeypatch) -> None:
    monkeypatch.setenv("VIBEOS_STATE_DIR", str(make_state_dir("loop-resume-trace")))
    monkeypatch.setenv("VIBEOS_ENABLE_GOAL_LOOP", "1")
    broker = CapabilityBroker(
        intent_broker=RuleIntentBroker(),
        portal=ObservedPortal(),
        audit=AuditLog(),
        reviews=ReviewStore(),
    )
    resolved_understanding = replace(
        make_understanding(
            UtteranceAnalysis(
                utterance="open that site we discussed yesterday\n\nAdditional user detail: browser",
                type="task",
                confidence=0.95,
                domains=("browser",),
                explanation="supplemental input resolved the browser target",
                task_spans=(TaskSpan(id="span_1", text="browser", start=51, end=58, domain="browser", confidence=0.95),),
            )
        ),
        primary_understanding_id="und_trace_identity",
        source_understanding_id="und_trace_identity",
        artifact_role="refinement",
    )
    resolved_plan = TaskPlan(
        schema_version="v0.5",
        plan_id="plan_trace_user_input_resume",
        utterance=resolved_understanding.utterance,
        display=DisplayFields(goal="search the web"),
        selected_route_id="browser_search_web_route",
        routes=(TaskRoute(id="browser_search_web_route", score=1.0, domain_id="browser", required_capabilities=("browser.search_web",)),),
        steps=(
            TaskStep(
                id="search_web",
                action="browser.search_web",
                capability_id="browser.search_web",
                target={"query": "browser"},
                expected_state=ExpectedState(kind="search_results_available", fields={"query": "browser"}),
                preconditions=(StepPrecondition(kind="capability_available", capability_id="browser.search_web"),),
                provenance=StepProvenance(source_span_id="span_1", planner="test"),
            ),
        ),
        provenance={"planner": "test"},
    )
    resolved_candidate_set = CandidateSet(
        candidate_set_id="cset_trace_resume",
        understanding_id=resolved_understanding.understanding_id,
        generated_by="test",
        candidates=(),
    )
    resolved_route_decision = CandidateSelectionDecision(
        route_decision_id="rdec_trace_resume",
        candidate_set_id="cset_trace_resume",
        understanding_id=resolved_understanding.understanding_id,
        action="select",
        selected_candidate_id="cand_trace_resume",
        reason="resolved after user input",
        provider_name="test",
        model_name="test",
    )
    review_state = LoopState(
        loop_snapshot_id="lsnap_trace_resume",
        trace_run_id="run_trace_resume",
        goal_id="goal_trace_resume",
        primary_understanding_id="und_trace_prior",
        candidate_set_id=None,
        selected_route_decision_id=None,
        current_step_id="open_app",
        completed_step_ids=("open_app",),
        attempt_records=(
            PlanAttempt(
                attempt_id="attempt_trace_prior",
                run_id="run_trace_resume",
                attempt_index=1,
                trigger="initial_plan",
                selected_route_id="apps_open_route",
                task_plan=TaskPlan(
                    schema_version="v0.5",
                    plan_id="plan_trace_prior_app",
                    utterance="open browser",
                    display=DisplayFields(goal="open browser"),
                    selected_route_id="apps_open_route",
                    routes=(TaskRoute(id="apps_open_route", score=1.0, domain_id="apps"),),
                    steps=(TaskStep(id="open_app", action="app.open", capability_id="app.open", target={"name": "browser"}),),
                ),
                execution_result=PlanExecutionResult(
                    plan_id="plan_trace_prior_app",
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
    review = broker.reviews.create_loop_review(
        "open that site we discussed yesterday",
        plan_payload={"analysis": {"type": "clarification", "confidence": 0.5, "domains": ["browser"], "explanation": "need more detail", "chat_response": "which site?"}},
        snapshot_payload=asdict(review_state),
        pending_reason="which site?",
        step_id=None,
        review_kind="user_input",
    )
    planning = PlanningArtifacts(
        understanding=resolved_understanding,
        analysis=resolved_understanding.analysis,
        goal_synthesis=None,
        plan=resolved_plan,
        candidates=(resolved_plan,),
        candidate_set=resolved_candidate_set,
        route_decision=resolved_route_decision,
    )

    monkeypatch.setattr("vibeos.broker.plan_turn", lambda *args, **kwargs: planning)

    resumed = broker.handle(CommandRequest("", review_id=review.review_id, supplemental_input="browser"))

    assert resumed.status == "executed"
    assert resumed.trace_run_id
    events = TaskTraceStore().events(resumed.trace_run_id)

    assert any(item["event_type"] == "loop_resumed" and item["data"]["resume_kind"] == "user_input" for item in events)
    assert any(item["event_type"] == "loop_completed" and item["status"] == "completed" for item in events)


def test_review_snapshot_is_traceable(monkeypatch) -> None:
    monkeypatch.setenv("VIBEOS_STATE_DIR", str(make_state_dir("review-snapshot-trace")))
    monkeypatch.setenv("VIBEOS_ENABLE_GOAL_LOOP", "1")
    broker = CapabilityBroker(
        intent_broker=RuleIntentBroker(),
        portal=ObservedPortal(),
        audit=AuditLog(),
        reviews=ReviewStore(),
    )

    def review_step(plan, step, pre_observation=None):
        return (
            PermissionReview("L2", True, True, "approval required"),
            StepReviewRecord("srev_traceable", step.id, step.action, "L2", True, True, "approval required"),
        )

    monkeypatch.setattr(broker, "review_task_step", review_step)

    result = broker.handle(CommandRequest("search web for hello"))

    assert result.status == "review_required"
    assert result.trace_run_id
    assert result.review_id
    events = TaskTraceStore().events(result.trace_run_id)
    suspended_event = next(item for item in events if item["event_type"] == "loop_suspended")
    review_record = broker.reviews.get(result.review_id)

    assert suspended_event["review_id"] == result.review_id
    assert suspended_event["status"] == "needs_review"
    assert suspended_event["data"]["loop_snapshot_id"] == result.result["loop_snapshot_id"]
    assert suspended_event["data"]["current_step_id"] == "browser_search_web"
    assert review_record is not None
    assert review_record.snapshot_payload is not None
    assert review_record.snapshot_payload["loop_snapshot_id"] == result.result["loop_snapshot_id"]
    assert review_record.snapshot_payload["pending_step_safety_review_id"] == "srev_traceable"


def test_loop_resume_preserves_understanding_and_goal_identity(monkeypatch) -> None:
    monkeypatch.setenv("VIBEOS_STATE_DIR", str(make_state_dir("loop-resume-identity")))
    monkeypatch.setenv("VIBEOS_ENABLE_GOAL_LOOP", "1")
    record_browser_observation(active_url="about:blank", query=None, adapter="test-reset")
    broker = CapabilityBroker(
        intent_broker=RuleIntentBroker(),
        portal=ObservedPortal(),
        audit=AuditLog(),
        reviews=ReviewStore(),
    )
    resolved_understanding = make_understanding(
        UtteranceAnalysis(
            utterance="open that site we discussed yesterday\n\nAdditional user detail: browser",
            type="task",
            confidence=0.95,
            domains=("browser",),
            explanation="supplemental input resolved the browser target",
            task_spans=(TaskSpan(id="span_1", text="browser", start=51, end=58, domain="browser", confidence=0.95),),
        )
    )
    resolved_plan = TaskPlan(
        schema_version="v0.5",
        plan_id="plan_trace_resume_identity",
        utterance=resolved_understanding.utterance,
        display=DisplayFields(goal="search the web"),
        selected_route_id="browser_search_web_route",
        routes=(TaskRoute(id="browser_search_web_route", score=1.0, domain_id="browser", required_capabilities=("browser.search_web",)),),
        steps=(
            TaskStep(
                id="search_web",
                action="browser.search_web",
                capability_id="browser.search_web",
                target={"query": "browser"},
                expected_state=ExpectedState(kind="search_results_available", fields={"query": "browser"}),
                preconditions=(StepPrecondition(kind="capability_available", capability_id="browser.search_web"),),
                provenance=StepProvenance(source_span_id="span_1", planner="test"),
            ),
        ),
        provenance={"planner": "test"},
    )
    resolved_candidate_set = CandidateSet(
        candidate_set_id="cset_trace_resume_identity",
        understanding_id=resolved_understanding.understanding_id,
        generated_by="test",
        candidates=(),
    )
    resolved_route_decision = CandidateSelectionDecision(
        route_decision_id="rdec_trace_resume_identity",
        candidate_set_id="cset_trace_resume_identity",
        understanding_id=resolved_understanding.understanding_id,
        action="select",
        selected_candidate_id="cand_trace_resume_identity",
        reason="resolved after user input",
        provider_name="test",
        model_name="test",
    )
    review_state = LoopState(
        loop_snapshot_id="lsnap_trace_resume_identity",
        trace_run_id="run_trace_resume_identity",
        goal_id="goal_trace_resume_identity",
        primary_understanding_id="und_trace_identity",
        candidate_set_id=None,
        selected_route_decision_id=None,
        current_step_id="open_app",
        completed_step_ids=("open_app",),
        attempt_records=(
            PlanAttempt(
                attempt_id="attempt_trace_identity",
                run_id="run_trace_resume_identity",
                attempt_index=1,
                trigger="initial_plan",
                understanding_id="und_trace_identity",
                selected_route_id="apps_open_route",
                task_plan=TaskPlan(
                    schema_version="v0.5",
                    plan_id="plan_trace_identity_prior",
                    utterance="open browser",
                    display=DisplayFields(goal="open browser"),
                    selected_route_id="apps_open_route",
                    routes=(TaskRoute(id="apps_open_route", score=1.0, domain_id="apps"),),
                    steps=(TaskStep(id="open_app", action="app.open", capability_id="app.open", target={"name": "browser"}),),
                ),
                execution_result=PlanExecutionResult(
                    plan_id="plan_trace_identity_prior",
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
    review = broker.reviews.create_loop_review(
        "open that site we discussed yesterday",
        plan_payload={"analysis": {"type": "clarification", "confidence": 0.5, "domains": ["browser"], "explanation": "need more detail", "chat_response": "which site?"}},
        snapshot_payload=asdict(review_state),
        pending_reason="which site?",
        step_id=None,
        review_kind="user_input",
    )
    planning = PlanningArtifacts(
        understanding=resolved_understanding,
        analysis=resolved_understanding.analysis,
        goal_synthesis=None,
        plan=resolved_plan,
        candidates=(resolved_plan,),
        candidate_set=resolved_candidate_set,
        route_decision=resolved_route_decision,
    )

    monkeypatch.setattr("vibeos.broker.plan_turn", lambda *args, **kwargs: planning)

    resumed = broker.handle(CommandRequest("", review_id=review.review_id, supplemental_input="browser"))

    assert resumed.status == "executed"
    assert resumed.trace_run_id
    events = TaskTraceStore().events(resumed.trace_run_id)
    loop_resumed = next(item for item in events if item["event_type"] == "loop_resumed")
    completion = next(item for item in events if item["event_type"] == "loop_completed")

    assert loop_resumed["goal_id"] == "goal_trace_resume_identity"
    assert loop_resumed["data"]["loop_snapshot_id"] == "lsnap_trace_resume_identity"
    assert completion["goal_id"] == "goal_trace_resume_identity"
    assert resumed.result["attempts"][0]["understanding_id"] == "und_trace_identity"


def test_acceptance_trace_records_semantic_artifacts(monkeypatch) -> None:
    monkeypatch.setenv("VIBEOS_STATE_DIR", str(make_state_dir("acceptance-trace")))
    store = TaskTraceStore()
    run_id = make_trace_run_id("acceptance-trace")
    session = store.start_run(
        run_id=run_id,
        command_name="ask",
        utterance="search web for hello",
        mode="auto",
        transport=None,
        dry_run=False,
        debug=False,
    )
    plan = TaskPlan(
        schema_version="v0.5",
        plan_id="plan_acceptance_trace",
        utterance="search web for hello",
        display=DisplayFields(goal="search the web"),
        selected_route_id="browser_search_web_route",
        routes=(TaskRoute(id="browser_search_web_route", score=1.0, domain_id="browser", required_capabilities=("browser.search_web",)),),
        steps=(
            TaskStep(
                id="search_web",
                action="browser.search_web",
                capability_id="browser.search_web",
                target={"query": "hello"},
                expected_state=ExpectedState(kind="search_results_visible", fields={"query": "hello"}),
                preconditions=(StepPrecondition(kind="capability_available", capability_id="browser.search_web"),),
                provenance=StepProvenance(source_span_id="span_1", planner="test"),
            ),
        ),
        provenance={"planner": "test"},
    )
    execution = PlanExecutionResult(
        plan_id=plan.plan_id,
        status="succeeded",
        step_results=(
            StepExecutionResult(
                step_id="search_web",
                layer="adapter_execute",
                status="succeeded",
                capability_id="browser.search_web",
                result={"status": "opened"},
            ),
        ),
    )

    with bind_trace_session(session):
        AcceptanceEngine().evaluate(
            plan=plan,
            execution=execution,
            verification_results=(
                {
                    "verifier_id": "browser_search_route_completed",
                    "status": "passed",
                    "details": {"query": "hello"},
                    "message": "observed matching browser query",
                },
            ),
            understanding_id="und_test",
            candidate_set_id="cset_test",
            route_decision_id="rdec_test",
        )
        session.finalize(status="executed", overall_status="completed", plan_id=plan.plan_id)

    events = store.events(run_id)

    assert any(item["event_type"] == "semantic_summary_created" for item in events)
    assert any(item["event_type"] == "semantic_acceptance_decided" for item in events)


def test_acceptance_trace_counts_semantic_summary_cache_hits(monkeypatch) -> None:
    monkeypatch.setenv("VIBEOS_STATE_DIR", str(make_state_dir("acceptance-trace-cache")))
    store = TaskTraceStore()
    run_id = make_trace_run_id("acceptance-trace-cache")
    session = store.start_run(
        run_id=run_id,
        command_name="ask",
        utterance="search web for hello",
        mode="auto",
        transport=None,
        dry_run=False,
        debug=False,
    )
    plan = TaskPlan(
        schema_version="v0.5",
        plan_id="plan_acceptance_trace_cache",
        utterance="search web for hello",
        display=DisplayFields(goal="search the web"),
        selected_route_id="browser_search_web_route",
        routes=(TaskRoute(id="browser_search_web_route", score=1.0, domain_id="browser", required_capabilities=("browser.search_web",)),),
        steps=(),
        provenance={"planner": "test"},
    )
    execution = PlanExecutionResult(plan_id=plan.plan_id, status="succeeded", execution_status="succeeded")
    engine = AcceptanceEngine()
    kwargs = {
        "plan": plan,
        "execution": execution,
        "verification_results": (
            {
                "verifier_id": "browser_search_route_completed",
                "status": "passed",
                "details": {"query": "hello"},
                "message": "observed matching browser query",
            },
        ),
        "understanding_id": "und_test",
        "candidate_set_id": "cset_test",
        "route_decision_id": "rdec_test",
    }

    with bind_trace_session(session):
        engine.evaluate(**kwargs)
        engine.evaluate(**kwargs)
        session.finalize(status="executed", overall_status="completed", plan_id=plan.plan_id)

    summary = store.summary(run_id)

    assert summary is not None
    assert summary["semantic_summary_cache_hit_count"] == 1


def make_state_dir(name: str) -> Path:
    return Path(".vibeos") / f"state-{name}-{uuid4().hex}"


def make_understanding(analysis: UtteranceAnalysis) -> UnderstandingArtifact:
    return UnderstandingArtifact(
        understanding_id=f"und_{uuid4().hex[:8]}",
        utterance=analysis.utterance,
        analysis=analysis,
    )
