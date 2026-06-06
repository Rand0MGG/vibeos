from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


ContextPackageStatus = Literal["loaded", "unavailable", "stale", "skipped", "error"]
VerifierStatus = Literal["passed", "failed", "unavailable", "skipped"]

DOMAIN_SCHEMA_VERSION = "v0.4"


@dataclass(frozen=True)
class ContextBudget:
    schema_version: str = DOMAIN_SCHEMA_VERSION
    max_items: int = 0
    max_bytes: int = 0
    ttl_ms: int = 0
    redaction_policy: str = "none"
    sensitive_fields: tuple[str, ...] = ()


@dataclass(frozen=True)
class DomainPack:
    schema_version: str = DOMAIN_SCHEMA_VERSION
    domain_id: str = ""
    label: str = ""
    route_ids: tuple[str, ...] = ()
    allowed_context_package_ids: tuple[str, ...] = ()
    capability_families: tuple[str, ...] = ()
    policy_defaults: dict[str, str] = field(default_factory=dict)
    default_verifier_ids: tuple[str, ...] = ()
    optional_fallback_domain_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class ObservationRequest:
    schema_version: str = DOMAIN_SCHEMA_VERSION
    active_domain_ids: tuple[str, ...] = ()
    requested_context_package_ids: tuple[str, ...] = ()
    postcondition_package_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class ResolvedContextPackage:
    schema_version: str = DOMAIN_SCHEMA_VERSION
    package_id: str = ""
    status: ContextPackageStatus = "loaded"
    payload: dict[str, Any] = field(default_factory=dict)
    payload_bytes: int = 0
    freshness_ts: str | None = None
    truncated: bool = False
    redacted_fields: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    errors: tuple[str, ...] = ()


@dataclass(frozen=True)
class ObservationReceipt:
    schema_version: str = DOMAIN_SCHEMA_VERSION
    requested_package_ids: tuple[str, ...] = ()
    loaded_package_ids: tuple[str, ...] = ()
    skipped_package_ids: tuple[str, ...] = ()
    packages: tuple[ResolvedContextPackage, ...] = ()
    warnings: tuple[str, ...] = ()
    errors: tuple[str, ...] = ()


@dataclass(frozen=True)
class CapabilityExposure:
    schema_version: str = DOMAIN_SCHEMA_VERSION
    active_domain_ids: tuple[str, ...] = ()
    exposed_route_ids: tuple[str, ...] = ()
    exposed_capability_ids: tuple[str, ...] = ()
    exposed_context_package_ids: tuple[str, ...] = ()
    hidden_domain_ids: tuple[str, ...] = ()
    hidden_route_ids: tuple[str, ...] = ()
    hidden_counts: dict[str, int] = field(default_factory=dict)


@dataclass(frozen=True)
class DomainRoutingResult:
    schema_version: str = DOMAIN_SCHEMA_VERSION
    candidate_domain_ids: tuple[str, ...] = ()
    active_domain_ids: tuple[str, ...] = ()
    fallback_domain_ids: tuple[str, ...] = ()
    clarification_needed: bool = False
    reason: str = ""
    observation_request: ObservationRequest | None = None


@dataclass(frozen=True)
class VerifierResult:
    schema_version: str = DOMAIN_SCHEMA_VERSION
    verifier_id: str = ""
    status: VerifierStatus = "skipped"
    message: str = ""
    observation_package_ids: tuple[str, ...] = ()
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ScenarioFixture:
    schema_version: str = DOMAIN_SCHEMA_VERSION
    fixture_id: str = ""
    utterance: str = ""
    locale: str = ""
    expected_analysis_type: str = ""
    active_domain_ids: tuple[str, ...] = ()
    observation_request: ObservationRequest | None = None
    context_packages: tuple[ResolvedContextPackage, ...] = ()
    expected_exposed_route_ids: tuple[str, ...] = ()
    expected_selected_route_id: str = ""
    expected_validation_ok: bool = False
    expected_verifier_status: str = ""


@dataclass(frozen=True)
class RunTrace:
    schema_version: str = DOMAIN_SCHEMA_VERSION
    utterance_analysis: dict[str, Any] = field(default_factory=dict)
    goal_synthesis: dict[str, Any] = field(default_factory=dict)
    domain_routing: dict[str, Any] = field(default_factory=dict)
    observation_request: dict[str, Any] = field(default_factory=dict)
    observation_receipt: dict[str, Any] = field(default_factory=dict)
    capability_exposure: dict[str, Any] = field(default_factory=dict)
    candidate_plan_selection: dict[str, Any] = field(default_factory=dict)
    selected_route: dict[str, Any] = field(default_factory=dict)
    validation: dict[str, Any] = field(default_factory=dict)
    review: dict[str, Any] = field(default_factory=dict)
    execution: dict[str, Any] = field(default_factory=dict)
    verification: dict[str, Any] = field(default_factory=dict)
    acceptance: dict[str, Any] = field(default_factory=dict)
    debug_trace_id: str | None = None
