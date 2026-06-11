from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal


AssistantObjectiveKind = Literal[
    "open_url",
    "open_named_website",
    "open_application",
    "in_app_search",
    "search_web",
    "generic",
]
AssistantCompletionKind = Literal["page_identity", "target_presence", "application_state", "search_results"]
InteractionSurface = Literal["native_action", "structured_ui_action", "computer_use_action"]

INTERACTION_SURFACES: tuple[InteractionSurface, ...] = (
    "native_action",
    "structured_ui_action",
    "computer_use_action",
)
INTERACTION_SURFACE_SCORES: dict[InteractionSurface, float] = {
    "native_action": 3.0,
    "structured_ui_action": 2.0,
    "computer_use_action": 1.0,
}


@dataclass(frozen=True)
class AssistantIntentTarget:
    entity_type: str
    display_name: str
    canonical_identifier: str | None = None
    app_name: str | None = None
    query_text: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class AssistantCompletionSemantics:
    kind: AssistantCompletionKind
    success_signal: str
    requires_follow_up_navigation: bool = False
    allows_intermediate_success: bool = False


@dataclass(frozen=True)
class AssistantIntent:
    objective_kind: AssistantObjectiveKind
    target: AssistantIntentTarget
    completion: AssistantCompletionSemantics
    interaction_hints: tuple[str, ...] = ()
    preferred_domains: tuple[str, ...] = ()


def interaction_surface_score(surface: InteractionSurface) -> float:
    return INTERACTION_SURFACE_SCORES[surface]


def weaker_interaction_surfaces(surface: InteractionSurface) -> tuple[InteractionSurface, ...]:
    if surface == "native_action":
        return ("structured_ui_action", "computer_use_action")
    if surface == "structured_ui_action":
        return ("computer_use_action",)
    return ()


def assistant_intent_to_payload(intent: AssistantIntent | None) -> dict[str, Any] | None:
    if intent is None:
        return None
    return asdict(intent)


def assistant_intent_from_payload(payload: dict[str, Any] | None) -> AssistantIntent | None:
    if not isinstance(payload, dict):
        return None
    target_payload = payload.get("target")
    completion_payload = payload.get("completion")
    if not isinstance(target_payload, dict) or not isinstance(completion_payload, dict):
        return None
    return AssistantIntent(
        objective_kind=str(payload.get("objective_kind", "generic")),
        target=AssistantIntentTarget(
            entity_type=str(target_payload.get("entity_type", "")),
            display_name=str(target_payload.get("display_name", "")),
            canonical_identifier=_optional_text(target_payload.get("canonical_identifier")),
            app_name=_optional_text(target_payload.get("app_name")),
            query_text=_optional_text(target_payload.get("query_text")),
            metadata=target_payload.get("metadata", {}) if isinstance(target_payload.get("metadata"), dict) else {},
        ),
        completion=AssistantCompletionSemantics(
            kind=str(completion_payload.get("kind", "search_results")),
            success_signal=str(completion_payload.get("success_signal", "")),
            requires_follow_up_navigation=bool(completion_payload.get("requires_follow_up_navigation", False)),
            allows_intermediate_success=bool(completion_payload.get("allows_intermediate_success", False)),
        ),
        interaction_hints=tuple(str(item) for item in payload.get("interaction_hints", ())),
        preferred_domains=tuple(str(item) for item in payload.get("preferred_domains", ())),
    )


def _optional_text(value: Any) -> str | None:
    if isinstance(value, str):
        stripped = value.strip()
        if stripped:
            return stripped
    return None
