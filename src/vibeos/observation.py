from __future__ import annotations

import json
from typing import Any

from .domain_models import CapabilityExposure, ObservationReceipt, ObservationRequest, ResolvedContextPackage
from .domain_registry import DomainRegistry
from .verifiers import VerifierHarness


def validate_observation_request(request: ObservationRequest, registry: DomainRegistry) -> tuple[str, ...]:
    errors: list[str] = []
    active_domain_ids = set(request.active_domain_ids)
    allowed_package_ids: set[str] = set()
    for domain_id in request.active_domain_ids:
        pack = registry.get_pack(domain_id)
        if pack is None:
            errors.append(f"unknown active domain {domain_id!r}")
            continue
        allowed_package_ids.update(pack.allowed_context_package_ids)

    seen_requested: set[str] = set()
    for package_id in request.requested_context_package_ids:
        if package_id in seen_requested:
            errors.append(f"duplicate context package id {package_id!r}")
        seen_requested.add(package_id)
        if registry.context_registry.get(package_id) is None:
            errors.append(f"unknown context package id {package_id!r}")
        elif active_domain_ids and package_id not in allowed_package_ids:
            errors.append(f"context package {package_id!r} is not allowed for active domains {sorted(active_domain_ids)!r}")

    seen_postconditions: set[str] = set()
    for package_id in request.postcondition_package_ids:
        if package_id in seen_postconditions:
            errors.append(f"duplicate context package id {package_id!r}")
        seen_postconditions.add(package_id)
        if registry.context_registry.get(package_id) is None:
            errors.append(f"unknown context package id {package_id!r}")
        elif active_domain_ids and package_id not in allowed_package_ids:
            errors.append(f"context package {package_id!r} is not allowed for active domains {sorted(active_domain_ids)!r}")
    return tuple(errors)


def resolve_observation_request(request: ObservationRequest, registry: DomainRegistry) -> ObservationReceipt:
    request_errors = list(validate_observation_request(request, registry))
    if request_errors:
        return ObservationReceipt(
            requested_package_ids=request.requested_context_package_ids,
            loaded_package_ids=(),
            skipped_package_ids=request.requested_context_package_ids,
            packages=(),
            errors=tuple(request_errors),
        )

    packages: list[ResolvedContextPackage] = []
    loaded_package_ids: list[str] = []
    skipped_package_ids: list[str] = []
    warnings: list[str] = []
    for package_id in request.requested_context_package_ids:
        definition = registry.context_registry.get(package_id)
        if definition is None:
            skipped_package_ids.append(package_id)
            warnings.append(f"skipped unknown package {package_id!r}")
            continue
        raw_payload = definition.producer()
        resolved = apply_context_budget(package_id, raw_payload, definition.budget)
        packages.append(resolved)
        if resolved.status == "loaded":
            loaded_package_ids.append(package_id)
        else:
            skipped_package_ids.append(package_id)
        warnings.extend(resolved.warnings)
    return ObservationReceipt(
        requested_package_ids=request.requested_context_package_ids,
        loaded_package_ids=tuple(loaded_package_ids),
        skipped_package_ids=tuple(skipped_package_ids),
        packages=tuple(packages),
        warnings=tuple(warnings),
        errors=(),
    )


def apply_context_budget(package_id: str, payload: dict[str, Any], budget) -> ResolvedContextPackage:
    materialized = dict(payload) if isinstance(payload, dict) else {"value": payload}
    warnings: list[str] = []
    redacted_fields: list[str] = []
    for field_name in budget.sensitive_fields:
        if field_name in materialized and materialized[field_name] not in (None, "", (), []):
            materialized[field_name] = "[redacted]"
            redacted_fields.append(field_name)
    truncated = False
    for key, value in list(materialized.items()):
        if isinstance(value, (list, tuple)) and budget.max_items and len(value) > budget.max_items:
            materialized[key] = list(value[: budget.max_items])
            truncated = True
            warnings.append(f"{package_id}:{key} truncated to {budget.max_items} items")
    payload_bytes = len(json.dumps(materialized, ensure_ascii=False).encode("utf-8"))
    if budget.max_bytes and payload_bytes > budget.max_bytes:
        materialized = {"status": "truncated", "package_id": package_id}
        truncated = True
        payload_bytes = len(json.dumps(materialized, ensure_ascii=False).encode("utf-8"))
        warnings.append(f"{package_id} exceeded {budget.max_bytes} bytes and was truncated")
    status = str(payload.get("status", "loaded")) if isinstance(payload, dict) else "loaded"
    if status not in {"loaded", "unavailable", "stale"}:
        status = "loaded"
    return ResolvedContextPackage(
        package_id=package_id,
        status=status,
        payload=materialized,
        payload_bytes=payload_bytes,
        freshness_ts=str(materialized.get("captured_at") or ""),
        truncated=truncated,
        redacted_fields=tuple(redacted_fields),
        warnings=tuple(warnings),
        errors=(),
    )


