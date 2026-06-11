from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from .assistant_semantics import INTERACTION_SURFACES, InteractionSurface, interaction_surface_score, weaker_interaction_surfaces
from .task_models import TaskPlan


StrategySelectionAction = Literal["select", "stop", "ask_user"]
CapabilitySurface = Literal["workspace-local", "shell-local", "browser", "desktop-linux", "unknown"]
CAPABILITY_SURFACES: tuple[CapabilitySurface, ...] = ("workspace-local", "shell-local", "browser", "desktop-linux", "unknown")


@dataclass(frozen=True)
class StrategyStep:
    tool_id: str
    input_payload: dict[str, object] = field(default_factory=dict)
    task_step_id: str | None = None


@dataclass(frozen=True)
class StrategyConstraint:
    do_not_repeat_strategy_ids: tuple[str, ...] = ()
    do_not_repeat_route_ids: tuple[str, ...] = ()
    do_not_repeat_capability_ids: tuple[str, ...] = ()
    candidate_capability_surfaces: tuple[str, ...] = ()
    candidate_interaction_surfaces: tuple[InteractionSurface, ...] = ()


@dataclass(frozen=True)
class StrategyCandidate:
    strategy_id: str
    goal_id: str
    title: str
    route_id: str
    capability_surface: CapabilitySurface
    task_plan: TaskPlan
    steps: tuple[StrategyStep, ...]
    interaction_surface: InteractionSurface = "native_action"
    priority: float = 1.0
    requires_desktop_integration: bool = False
    requires_network: bool = False
    metadata: dict[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.capability_surface not in CAPABILITY_SURFACES:
            raise ValueError(f"unsupported capability surface: {self.capability_surface}")
        if self.interaction_surface not in INTERACTION_SURFACES:
            raise ValueError(f"unsupported interaction surface: {self.interaction_surface}")

    @property
    def capability_ids(self) -> tuple[str, ...]:
        return tuple(step.capability_id for step in self.task_plan.steps)

    @property
    def tool_ids(self) -> tuple[str, ...]:
        return tuple(step.tool_id for step in self.steps)


@dataclass(frozen=True)
class StrategyDecision:
    action: StrategySelectionAction
    reason: str
    selected_strategy_id: str | None = None
    constraints: StrategyConstraint = field(default_factory=StrategyConstraint)
    failure_class: str = "none"


class RecoveryPolicy:
    def select_strategy(
        self,
        *,
        strategies: tuple[StrategyCandidate, ...],
        constraints: StrategyConstraint,
        environment,
        attempts,
        last_failure_class: str = "none",
    ) -> StrategyDecision:
        eligible: list[tuple[float, StrategyCandidate]] = []
        excluded_by_environment = False
        excluded_strategy_ids = set(constraints.do_not_repeat_strategy_ids)
        excluded_route_ids = set(constraints.do_not_repeat_route_ids)
        excluded_capability_ids = set(constraints.do_not_repeat_capability_ids)
        preferred_surfaces = set(constraints.candidate_capability_surfaces)
        preferred_interaction_surfaces = set(constraints.candidate_interaction_surfaces)
        available_interaction_surfaces = set(getattr(environment, "available_interaction_surfaces", INTERACTION_SURFACES))
        for strategy in strategies:
            if strategy.strategy_id in excluded_strategy_ids:
                continue
            if strategy.route_id in excluded_route_ids:
                continue
            if any(capability_id in excluded_capability_ids for capability_id in strategy.capability_ids):
                continue
            if preferred_surfaces and strategy.capability_surface not in preferred_surfaces:
                continue
            if strategy.interaction_surface not in available_interaction_surfaces:
                excluded_by_environment = True
                continue
            if preferred_interaction_surfaces and strategy.interaction_surface not in preferred_interaction_surfaces:
                continue
            if strategy.requires_desktop_integration and not getattr(environment, "desktop_integration_available", False):
                excluded_by_environment = True
                continue
            if strategy.requires_network and getattr(environment, "connectivity_limitations", "") == "offline":
                excluded_by_environment = True
                continue
            score = strategy.priority + interaction_surface_score(strategy.interaction_surface) + _environment_bonus(strategy, environment)
            eligible.append((score, strategy))
        if not eligible:
            reason = "no eligible strategy remains after applying recovery constraints"
            failure_class = last_failure_class or "unsupported_request"
            if excluded_by_environment:
                failure_class = "environment_unreachable"
                reason = "no eligible strategy is available in the current environment"
            elif last_failure_class == "environment_unreachable":
                reason = "no eligible strategy is available in the current environment"
            return StrategyDecision(action="stop", reason=reason, failure_class=failure_class)
        eligible.sort(key=lambda item: (-item[0], item[1].strategy_id))
        selected = eligible[0][1]
        reason = "selected highest scoring strategy candidate"
        if last_failure_class != "none":
            reason = f"selected replacement strategy after {last_failure_class}"
        return StrategyDecision(action="select", reason=reason, selected_strategy_id=selected.strategy_id, failure_class=last_failure_class)

    def next_constraints(self, strategy: StrategyCandidate, failure_class: str) -> StrategyConstraint:
        candidate_surfaces: tuple[str, ...] = ()
        candidate_interaction_surfaces: tuple[InteractionSurface, ...] = ()
        exclude_capability_ids = strategy.capability_ids if failure_class == "semantic_mismatch" else ()
        if failure_class == "semantic_mismatch":
            if bool(strategy.metadata.get("enable_surface_downgrade", False)):
                candidate_interaction_surfaces = weaker_interaction_surfaces(strategy.interaction_surface)
                exclude_capability_ids = ()
            if strategy.capability_surface != "browser" and "app.open" in strategy.capability_ids:
                candidate_surfaces = ("browser",)
        return StrategyConstraint(
            do_not_repeat_strategy_ids=(strategy.strategy_id,),
            do_not_repeat_route_ids=(strategy.route_id,),
            do_not_repeat_capability_ids=exclude_capability_ids,
            candidate_capability_surfaces=candidate_surfaces,
            candidate_interaction_surfaces=candidate_interaction_surfaces,
        )


def _environment_bonus(strategy: StrategyCandidate, environment) -> float:
    bonus = 0.0
    if strategy.capability_surface == "browser" and getattr(environment, "search_policy", "") == "browser_first":
        bonus += 5.0
    if strategy.capability_surface == "desktop-linux" and getattr(environment, "desktop_integration_available", False):
        bonus += 1.0
    if strategy.capability_surface == "browser" and not getattr(environment, "desktop_integration_available", False):
        bonus += 2.0
    return bonus
