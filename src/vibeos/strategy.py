from __future__ import annotations

from dataclasses import asdict, dataclass, field, replace
from hashlib import sha256
import json
import urllib.error
from typing import Literal

from .assistant_semantics import INTERACTION_SURFACES, InteractionSurface, interaction_surface_score, weaker_interaction_surfaces
from .models import utc_now_iso
from .provider_client import env_flag_enabled, load_openai_compatible_provider_config, request_json_object
from .task_models import TaskPlan
from .task_trace import record_model_io


StrategySelectionAction = Literal["select", "stop", "ask_user"]
CapabilitySurface = Literal["workspace-local", "shell-local", "browser", "desktop-linux", "unknown"]
CAPABILITY_SURFACES: tuple[CapabilitySurface, ...] = ("workspace-local", "shell-local", "browser", "desktop-linux", "unknown")
STRATEGY_SELECTION_ACTIONS: tuple[StrategySelectionAction, ...] = ("select", "stop", "ask_user")

STRATEGY_SELECTION_SYSTEM_PROMPT = """You are VibeOS's bounded strategy selector.
Choose exactly one response as JSON only.
You may only choose among the provided eligible strategy ids.
Never invent a new strategy id, route, tool, capability, or authority.
If no provided strategy should run, return action "stop" or "ask_user".
Schema:
{
  "action": "select",
  "selected_strategy_id": "strategy_id_here",
  "reason": "short explanation"
}
Return JSON only."""


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
    strategy_decision_id: str = ""
    provider_name: str = "provider"
    model_name: str = "structured"
    parse_valid: bool = True
    fallback_used: bool = False
    error: str | None = None


@dataclass(frozen=True)
class StrategySelectionResult:
    decision: StrategyDecision
    request_payload: dict[str, object] | None = None
    response_payload: object | None = None


class StrategySelectionProvider:
    provider_name = "provider"
    model_name = "structured"

    def decide(
        self,
        *,
        utterance: str,
        eligible: tuple[tuple[float, StrategyCandidate], ...],
        constraints: StrategyConstraint,
        environment,
        attempts,
        last_failure_class: str,
    ) -> StrategySelectionResult:
        raise NotImplementedError


class DeterministicStrategySelectionProvider(StrategySelectionProvider):
    provider_name = "host_strategy_selector"
    model_name = "deterministic-local"

    def decide(
        self,
        *,
        utterance: str,
        eligible: tuple[tuple[float, StrategyCandidate], ...],
        constraints: StrategyConstraint,
        environment,
        attempts,
        last_failure_class: str,
    ) -> StrategySelectionResult:
        selected = eligible[0][1]
        reason = "selected highest scoring strategy candidate"
        if last_failure_class != "none":
            reason = f"selected replacement strategy after {last_failure_class}"
        decision = make_strategy_decision(
            action="select",
            reason=reason,
            selected_strategy_id=selected.strategy_id,
            constraints=constraints,
            failure_class=last_failure_class,
            provider_name=self.provider_name,
            model_name=self.model_name,
        )
        return StrategySelectionResult(
            decision=decision,
            request_payload=build_strategy_selection_request_payload(
                utterance=utterance,
                eligible=eligible,
                constraints=constraints,
                environment=environment,
                attempts=attempts,
                last_failure_class=last_failure_class,
            ),
            response_payload={"action": decision.action, "selected_strategy_id": decision.selected_strategy_id, "reason": decision.reason},
        )


