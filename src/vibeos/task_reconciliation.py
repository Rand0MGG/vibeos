from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol

from .core.domain.task import ActionProposal
from .task_models import StepExecutionResult


ReconciliationOutcome = Literal["succeeded", "not_applied", "unknown"]


@dataclass(frozen=True)
class ReconciliationResult:
    outcome: ReconciliationOutcome
    reason: str
    step_result: StepExecutionResult | None = None


class ActionReconciler(Protocol):
    def reconcile(self, proposal: ActionProposal) -> ReconciliationResult: ...


class ConservativeActionReconciler:
    """Default when an adapter has no capability-specific external proof."""

    def reconcile(self, proposal: ActionProposal) -> ReconciliationResult:
        return ReconciliationResult(
            outcome="unknown",
            reason=f"{proposal.capability_id} adapter has no safe reconciliation proof",
        )
