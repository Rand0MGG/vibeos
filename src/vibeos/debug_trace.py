from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class DebugTrace:
    utterance_analysis: dict[str, Any] = field(default_factory=dict)
    goal_synthesis: dict[str, Any] = field(default_factory=dict)
    model_exchange: tuple[dict[str, Any], ...] = ()
    synthesis_constraints: tuple[str, ...] = ()
    route_competition: tuple[dict[str, Any], ...] = ()
    fallback_reasons: tuple[str, ...] = ()
    review: dict[str, Any] = field(default_factory=dict)
    execution: dict[str, Any] = field(default_factory=dict)
    acceptance: dict[str, Any] = field(default_factory=dict)


def build_debug_trace(
    *,
    utterance_analysis: dict[str, Any],
    goal_synthesis: dict[str, Any],
    model_exchange: tuple[dict[str, Any], ...],
    synthesis_constraints: tuple[str, ...],
    route_competition: tuple[dict[str, Any], ...],
    fallback_reasons: tuple[str, ...],
    review: dict[str, Any] | None = None,
    execution: dict[str, Any] | None = None,
    acceptance: dict[str, Any] | None = None,
) -> DebugTrace:
    return DebugTrace(
        utterance_analysis=utterance_analysis,
        goal_synthesis=goal_synthesis,
        model_exchange=model_exchange,
        synthesis_constraints=synthesis_constraints,
        route_competition=route_competition,
        fallback_reasons=fallback_reasons,
        review=review or {},
        execution=execution or {},
        acceptance=acceptance or {},
    )


def serialize_provider_exchange(payload: dict[str, Any], *, include_raw: bool) -> dict[str, Any]:
    normalized = dict(payload)
    raw_output = normalized.pop("raw_output", None)
    if include_raw:
        normalized["raw_output"] = redact_and_limit(raw_output)
    return normalized


def redact_and_limit(raw_output: Any, max_chars: int = 4000) -> str | None:
    if raw_output in {None, ""}:
        return None
    if isinstance(raw_output, str):
        text = raw_output
    else:
        text = json.dumps(raw_output, ensure_ascii=False)
    text = text.replace("\r", "")
    if len(text) > max_chars:
        return text[:max_chars] + "...<truncated>"
    return text
