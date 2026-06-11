from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .strategy import StrategyConstraint, StrategyDecision
from .tool_protocol import ToolInvocationEnvelope


@dataclass(frozen=True)
class StrategyLedgerEntry:
    turn_id: str
    attempt_id: str | None
    strategy_id: str | None
    action: str
    reason: str
    constraints: StrategyConstraint = field(default_factory=StrategyConstraint)
    failure_class: str = "none"


@dataclass(frozen=True)
class AttemptLedgerEntry:
    attempt_id: str
    turn_id: str
    goal_id: str
    strategy_id: str
    route_id: str
    trigger: str
    task_plan_id: str
    capability_surface: str
    interaction_surface: str
    tool_invocations: tuple[ToolInvocationEnvelope, ...] = ()
    evidence: tuple[dict[str, Any], ...] = ()
    outcome_status: str = "incomplete"
    failure_class: str = "none"
    message: str = ""


@dataclass(frozen=True)
class RunLedger:
    session_id: str
    goal_id: str
    strategy_history: tuple[StrategyLedgerEntry, ...] = ()
    attempts: tuple[AttemptLedgerEntry, ...] = ()
    terminal_outcome: dict[str, Any] = field(default_factory=dict)

    def append_strategy_decision(self, decision: StrategyDecision, *, turn_id: str, attempt_id: str | None = None) -> "RunLedger":
        entry = StrategyLedgerEntry(
            turn_id=turn_id,
            attempt_id=attempt_id,
            strategy_id=decision.selected_strategy_id,
            action=decision.action,
            reason=decision.reason,
            constraints=decision.constraints,
            failure_class=decision.failure_class,
        )
        return RunLedger(
            session_id=self.session_id,
            goal_id=self.goal_id,
            strategy_history=(*self.strategy_history, entry),
            attempts=self.attempts,
            terminal_outcome=self.terminal_outcome,
        )

    def append_attempt(self, attempt: AttemptLedgerEntry) -> "RunLedger":
        return RunLedger(
            session_id=self.session_id,
            goal_id=self.goal_id,
            strategy_history=self.strategy_history,
            attempts=(*self.attempts, attempt),
            terminal_outcome=self.terminal_outcome,
        )

    def with_terminal_outcome(self, outcome: dict[str, Any]) -> "RunLedger":
        return RunLedger(
            session_id=self.session_id,
            goal_id=self.goal_id,
            strategy_history=self.strategy_history,
            attempts=self.attempts,
            terminal_outcome=dict(outcome),
        )
