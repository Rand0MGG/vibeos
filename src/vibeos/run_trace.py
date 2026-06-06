from __future__ import annotations

from dataclasses import asdict
from typing import Any

from .domain_models import RunTrace


def build_run_trace(
    *,
    utterance_analysis: Any,
    goal_synthesis: Any = None,
    domain_routing: Any = None,
    observation_request: Any = None,
    observation_receipt: Any = None,
    capability_exposure: Any = None,
    candidate_plan_selection: Any = None,
    selected_route: Any = None,
    validation: Any = None,
    review: Any = None,
    execution: Any = None,
    verification: Any = None,
    acceptance: Any = None,
    debug_trace_id: str | None = None,
) -> RunTrace:
    return RunTrace(
        utterance_analysis=serialize_trace_value(utterance_analysis),
        goal_synthesis=serialize_trace_value(goal_synthesis),
        domain_routing=serialize_trace_value(domain_routing),
        observation_request=serialize_trace_value(observation_request),
        observation_receipt=serialize_trace_value(observation_receipt),
        capability_exposure=serialize_trace_value(capability_exposure),
        candidate_plan_selection=serialize_trace_value(candidate_plan_selection),
        selected_route=serialize_trace_value(selected_route),
        validation=serialize_trace_value(validation),
        review=serialize_trace_value(review),
        execution=serialize_trace_value(execution),
        verification=serialize_trace_value(verification),
        acceptance=serialize_trace_value(acceptance),
        debug_trace_id=debug_trace_id,
    )


def serialize_trace_value(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, dict):
        return value
    return asdict(value)