class OpenAICompatibleStrategySelectionProvider(StrategySelectionProvider):
    def __init__(self, fallback: StrategySelectionProvider | None = None) -> None:
        self.config = load_openai_compatible_provider_config()
        self.provider_name = self.config.provider_name
        self.model_name = self.config.model_name or "unknown-model"
        self.fallback = fallback or DeterministicStrategySelectionProvider()

    def decide(
        self,
        *,
        utterance: str,
        eligible: tuple[tuple[float, StrategyCandidate], ...],
        constraints: StrategyConstraint,
        environment,
        attempts,
        last_failure_class: str,
    ) -> StrategySelectionResult:
        request_payload = build_strategy_selection_request_payload(
            utterance=utterance,
            eligible=eligible,
            constraints=constraints,
            environment=environment,
            attempts=attempts,
            last_failure_class=last_failure_class,
        )
        if not self.config.configured or not model_guidance_enabled("VIBEOS_ENABLE_MODEL_STRATEGY_SELECTION"):
            return self._fallback_result(
                request_payload=request_payload,
                utterance=utterance,
                eligible=eligible,
                constraints=constraints,
                environment=environment,
                attempts=attempts,
                last_failure_class=last_failure_class,
                error="missing_api_key_or_model_or_guidance_disabled",
            )

        try:
            response = request_json_object(
                config=self.config,
                system_prompt=STRATEGY_SELECTION_SYSTEM_PROMPT,
                user_content=json.dumps(request_payload, ensure_ascii=False),
                max_tokens=384,
                purpose="strategy_selection",
            )
            decision = parse_strategy_selection_response(
                json.dumps(response.parsed_object, ensure_ascii=False),
                allowed_strategy_ids={candidate.strategy_id for _, candidate in eligible},
                constraints=constraints,
                failure_class=last_failure_class,
                provider_name=self.provider_name,
                model_name=self.model_name,
            )
            return StrategySelectionResult(
                decision=decision,
                request_payload=response.request_payload,
                response_payload=response.response_payload,
            )
        except (urllib.error.URLError, TimeoutError, KeyError, ValueError, json.JSONDecodeError) as exc:
            return self._fallback_result(
                request_payload=request_payload,
                utterance=utterance,
                eligible=eligible,
                constraints=constraints,
                environment=environment,
                attempts=attempts,
                last_failure_class=last_failure_class,
                error=str(exc),
            )

    def _fallback_result(
        self,
        *,
        request_payload: dict[str, object],
        utterance: str,
        eligible: tuple[tuple[float, StrategyCandidate], ...],
        constraints: StrategyConstraint,
        environment,
        attempts,
        last_failure_class: str,
        error: str,
    ) -> StrategySelectionResult:
        fallback = self.fallback.decide(
            utterance=utterance,
            eligible=eligible,
            constraints=constraints,
            environment=environment,
            attempts=attempts,
            last_failure_class=last_failure_class,
        )
        return StrategySelectionResult(
            decision=replace(
                fallback.decision,
                provider_name=self.provider_name,
                model_name=self.model_name,
                fallback_used=True,
                error=error,
                strategy_decision_id=make_strategy_decision_id(
                    fallback.decision.action,
                    fallback.decision.selected_strategy_id,
                    fallback.decision.reason,
                    provider_name=self.provider_name,
                    model_name=self.model_name,
                ),
            ),
            request_payload=request_payload,
            response_payload=fallback.response_payload,
        )


