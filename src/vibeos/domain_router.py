from __future__ import annotations

from .domain_models import DomainRoutingResult, ObservationRequest
from .domain_registry import DomainRegistry
from .task_models import UtteranceAnalysis


def route_domains(
    analysis: UtteranceAnalysis,
    registry: DomainRegistry,
    candidate_domain_ids: tuple[str, ...] | None = None,
) -> DomainRoutingResult | None:
    if analysis.type not in {"task", "mixed", "clarification"}:
        return None

    active_candidates = candidate_domain_ids or tuple(domain_id for domain_id in analysis.domains if registry.get_pack(domain_id) is not None)
    if not active_candidates and analysis.type == "clarification":
        active_candidates = ("media",)
    if not active_candidates:
        return None

    if analysis.type == "clarification":
        pack = registry.get_pack(active_candidates[0])
        observation_request = ObservationRequest(
            active_domain_ids=(pack.domain_id,) if pack else (),
            requested_context_package_ids=pack.allowed_context_package_ids[:1] if pack else (),
            postcondition_package_ids=(),
        )
        return DomainRoutingResult(
            candidate_domain_ids=active_candidates,
            active_domain_ids=(pack.domain_id,) if pack else (),
            fallback_domain_ids=(),
            clarification_needed=True,
            reason=analysis.explanation,
            observation_request=observation_request,
        )

    active_domain_ids = active_candidates
    fallback_domain_ids: tuple[str, ...] = ()
    if "media" in active_candidates:
        media_pack = registry.get_pack("media")
        fallback_domain_ids = media_pack.optional_fallback_domain_ids if media_pack else ()
        active_domain_ids = ("media",) + tuple(domain_id for domain_id in fallback_domain_ids if domain_id != "media")
    elif len(active_candidates) > 1:
        active_domain_ids = active_candidates[:1]

    requested_context_package_ids: list[str] = []
    for domain_id in active_domain_ids:
        pack = registry.get_pack(domain_id)
        if pack is None:
            continue
        for package_id in pack.allowed_context_package_ids:
            if package_id not in requested_context_package_ids:
                requested_context_package_ids.append(package_id)

    observation_request = ObservationRequest(
        active_domain_ids=tuple(active_domain_ids),
        requested_context_package_ids=tuple(requested_context_package_ids),
        postcondition_package_ids=tuple(requested_context_package_ids),
    )
    return DomainRoutingResult(
        candidate_domain_ids=active_candidates,
        active_domain_ids=tuple(active_domain_ids),
        fallback_domain_ids=fallback_domain_ids,
        clarification_needed=False,
        reason=analysis.explanation,
        observation_request=observation_request,
    )
