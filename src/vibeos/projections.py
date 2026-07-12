from __future__ import annotations

from dataclasses import replace
from hashlib import sha256

from .agent_runtime import GoalPolicy, GoalRunResult, GoalRuntime, GoalTurn, TerminalOutcome
from .goal_models import GoalSpec
from .run_context import RunContext
from .run_ledger import AttemptLedgerEntry, RunLedger
from .strategy import StrategyCandidate, StrategyDecision


def project_legacy_runtime_payload(
    *,
    context: RunContext,
    goal_spec: GoalSpec,
    utterance: str,
    strategies: tuple[StrategyCandidate, ...],
    selected_strategy_id: str | None,
    strategy_history: tuple[tuple[StrategyDecision, str | None], ...],
    attempts: tuple[AttemptLedgerEntry, ...],
    terminal: TerminalOutcome,
) -> GoalRunResult:
    """Build the legacy runtime-shaped payload without mutating AgentRuntime."""
    session_id = f"session_{context.run_id}"
    turn_id = _projection_id("turn", f"{context.run_id}:{goal_spec.goal_id}")
    turn = GoalTurn(
        turn_id=turn_id,
        goal_id=goal_spec.goal_id,
        turn_index=1,
        utterance=utterance,
        attempt_ids=tuple(item.attempt_id for item in attempts),
        status=terminal.status,
    )
    runtime = GoalRuntime(
        goal_id=goal_spec.goal_id,
        session_id=session_id,
        goal_spec=goal_spec,
        policy=GoalPolicy(),
        status=terminal.status,
        current_strategy_id=selected_strategy_id,
        turn_ids=(turn_id,),
        attempt_ids=tuple(item.attempt_id for item in attempts),
        terminal_outcome=terminal,
    )
    ledger = RunLedger(session_id=session_id, goal_id=goal_spec.goal_id)
    for decision, attempt_id in strategy_history:
        ledger = ledger.append_strategy_decision(decision, turn_id=turn_id, attempt_id=attempt_id)
    for attempt in attempts:
        ledger = ledger.append_attempt(replace(attempt, turn_id=turn_id, goal_id=goal_spec.goal_id))
    ledger = ledger.with_terminal_outcome(
        {"status": terminal.status, "reason": terminal.reason, "failure_class": terminal.failure_class, "verifier_confirmed": terminal.verifier_confirmed}
    )
    return GoalRunResult(
        session_id=session_id,
        goal_runtime=runtime,
        turn=turn,
        ledger=ledger,
        terminal_outcome=terminal,
        strategy_candidates=strategies,
        selected_strategy_id=selected_strategy_id,
        debug_payload=_debug_payload(runtime, turn, strategies, ledger, selected_strategy_id),
    )


def _projection_id(prefix: str, seed: str) -> str:
    return f"{prefix}_{sha256(f'{prefix}:{seed}'.encode('utf-8')).hexdigest()[:12]}"


def _debug_payload(
    runtime: GoalRuntime, turn: GoalTurn, strategies: tuple[StrategyCandidate, ...], ledger: RunLedger, selected_strategy_id: str | None
) -> dict[str, object]:
    current_attempts = [attempt for attempt in ledger.attempts if attempt.turn_id == turn.turn_id]
    return {
        "goal_runtime": {
            "goal_id": runtime.goal_id,
            "status": runtime.status,
            "current_strategy_id": runtime.current_strategy_id,
            "turn_ids": list(runtime.turn_ids),
            "attempt_ids": list(runtime.attempt_ids),
        },
        "strategy_candidates": [
            {
                "strategy_id": item.strategy_id,
                "route_id": item.route_id,
                "capability_surface": item.capability_surface,
                "interaction_surface": item.interaction_surface,
                "task_plan_id": item.task_plan.plan_id,
            }
            for item in strategies
        ],
        "selected_strategy_id": selected_strategy_id,
        "action_plan_provenance": [
            {"strategy_id": item.strategy_id, "task_plan_id": item.task_plan_id, "route_id": item.route_id} for item in current_attempts
        ],
        "recovery_decisions": [
            {
                "strategy_decision_id": item.strategy_decision_id,
                "action": item.action,
                "strategy_id": item.strategy_id,
                "reason": item.reason,
                "failure_class": item.failure_class,
                "provider_name": item.provider_name,
                "model_name": item.model_name,
                "fallback_used": item.fallback_used,
                "error": item.error,
            }
            for item in ledger.strategy_history
            if item.turn_id == turn.turn_id
        ],
        "provider_artifacts": [
            {
                "strategy_decision_id": item.strategy_decision_id,
                "provider_name": item.provider_name,
                "model_name": item.model_name,
                "parse_valid": item.parse_valid,
                "fallback_used": item.fallback_used,
                "error": item.error,
            }
            for item in ledger.strategy_history
            if item.turn_id == turn.turn_id
        ],
    }
