from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Literal

from .strategy import CAPABILITY_SURFACES, CapabilitySurface

ToolFamily = Literal["action", "resolver", "observer", "verifier", "wait_poll", "environment"]
ToolStatus = Literal["succeeded", "failed", "blocked", "unavailable"]


@dataclass(frozen=True)
class ToolExecutionContext:
    session_id: str
    goal_id: str
    turn_id: str
    attempt_id: str
    strategy_id: str
    environment: Any
    state: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ToolResult:
    status: ToolStatus
    message: str = ""
    output: dict[str, Any] = field(default_factory=dict)
    evidence: dict[str, Any] = field(default_factory=dict)
    state_updates: dict[str, Any] = field(default_factory=dict)
    failure_class: str = "none"
    accepted: bool | None = None


ToolRunner = Callable[[dict[str, Any], ToolExecutionContext], ToolResult]
AvailabilityChecker = Callable[[Any], bool]


@dataclass(frozen=True)
class ToolSpec:
    tool_id: str
    family: ToolFamily
    capability_surface: CapabilitySurface
    runner: ToolRunner
    availability: AvailabilityChecker | None = None

    def __post_init__(self) -> None:
        if self.capability_surface not in CAPABILITY_SURFACES:
            raise ValueError(f"unsupported capability surface: {self.capability_surface}")

    def is_available(self, environment: Any) -> bool:
        if self.availability is None:
            return True
        return bool(self.availability(environment))


@dataclass(frozen=True)
class ToolInvocationEnvelope:
    tool_id: str
    family: ToolFamily
    capability_surface: CapabilitySurface
    strategy_id: str
    attempt_id: str
    status: ToolStatus
    message: str = ""
    input_payload: dict[str, Any] = field(default_factory=dict)
    output_payload: dict[str, Any] = field(default_factory=dict)
    evidence: dict[str, Any] = field(default_factory=dict)
    failure_class: str = "none"


class ToolRegistry:
    def __init__(self, specs: tuple[ToolSpec, ...]) -> None:
        self._specs = {spec.tool_id: spec for spec in specs}

    def get(self, tool_id: str) -> ToolSpec | None:
        return self._specs.get(tool_id)

    def ids(self) -> tuple[str, ...]:
        return tuple(sorted(self._specs))

    def is_available(self, tool_id: str, environment: Any) -> bool:
        spec = self.get(tool_id)
        if spec is None:
            return False
        return spec.is_available(environment)

    def invoke(self, tool_id: str, payload: dict[str, Any], context: ToolExecutionContext) -> tuple[ToolInvocationEnvelope, ToolResult]:
        recorded_input = _redacted_input_payload(payload)
        spec = self.get(tool_id)
        if spec is None:
            result = ToolResult(status="unavailable", message="tool is not registered", failure_class="unsupported_request")
            return (
                ToolInvocationEnvelope(
                    tool_id=tool_id,
                    family="environment",
                    capability_surface="unknown",
                    strategy_id=context.strategy_id,
                    attempt_id=context.attempt_id,
                    status=result.status,
                    message=result.message,
                    input_payload=recorded_input,
                    output_payload=result.output,
                    evidence=result.evidence,
                    failure_class=result.failure_class,
                ),
                result,
            )
        if not spec.is_available(context.environment):
            result = ToolResult(
                status="unavailable",
                message="tool is unavailable in the current environment",
                failure_class="environment_unreachable",
            )
            return (
                ToolInvocationEnvelope(
                    tool_id=tool_id,
                    family=spec.family,
                    capability_surface=spec.capability_surface,
                    strategy_id=context.strategy_id,
                    attempt_id=context.attempt_id,
                    status=result.status,
                    message=result.message,
                    input_payload=recorded_input,
                    output_payload=result.output,
                    evidence=result.evidence,
                    failure_class=result.failure_class,
                ),
                result,
            )
        result = spec.runner(dict(payload), context)
        return (
            ToolInvocationEnvelope(
                tool_id=tool_id,
                family=spec.family,
                capability_surface=spec.capability_surface,
                strategy_id=context.strategy_id,
                attempt_id=context.attempt_id,
                status=result.status,
                message=result.message,
                input_payload=recorded_input,
                output_payload=result.output,
                evidence=result.evidence,
                failure_class=result.failure_class,
            ),
            result,
        )


_USER_CONTENT_KEYS = {"body", "content", "message", "supplemental_input", "text"}
_SECRET_KEYS = {"api_key", "authorization", "credential", "password", "secret", "token"}


def _redacted_input_payload(payload: dict[str, Any]) -> dict[str, Any]:
    sanitized: dict[str, Any] = {}
    for key, value in payload.items():
        normalized = key.lower()
        if normalized in _USER_CONTENT_KEYS or any(token in normalized for token in _SECRET_KEYS):
            sanitized[key] = "[REDACTED]"
        else:
            sanitized[key] = value
    return sanitized
