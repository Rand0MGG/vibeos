from vibeos.run_ledger import AttemptLedgerEntry, RunLedger
from vibeos.strategy import StrategyConstraint, StrategyDecision


def test_run_ledger_appends_strategy_decision_and_attempt() -> None:
    ledger = RunLedger(session_id="session_1", goal_id="goal_1")
    decision = StrategyDecision(
        action="select",
        reason="selected highest scoring strategy candidate",
        selected_strategy_id="strategy_1",
        strategy_decision_id="sdec_1",
        provider_name="rule_strategy_selector",
        model_name="deterministic-local",
        constraints=StrategyConstraint(do_not_repeat_route_ids=("route_old",)),
        failure_class="semantic_mismatch",
    )

    updated = ledger.append_strategy_decision(decision, turn_id="turn_1", attempt_id="attempt_1").append_attempt(
        AttemptLedgerEntry(
            attempt_id="attempt_1",
            turn_id="turn_1",
            goal_id="goal_1",
            understanding_id="und_1",
            candidate_set_id="cset_1",
            route_decision_id="rdec_1",
            replan_decision_id="rpdec_1",
            semantic_summary_id="ssum_1",
            semantic_acceptance_decision_id="sacc_1",
            strategy_id="strategy_1",
            route_id="route_1",
            trigger="execute_strategy",
            task_plan_id="plan_1",
            capability_surface="browser",
            interaction_surface="structured_ui_action",
            outcome_status="completed",
            message="ok",
        )
    ).with_terminal_outcome({"status": "completed"})

    assert updated.strategy_history[0].strategy_id == "strategy_1"
    assert updated.strategy_history[0].strategy_decision_id == "sdec_1"
    assert updated.strategy_history[0].provider_name == "rule_strategy_selector"
    assert updated.strategy_history[0].constraints.do_not_repeat_route_ids == ("route_old",)
    assert updated.attempts[0].interaction_surface == "structured_ui_action"
    assert updated.attempts[0].understanding_id == "und_1"
    assert updated.attempts[0].semantic_acceptance_decision_id == "sacc_1"
    assert updated.terminal_outcome["status"] == "completed"
