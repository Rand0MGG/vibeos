from vibeos.failure_classifier import FailureClassifier
from vibeos.task_models import (
    DisplayFields,
    PlanExecutionResult,
    StepExecutionResult,
    TaskPlan,
    TaskRoute,
    TaskStep,
)


def test_classify_transport_timeout_from_transport_adapter() -> None:
    classifier = FailureClassifier()
    plan = make_plan("window.close")
    execution = PlanExecutionResult(
        plan_id=plan.plan_id,
        status="failed",
        step_results=(
            StepExecutionResult(
                step_id="step_1",
                layer="adapter_execute",
                status="failed",
                adapter="transport.dbus",
                capability_id="window.close",
                adapter_status="timeout",
                error="CommandRequest timed out",
            ),
        ),
        execution_status="failed",
        acceptance_status="skipped",
        overall_status="failed",
    )

    result = classifier.classify(plan, execution)

    assert result.failure_class == "transport_timeout"
    assert result.retryable is True


def test_classify_semantic_mismatch_for_missing_local_app() -> None:
    classifier = FailureClassifier()
    plan = make_plan("app.open", route_id="apps_open_route")
    execution = PlanExecutionResult(
        plan_id=plan.plan_id,
        status="failed",
        step_results=(
            StepExecutionResult(
                step_id="step_1",
                layer="adapter_execute",
                status="failed",
                adapter="apps.registry",
                capability_id="app.open",
                adapter_status="failed",
                error="no application matched 'foo'",
            ),
        ),
        execution_status="failed",
        acceptance_status="skipped",
        overall_status="failed",
    )

    result = classifier.classify(plan, execution)

    assert result.failure_class == "semantic_mismatch"
    assert result.replannable is True
    assert result.details["selected_route_id"] == "apps_open_route"


def test_classify_acceptance_failed_after_successful_execution() -> None:
    classifier = FailureClassifier()
    plan = make_plan("browser.search_web")
    execution = PlanExecutionResult(
        plan_id=plan.plan_id,
        status="succeeded",
        execution_status="succeeded",
        acceptance_status="failed",
        overall_status="failed",
        acceptance_result={"message": "browser verifier did not observe the expected search query"},
    )

    result = classifier.classify(plan, execution)

    assert result.failure_class == "acceptance_failed"
    assert "browser verifier" in result.message
    assert result.replannable is True


def make_plan(capability_id: str, *, route_id: str = "route_1") -> TaskPlan:
    return TaskPlan(
        schema_version="v2",
        plan_id="plan_1",
        utterance="test",
        display=DisplayFields(goal="test"),
        selected_route_id=route_id,
        routes=(TaskRoute(id=route_id, score=1.0),),
        steps=(TaskStep(id="step_1", action=capability_id, capability_id=capability_id),),
    )