def build_capability_exposure(
    registry: DomainRegistry,
    active_domain_ids: tuple[str, ...],
    observation_receipt: ObservationReceipt,
) -> CapabilityExposure:
    all_domain_ids = tuple(pack.domain_id for pack in registry.packs())
    routes = registry.routes_for_domains(active_domain_ids)
    exposed_route_ids = tuple(route.route_id for route in routes)
    exposed_capability_ids = tuple(capability_id for route in routes for capability_id in route.required_capability_ids)
    unique_capability_ids = tuple(dict.fromkeys(exposed_capability_ids))
    exposed_context_package_ids = observation_receipt.loaded_package_ids
    hidden_domain_ids = tuple(domain_id for domain_id in all_domain_ids if domain_id not in active_domain_ids)
    hidden_route_ids = tuple(route.route_id for route in registry.routes_for_domains(all_domain_ids) if route.route_id not in exposed_route_ids)
    return CapabilityExposure(
        active_domain_ids=active_domain_ids,
        exposed_route_ids=exposed_route_ids,
        exposed_capability_ids=unique_capability_ids,
        exposed_context_package_ids=exposed_context_package_ids,
        hidden_domain_ids=hidden_domain_ids,
        hidden_route_ids=hidden_route_ids,
        hidden_counts={
            "domains": len(hidden_domain_ids),
            "routes": len(hidden_route_ids),
            "context_packages": max(0, len(registry.context_registry.ids()) - len(exposed_context_package_ids)),
        },
    )


def planner_context_payload(receipt: ObservationReceipt, exposure: CapabilityExposure) -> dict[str, Any]:
    visible = {package.package_id: package.payload for package in receipt.packages if package.package_id in exposure.exposed_context_package_ids}
    return {
        "packages": visible,
        "loaded_package_ids": list(receipt.loaded_package_ids),
        "warnings": list(receipt.warnings),
    }


def resolve_post_execution_observation(
    request: ObservationRequest,
    registry: DomainRegistry,
    harness: VerifierHarness | None = None,
) -> ObservationReceipt:
    request_errors = list(validate_observation_request(request, registry))
    if request_errors:
        return ObservationReceipt(
            requested_package_ids=request.postcondition_package_ids,
            loaded_package_ids=(),
            skipped_package_ids=request.postcondition_package_ids,
            packages=(),
            errors=tuple(request_errors),
        )

    active_harness = harness or VerifierHarness()
    packages: list[ResolvedContextPackage] = []
    loaded_package_ids: list[str] = []
    skipped_package_ids: list[str] = []
    warnings: list[str] = []
    for package_id in request.postcondition_package_ids:
        definition = registry.context_registry.get(package_id)
        if definition is None:
            skipped_package_ids.append(package_id)
            warnings.append(f"skipped unknown package {package_id!r}")
            continue
        raw_payload = active_harness.context_package_for(package_id) or definition.producer()
        resolved = apply_context_budget(package_id, raw_payload, definition.budget)
        packages.append(resolved)
        if resolved.status == "loaded":
            loaded_package_ids.append(package_id)
        else:
            skipped_package_ids.append(package_id)
        warnings.extend(resolved.warnings)
    return ObservationReceipt(
        requested_package_ids=request.postcondition_package_ids,
        loaded_package_ids=tuple(loaded_package_ids),
        skipped_package_ids=tuple(skipped_package_ids),
        packages=tuple(packages),
        warnings=tuple(warnings),
        errors=(),
    )
