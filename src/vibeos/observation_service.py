from __future__ import annotations

from contextlib import nullcontext
from hashlib import sha256
from typing import Any

from .browser_state import browser_observation_scope
from .domain_models import ObservationReceipt, ObservationRequest
from .domain_registry import default_domain_registry
from .loop_models import LoopObservation, ObservationLevel
from .models import CommandRequest, utc_now_iso
from .task_models import StepExecutionResult
from .observation import resolve_observation_request, resolve_post_execution_observation
from .task_models import TaskPlan, TaskStep
from .verifiers import VerifierHarness, VerifierRegistry

VOLATILE_OBSERVATION_KEYS = {"attempt_id", "captured_at", "freshness_ts", "run_id"}
OBSERVATION_PACKAGE_LEVELS: dict[str, ObservationLevel] = {
    "session_context": "L0",
    "browser_context": "L1",
    "window_context": "L1",
    "media_context": "L1",
    "system_context": "L1",
}


class ObservationService:
    def __init__(self, verifier_registry: VerifierRegistry, harness: VerifierHarness | None = None) -> None:
        self.verifier_registry = verifier_registry
        self.harness = harness or VerifierHarness()
        self.registry = default_domain_registry(self.verifier_registry.ids())

    def observe(
        self,
        *,
        plan: TaskPlan,
        step: TaskStep | None,
        phase: str,
        level: ObservationLevel,
        attempt_id: str | None = None,
    ) -> LoopObservation:
        request = _observation_request(plan=plan, phase=phase, level=level, registry=self.registry)
        scope = browser_observation_scope(attempt_id) if phase == "post" else nullcontext()
        with scope:
            receipt = _resolve_observation_receipt(request=request, registry=self.registry, harness=self.harness, phase=phase)
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

    def progressed(
        self,
        plan: TaskPlan,
        step: TaskStep,
        step_result: StepExecutionResult,
        pre_observation: LoopObservation,
        post_observation: LoopObservation,
        request: CommandRequest,
    ) -> bool:
        """Apply the selected route's explicit evidence contract."""

        if step_result.status != "succeeded":
            return False
        if request.dry_run:
            return True
        route = self.registry.get_route(plan.selected_route_id)
        if route is None or not route.default_verifier_ids:
            return True
        return observation_progressed(pre_observation, post_observation)


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
        return {key: _strip_volatile_fields(item) for key, item in value.items() if key not in VOLATILE_OBSERVATION_KEYS}
    if isinstance(value, list):
        return [_strip_volatile_fields(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_strip_volatile_fields(item) for item in value)
    return value


def _resolve_observation_receipt(
    *,
    request: ObservationRequest,
    registry,
    harness: VerifierHarness,
    phase: str,
) -> ObservationReceipt:
    if phase == "pre":
        return resolve_observation_request(request, registry)
    return resolve_post_execution_observation(request, registry, harness)


def _observation_request(*, plan: TaskPlan, phase: str, level: ObservationLevel, registry) -> ObservationRequest:
    active_domain_ids = _active_domain_ids(plan)
    route = registry.get_route(plan.selected_route_id)
    package_ids = _packages_for_observation_level(
        active_domain_ids=active_domain_ids,
        route_package_ids=route.required_context_package_ids if route is not None else (),
        registry=registry,
        level=level,
    )
    if phase == "pre":
        return ObservationRequest(
            active_domain_ids=active_domain_ids,
            requested_context_package_ids=package_ids,
            postcondition_package_ids=(),
        )
    return ObservationRequest(
        active_domain_ids=active_domain_ids,
        requested_context_package_ids=(),
        postcondition_package_ids=package_ids,
    )


def _active_domain_ids(plan: TaskPlan) -> tuple[str, ...]:
    route = next((item for item in plan.routes if item.id == plan.selected_route_id and item.domain_id), None)
    if route is not None and route.domain_id:
        return (route.domain_id,)
    return tuple(dict.fromkeys(item.domain_id for item in plan.routes if item.domain_id))


def _packages_for_observation_level(
    *,
    active_domain_ids: tuple[str, ...],
    route_package_ids: tuple[str, ...],
    registry,
    level: ObservationLevel,
) -> tuple[str, ...]:
    allowed_package_ids: list[str] = []
    for domain_id in active_domain_ids:
        pack = registry.get_pack(domain_id)
        if pack is None:
            continue
        allowed_package_ids.extend(pack.allowed_context_package_ids)
    allowed = tuple(dict.fromkeys(package_id for package_id in allowed_package_ids if package_id))
    route_required = tuple(dict.fromkeys(package_id for package_id in route_package_ids if package_id))
    baseline = route_required or tuple(package_id for package_id in ("session_context",) if package_id in allowed)

    if level == "L0":
        if baseline:
            return baseline
        if route_required:
            return route_required[:1]
        return allowed[:1]
    if level == "L1":
        targeted = tuple(
            dict.fromkeys(
                package_id
                for package_id in (*baseline, *allowed)
                if _observation_level_rank(OBSERVATION_PACKAGE_LEVELS.get(package_id, "L2")) <= _observation_level_rank("L1")
            )
        )
        if targeted:
            return targeted
        if baseline:
            return baseline
        return allowed
    if allowed:
        return allowed
    if route_required:
        return route_required
    return baseline


def _observation_level_rank(level: ObservationLevel) -> int:
    return {"L0": 0, "L1": 1, "L2": 2}[level]


def _make_observation_id(plan_id: str, step_id: str, phase: str, level: ObservationLevel) -> str:
    digest = sha256(f"{plan_id}:{step_id}:{phase}:{level}:{utc_now_iso()}".encode("utf-8")).hexdigest()[:12]
    return f"obs_{digest}"