class RecoveryPolicy:
    def __init__(self, provider: StrategySelectionProvider | None = None) -> None:
        if provider is not None:
            self.provider = provider
        else:
            self.provider = OpenAICompatibleStrategySelectionProvider()

    def select_strategy(
        self,
        *,
        utterance: str = "",
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
            return make_strategy_decision(
                action="stop",
                reason=reason,
                constraints=constraints,
                failure_class=failure_class,
                provider_name=DeterministicStrategySelectionProvider.provider_name,
                model_name=DeterministicStrategySelectionProvider.model_name,
            )
        eligible.sort(key=lambda item: (-item[0], item[1].strategy_id))
        selection = self.provider.decide(
            utterance=utterance,
            eligible=tuple(eligible),
            constraints=constraints,
            environment=environment,
            attempts=attempts,
            last_failure_class=last_failure_class,
        )
        record_model_io(
            phase="routing",
            provider=selection.decision.provider_name,
            model=selection.decision.model_name,
            request_payload=selection.request_payload,
            response_payload=selection.response_payload,
            normalized_output=asdict(selection.decision),
            parse_valid=selection.decision.parse_valid,
            fallback_used=selection.decision.fallback_used,
            error=selection.decision.error,
            actor="strategy_selector",
            call_kind="structured_followup",
            consumed_artifacts={
                "goal_id": eligible[0][1].goal_id if eligible else None,
                "strategy_ids": [candidate.strategy_id for _, candidate in eligible],
            },
        )
        return selection.decision

    def next_constraints(self, strategy: StrategyCandidate, failure_class: str) -> StrategyConstraint:
        candidate_interaction_surfaces: tuple[InteractionSurface, ...] = ()
        exclude_capability_ids = strategy.capability_ids if failure_class == "semantic_mismatch" else ()
        if failure_class == "semantic_mismatch":
            if bool(strategy.metadata.get("enable_surface_downgrade", False)):
                candidate_interaction_surfaces = weaker_interaction_surfaces(strategy.interaction_surface)
                exclude_capability_ids = ()
        return StrategyConstraint(
            do_not_repeat_strategy_ids=(strategy.strategy_id,),
            do_not_repeat_route_ids=(strategy.route_id,),
            do_not_repeat_capability_ids=exclude_capability_ids,
            candidate_capability_surfaces=(),
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


def model_guidance_enabled(env_name: str) -> bool:
    return env_flag_enabled(env_name)


def make_strategy_decision(
    *,
    action: StrategySelectionAction,
    reason: str,
    selected_strategy_id: str | None = None,
    constraints: StrategyConstraint | None = None,
    failure_class: str = "none",
    provider_name: str,
    model_name: str,
    parse_valid: bool = True,
    fallback_used: bool = False,
    error: str | None = None,
) -> StrategyDecision:
    return StrategyDecision(
        action=action,
        reason=reason,
        selected_strategy_id=selected_strategy_id,
        constraints=constraints or StrategyConstraint(),
        failure_class=failure_class,
        strategy_decision_id=make_strategy_decision_id(action, selected_strategy_id, reason, provider_name=provider_name, model_name=model_name),
        provider_name=provider_name,
        model_name=model_name,
        parse_valid=parse_valid,
        fallback_used=fallback_used,
        error=error,
    )


def make_strategy_decision_id(
    action: StrategySelectionAction,
    selected_strategy_id: str | None,
    reason: str,
    *,
    provider_name: str,
    model_name: str,
) -> str:
    digest = sha256(f"{action}:{selected_strategy_id}:{reason}:{provider_name}:{model_name}:{utc_now_iso()}".encode("utf-8")).hexdigest()[:12]
    return f"sdec_{digest}"


def build_strategy_selection_request_payload(
    *,
    utterance: str,
    eligible: tuple[tuple[float, StrategyCandidate], ...],
    constraints: StrategyConstraint,
    environment,
    attempts,
    last_failure_class: str,
) -> dict[str, object]:
    return {
        "utterance": utterance,
        "last_failure_class": last_failure_class,
        "constraints": asdict(constraints),
        "environment": {
            "platform": getattr(environment, "platform", "unknown"),
            "transport_mode": getattr(environment, "transport_mode", "unknown"),
            "desktop_integration_available": bool(getattr(environment, "desktop_integration_available", False)),
            "connectivity_limitations": getattr(environment, "connectivity_limitations", "unknown"),
            "deployment_profile": getattr(environment, "deployment_profile", "unknown"),
            "search_policy": getattr(environment, "search_policy", "balanced"),
            "available_interaction_surfaces": list(getattr(environment, "available_interaction_surfaces", INTERACTION_SURFACES)),
        },
        "attempt_count": len(attempts),
        "eligible_strategies": [
            {
                "strategy_id": candidate.strategy_id,
                "title": candidate.title,
                "route_id": candidate.route_id,
                "capability_surface": candidate.capability_surface,
                "interaction_surface": candidate.interaction_surface,
                "priority": candidate.priority,
                "host_score": score,
                "required_capability_ids": list(candidate.capability_ids),
                "tool_ids": list(candidate.tool_ids),
                "metadata": dict(candidate.metadata),
            }
            for score, candidate in eligible
        ],
    }


def parse_strategy_selection_response(
    raw_content: str,
    *,
    allowed_strategy_ids: set[str],
    constraints: StrategyConstraint,
    failure_class: str,
    provider_name: str,
    model_name: str,
) -> StrategyDecision:
    parsed = json.loads(raw_content)
    if not isinstance(parsed, dict):
        raise ValueError("strategy selection payload must be an object")
    action = str(parsed.get("action") or "").strip()
    if action not in STRATEGY_SELECTION_ACTIONS:
        raise ValueError("strategy selection action is invalid")
    reason = parsed.get("reason")
    if not isinstance(reason, str) or not reason.strip():
        raise ValueError("strategy selection reason is required")
    selected_strategy_id = parsed.get("selected_strategy_id")
    if action == "select":
        if not isinstance(selected_strategy_id, str) or selected_strategy_id not in allowed_strategy_ids:
            raise ValueError("selected_strategy_id must be one of the eligible candidates")
    else:
        selected_strategy_id = None
    return make_strategy_decision(
        action=action,
        reason=reason.strip(),
        selected_strategy_id=selected_strategy_id,
        constraints=constraints,
        failure_class=failure_class,
        provider_name=provider_name,
        model_name=model_name,
        parse_valid=True,
        fallback_used=False,
        error=None,
    )
