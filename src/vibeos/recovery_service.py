from __future__ import annotations

from .failure_classifier import FailureClassifier
from .replanner import Replanner
from .task_models import FailureClassification, PlanAttempt, PlanExecutionResult, ReplanDecision, TaskPlan


class RecoveryService:
    """Owns failure classification and bounded recovery selection."""

    def __init__(self, *, classifier: FailureClassifier, replanner: Replanner) -> None:
        self.classifier = classifier
        self.replanner = replanner

    def classify(self, plan: TaskPlan, execution: PlanExecutionResult) -> FailureClassification:
        return self.classifier.classify(plan, execution)

    def decide(
        self,
        utterance: str,
        plan: TaskPlan,
        attempts: tuple[PlanAttempt, ...],
        failure: FailureClassification,
        understanding_id: str | None,
        candidate_set_id: str | None,
        available_domain_ids: tuple[str, ...],
    ) -> ReplanDecision:
        return self.replanner.decide(
            utterance=utterance,
            current_plan=plan,
            attempts=attempts,
            failure=failure,
            understanding_id=understanding_id,
            candidate_set_id=candidate_set_id,
            available_domain_ids=available_domain_ids,
        )
