from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


GoalSynthesisStatus = Literal["ready", "clarification_needed", "missing_capability", "unsupported"]


@dataclass(frozen=True)
class GoalSubgoal:
    subgoal_id: str
    text: str
    goal_type: str
    candidate_domain_ids: tuple[str, ...] = ()
    required_capability_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class GoalSynthesisProvenance:
    provider_name: str
    provider_version: str
    model_name: str | None = None
    fallback_used: bool = False
    parse_valid: bool = True
    error: str | None = None


@dataclass(frozen=True)
class GoalSpec:
    goal_id: str
    goal_text: str
    goal_type: str
    subgoals: tuple[GoalSubgoal, ...] = ()
    candidate_domain_ids: tuple[str, ...] = ()
    required_capability_ids: tuple[str, ...] = ()
    missing_capability_ids: tuple[str, ...] = ()
    clarification_questions: tuple[str, ...] = ()
    constraints: tuple[str, ...] = ()
    fallback_hints: tuple[str, ...] = ()
    assumptions: tuple[str, ...] = ()
    synthesis_provenance: GoalSynthesisProvenance = field(
        default_factory=lambda: GoalSynthesisProvenance(provider_name="unknown", provider_version="unknown")
    )


@dataclass(frozen=True)
class ProviderExchange:
    provider_name: str
    model_name: str
    normalized_output: dict[str, Any] = field(default_factory=dict)
    raw_output: str | None = None
    parse_valid: bool = True
    fallback_used: bool = False
    error: str | None = None


@dataclass(frozen=True)
class GoalSynthesisResult:
    status: GoalSynthesisStatus
    goal_spec: GoalSpec | None = None
    message: str = ""
    exchange: ProviderExchange = field(default_factory=lambda: ProviderExchange(provider_name="unknown", model_name="unknown"))
