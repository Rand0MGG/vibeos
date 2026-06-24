from __future__ import annotations

from dataclasses import asdict
from hashlib import sha256
from typing import Any

from .domain_registry import default_domain_registry
from .loop_models import LoopObservation, ObservationLevel
from .models import utc_now_iso
from .observation import resolve_post_execution_observation
from .task_models import TaskPlan, TaskStep
from .verifiers import VerifierHarness, VerifierRegistry

VOLATILE_OBSERVATION_KEYS = {"attempt_id", "captured_at", "freshness_ts", "run_id"}


class ObservationService:
    def __init__(self, verifier_registry: VerifierRegistry, harness: VerifierHarness | None = None) -> None:
        self.verifier_registry = verifier_registry
        self.harness = harness or VerifierHarness()

    def observe(
        self,
        *,
        plan: TaskPlan,
        step: TaskStep | None,
        phase: str,
        level: ObservationLevel,
    ) -> LoopObservation:
        registry = default_domain_registry(self.verifier_registry.ids())
        route = registry.get_route(plan.selected_route_id)
        package_ids = route.required_context_package_ids if route is not None else ()
        receipt = resolve_post_execution_observation(
            request=_observation_request(plan, package_ids),
            registry=registry,
            harness=self.harness,
        )
        packages = {package.package_id: dict(package.payload) for package in receipt.packages}
        observation_id = _make_observation_id(
            plan.plan_id,
            step.id if step is not None else "plan",
            phase,
            level,
        )
        return LoopObservation(
            observation_id=observation_id,
            level=level,
            phase="pre" if phase == "pre" else "post",
            packages=packages,
            route_id=plan.selected_route_id,
            step_id=step.id if step is not None else None,
        )


def observation_progressed(pre: LoopObservation | None, post: LoopObservation | None) -> bool:
    if pre is None or post is None:
        return True
    return (
        _normalized_observation_packages(pre.packages),
        pre.route_id,
        pre.step_id,
    ) != (
        _normalized_observation_packages(post.packages),
        post.route_id,
        post.step_id,
    )


def _normalized_observation_packages(packages: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {package_id: _strip_volatile_fields(payload) for package_id, payload in packages.items()}


def _strip_volatile_fields(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _strip_volatile_fields(item)
            for key, item in value.items()
            if key not in VOLATILE_OBSERVATION_KEYS
        }
    if isinstance(value, list):
        return [_strip_volatile_fields(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_strip_volatile_fields(item) for item in value)
    return value


def _observation_request(plan: TaskPlan, package_ids: tuple[str, ...]):
    active_domain_ids = tuple(dict.fromkeys(route.domain_id for route in plan.routes if route.domain_id))
    from .domain_models import ObservationRequest

    return ObservationRequest(
        active_domain_ids=active_domain_ids,
        requested_context_package_ids=(),
        postcondition_package_ids=package_ids,
    )


def _make_observation_id(plan_id: str, step_id: str, phase: str, level: ObservationLevel) -> str:
    digest = sha256(f"{plan_id}:{step_id}:{phase}:{level}:{utc_now_iso()}".encode("utf-8")).hexdigest()[:12]
    return f"obs_{digest}"
