from vibeos.replanner import EvidenceDrivenReplanner, ReplanDecisionProvider, ReplanOption, make_replan_decision
from vibeos.task_models import DisplayFields, FailureClassification, PlanAttempt, TaskPlan, TaskRoute, TaskStep


def test_replanner_retries_same_route_once_for_transient_failures() -> None:
    replanner = EvidenceDrivenReplanner(max_attempts=3)
    plan = make_plan("browser.search_web", route_id="browser_search")
    attempts = (
        PlanAttempt(attempt_id="attempt_1", run_id="run_1", attempt_index=1, trigger="initial", selected_route_id="browser_search"),
    )

    decision = replanner.decide(
        utterance="search web for hello",
        current_plan=plan,
        attempts=attempts,
        failure=FailureClassification(failure_class="tool_timeout", message="timed out", retryable=True),
    )

    assert decision.action == "retry_same_attempt"


def test_replanner_prefers_host_generated_alternative_domains_after_semantic_mismatch() -> None:
    replanner = EvidenceDrivenReplanner()
    plan = make_plan("app.open", route_id="apps_open_route", domain_id="apps")

    decision = replanner.decide(
        utterance="open Baidu official website",
        current_plan=plan,
        attempts=(),
        failure=FailureClassification(failure_class="semantic_mismatch", message="not a local app", replannable=True),
        available_domain_ids=("apps", "browser"),
    )

    assert decision.action == "replan_with_constraints"
    assert decision.candidate_domain_ids == ("browser",)
    assert decision.do_not_repeat_route_ids == ("apps_open_route",)
    assert decision.replan_decision_id is not None
    assert decision.provider_name == "local"
    assert decision.parse_valid is False
    assert decision.fallback_used is True


def test_replanner_asks_user_for_permission_blocked() -> None:
    replanner = EvidenceDrivenReplanner()
    plan = make_plan("window.close", route_id="window_close_route")

    decision = replanner.decide(
        utterance="close firefox",
        current_plan=plan,
        attempts=(),
        failure=FailureClassification(failure_class="permission_blocked", message="approval required"),
    )

    assert decision.action == "ask_user"
    assert "approval required" in decision.reason
    assert decision.replan_decision_id is not None


class FixedReplanProvider(ReplanDecisionProvider):
    provider_name = "fake_replanner"
    model_name = "fake-structured"

    def __init__(self, selected_option_id: str) -> None:
        self.selected_option_id = selected_option_id

    def select_option(
        self,
        *,
        utterance: str,
        current_plan: TaskPlan,
        attempts,
        failure,
        options,
        understanding_id,
        candidate_set_id,
        available_domain_ids=(),
    ):
        selected = next(item for item in options if item.option_id == self.selected_option_id)
        return make_replan_decision(
            option=ReplanOption(
                option_id=selected.option_id,
                action=selected.action,
                reason="fake replanner selected a bounded option",
                do_not_repeat_route_ids=selected.do_not_repeat_route_ids,
                do_not_repeat_capability_ids=selected.do_not_repeat_capability_ids,
                candidate_domain_ids=selected.candidate_domain_ids,
            ),
            understanding_id=understanding_id,
            candidate_set_id=candidate_set_id,
            provider_name=self.provider_name,
            model_name=self.model_name,
        )


def test_replanner_allows_bounded_provider_override_within_host_generated_options() -> None:
    plan = make_plan("app.open", route_id="apps_open_route", domain_id="apps")
    replanner = EvidenceDrivenReplanner(provider=FixedReplanProvider("ask_user"))

    decision = replanner.decide(
        utterance="open Baidu official website",
        current_plan=plan,
        attempts=(),
        failure=FailureClassification(failure_class="semantic_mismatch", message="not a local app", replannable=True),
        understanding_id="und_1",
        candidate_set_id="cset_1",
        available_domain_ids=("apps", "browser"),
    )

    assert decision.action == "ask_user"
    assert decision.provider_name == "fake_replanner"
    assert decision.reason == "fake replanner selected a bounded option"


def test_replanner_uses_generic_route_replan_when_no_alternative_domain_is_available() -> None:
    replanner = EvidenceDrivenReplanner()
    plan = make_plan("app.open", route_id="apps_open_route", domain_id="apps")

    decision = replanner.decide(
        utterance="open Baidu official website",
        current_plan=plan,
        attempts=(),
        failure=FailureClassification(failure_class="semantic_mismatch", message="not a local app", replannable=True),
        available_domain_ids=("apps",),
    )

    assert decision.action == "replan_with_constraints"
    assert decision.candidate_domain_ids == ()
    assert decision.do_not_repeat_route_ids == ("apps_open_route",)


def test_replanner_offers_alternative_domain_replan_after_acceptance_failure() -> None:
    replanner = EvidenceDrivenReplanner()
    plan = make_plan("app.open", route_id="apps_open_route", domain_id="apps")

    decision = replanner.decide(
        utterance="open Baidu official website",
        current_plan=plan,
        attempts=(),
        failure=FailureClassification(
            failure_class="acceptance_failed",
            message="the resulting state did not satisfy the requested target",
            replannable=True,
        ),
        available_domain_ids=("apps", "browser"),
    )

    assert decision.action == "replan_with_constraints"
    assert decision.candidate_domain_ids == ("browser",)
    assert decision.do_not_repeat_route_ids == ("apps_open_route",)


def make_plan(capability_id: str, *, route_id: str, domain_id: str = "desktop") -> TaskPlan:
    return TaskPlan(
        schema_version="v0.5",
        plan_id="plan_1",
        utterance="test",
        display=DisplayFields(goal="test"),
        selected_route_id=route_id,
        routes=(TaskRoute(id=route_id, score=1.0, domain_id=domain_id),),
        steps=(TaskStep(id="step_1", action=capability_id, capability_id=capability_id),),
    )
