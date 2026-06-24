from __future__ import annotations

import os

from .loop_models import LoopPolicy, LoopState, ObservationLevel


TRUTHY_ENV_VALUES = {"1", "true", "yes", "on"}


def goal_loop_enabled() -> bool:
    raw = os.environ.get("VIBEOS_ENABLE_GOAL_LOOP", "0").strip().lower()
    return raw in TRUTHY_ENV_VALUES


def default_loop_policy() -> LoopPolicy:
    return LoopPolicy()


def loop_budget_exhausted(state: LoopState, policy: LoopPolicy) -> bool:
    return state.step_count >= policy.max_steps or state.attempt_count >= policy.max_attempts


def next_observation_level(state: LoopState, policy: LoopPolicy, *, escalate: bool) -> ObservationLevel:
    if not policy.observation_escalation_enabled or not escalate:
        return state.observation_level
    if state.observation_level == "L0":
        return "L1"
    if state.observation_level == "L1":
        return "L2"
    return "L2"
