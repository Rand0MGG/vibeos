from __future__ import annotations

from dataclasses import dataclass

from .candidate_selection import CandidateSelectionDecision, CandidateSet
from .domain_models import CapabilityExposure, DomainRoutingResult, ObservationReceipt, ObservationRequest
from .goal_models import GoalSynthesisResult
from .task_models import TaskPlan, UtteranceAnalysis
from .understanding import UnderstandingArtifact, UnderstandingRefinement, UnderstandingSupersession


@dataclass(frozen=True)
class PlanningArtifacts:
    understanding: UnderstandingArtifact
    analysis: UtteranceAnalysis
    goal_synthesis: GoalSynthesisResult | None
    plan: TaskPlan | None
    candidates: tuple[TaskPlan, ...]
    understanding_refinement: UnderstandingRefinement | None = None
    understanding_supersession: UnderstandingSupersession | None = None
    candidate_set: CandidateSet | None = None
    route_decision: CandidateSelectionDecision | None = None
    domain_routing: DomainRoutingResult | None = None
    observation_request: ObservationRequest | None = None
    observation_receipt: ObservationReceipt | None = None
    capability_exposure: CapabilityExposure | None = None
    trace: object | None = None
    debug_trace: object | None = None
