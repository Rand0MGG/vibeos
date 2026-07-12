from __future__ import annotations

from dataclasses import replace

from .capabilities import CAPABILITIES
from .task_models import DisplayFields, TaskPlan


def available_capabilities() -> set[str]:
    return set(CAPABILITIES)


def score_candidates(candidates: list[TaskPlan], capability_context: set[str] | None = None) -> list[TaskPlan]:
    available = available_capabilities() if capability_context is None else capability_context
    return [score_plan_candidate(candidate, available) for candidate in candidates]


def select_best_plan(candidates: list[TaskPlan], capability_context: set[str] | None = None) -> TaskPlan | None:
    if not candidates:
        return None
    available = available_capabilities() if capability_context is None else capability_context
    scored = score_candidates(candidates, available)
    satisfiable = [plan for plan in scored if route_is_satisfied(plan, available)]
    if not satisfiable:
        return None
    satisfiable.sort(key=lambda plan: route_sort_key(plan, available))
    return satisfiable[0]


def score_plan_candidate(plan: TaskPlan, available: set[str]) -> TaskPlan:
    route = plan.routes[0]
    required = set(route.required_capabilities)
    covered = len(required & available)
    total = len(required) or 1
    capability_coverage = covered / total
    max_risk = max((risk_rank(step.risk_level) for step in plan.steps), default=0)
    risk_penalty = max_risk / 4.0
    domain_match = 1.0
    precondition_score = capability_coverage
    clarification_penalty = 0.0 if not plan.needs_user_input else 1.0
    preference_bonus = route_preference_bonus(plan)
    score = (
        domain_match * 0.35
        + capability_coverage * 0.30
        + precondition_score * 0.20
        + preference_bonus * 0.05
        - risk_penalty * 0.10
        - clarification_penalty * 0.20
    )
    rescored_route = replace(
        route,
        score=round(score, 4),
        display=DisplayFields(
            goal=route.display.goal,
            explanation=route.display.explanation or "Route scored using deterministic v0.3 inputs.",
            assumptions=route.display.assumptions,
        ),
        score_inputs={
            "domain_match": round(domain_match, 4),
            "capability_coverage": round(capability_coverage, 4),
            "precondition_score": round(precondition_score, 4),
            "risk_penalty": round(risk_penalty, 4),
            "clarification_penalty": round(clarification_penalty, 4),
            "preference_bonus": round(preference_bonus, 4),
        },
    )
    return replace(plan, selected_route_id=rescored_route.id, routes=(rescored_route,))


def route_sort_key(plan: TaskPlan, available: set[str]) -> tuple[float, int, int, int, str]:
    route = plan.routes[0]
    max_risk = max((risk_rank(step.risk_level) for step in plan.steps), default=0)
    missing = len([cap for cap in route.required_capabilities if cap not in available])
    return (-route.score, max_risk, missing, len(plan.steps), route.id)


def route_is_satisfied(plan: TaskPlan, available: set[str]) -> bool:
    route = plan.routes[0]
    return all(capability in available for capability in route.required_capabilities)


def risk_rank(risk_level: str) -> int:
    return {"L0": 0, "L1": 1, "L2": 2, "L3": 3}.get(risk_level, 4)


def route_preference_bonus(plan: TaskPlan) -> float:
    if str(plan.provenance.get("fallback_from_domain") or ""):
        return -1.0
    if str(plan.provenance.get("domain_id") or "") == "app_interaction":
        interaction_surface = str(plan.provenance.get("interaction_surface") or "")
        if interaction_surface == "structured":
            return 1.0
        if interaction_surface == "shortcut":
            return 0.5
    if str(plan.provenance.get("domain_id") or "") == "media":
        return 1.0
    return 0.0
