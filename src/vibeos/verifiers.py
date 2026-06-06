from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from .domain_models import VerifierResult
from .task_models import PlanExecutionResult, TaskPlan


VerifierRunner = Callable[[TaskPlan, PlanExecutionResult, "VerifierHarness"], VerifierResult]


@dataclass(frozen=True)
class VerifierSpec:
    verifier_id: str
    observation_package_ids: tuple[str, ...]
    runner: VerifierRunner


class VerifierHarness:
    def __init__(
        self,
        observations: dict[str, dict[str, Any]] | None = None,
        context_packages: dict[str, dict[str, Any]] | None = None,
    ) -> None:
        self._observations = observations or {}
        self._context_packages = context_packages or {}

    def observation_for(self, verifier_id: str) -> dict[str, Any]:
        return dict(self._observations.get(verifier_id, {}))

    def context_package_for(self, package_id: str) -> dict[str, Any]:
        return dict(self._context_packages.get(package_id, {}))


class VerifierRegistry:
    def __init__(self, specs: tuple[VerifierSpec, ...]) -> None:
        self._specs = {spec.verifier_id: spec for spec in specs}

    def get(self, verifier_id: str) -> VerifierSpec | None:
        return self._specs.get(verifier_id)

    def ids(self) -> tuple[str, ...]:
        return tuple(sorted(self._specs))

    def validate(self) -> tuple[str, ...]:
        errors: list[str] = []
        for verifier_id, spec in self._specs.items():
            if not callable(spec.runner):
                errors.append(f"verifier {verifier_id!r} runner must be callable")
        return tuple(errors)

    def verify_plan(
        self,
        plan: TaskPlan,
        execution: PlanExecutionResult,
        verifier_ids: tuple[str, ...],
        harness: VerifierHarness | None = None,
    ) -> tuple[VerifierResult, ...]:
        active_harness = harness or VerifierHarness()
        results: list[VerifierResult] = []
        for verifier_id in verifier_ids:
            spec = self.get(verifier_id)
            if spec is None:
                results.append(VerifierResult(verifier_id=verifier_id, status="unavailable", message="verifier not registered"))
                continue
            results.append(spec.runner(plan, execution, active_harness))
        return tuple(results)


def browser_url_opened_verifier(plan: TaskPlan, execution: PlanExecutionResult, harness: VerifierHarness) -> VerifierResult:
    observation = harness.observation_for("browser_url_opened")
    browser_context = harness.context_package_for("browser_context")
    opened_url = str(
        observation.get("opened_url")
        or browser_context.get("active_url")
        or browser_context.get("requested_url")
        or ""
    )
    expected_url = ""
    for step in plan.steps:
        if step.action == "browser.open_url":
            expected_url = str(step.target.get("uri") or "")
            break
    if execution.status != "succeeded":
        return VerifierResult(
            verifier_id="browser_url_opened",
            status="skipped",
            message="adapter execution did not succeed",
            observation_package_ids=("browser_context",),
            details={"execution_status": execution.status},
        )
    if not opened_url:
        return VerifierResult(
            verifier_id="browser_url_opened",
            status="failed",
            message="browser verifier did not observe an opened URL",
            observation_package_ids=("browser_context",),
            details={"expected_url": expected_url, "browser_context_status": browser_context.get("status")},
        )
    if opened_url != expected_url:
        return VerifierResult(
            verifier_id="browser_url_opened",
            status="failed",
            message="browser opened a different URL than expected",
            observation_package_ids=("browser_context",),
            details={"expected_url": expected_url, "opened_url": opened_url},
        )
    return VerifierResult(
        verifier_id="browser_url_opened",
        status="passed",
        message="browser verifier observed the requested URL",
        observation_package_ids=("browser_context",),
        details={"opened_url": opened_url},
    )


def browser_search_route_completed_verifier(plan: TaskPlan, execution: PlanExecutionResult, harness: VerifierHarness) -> VerifierResult:
    observation = harness.observation_for("browser_search_route_completed")
    browser_context = harness.context_package_for("browser_context")
    observed_query = str(observation.get("query") or browser_context.get("query") or "")
    expected_query = ""
    for step in plan.steps:
        if step.action in {"browser.search_web", "browser.open_site_search"}:
            expected_query = str(step.target.get("query") or "")
            break
    if execution.status != "succeeded":
        return VerifierResult(
            verifier_id="browser_search_route_completed",
            status="skipped",
            message="adapter execution did not succeed",
            observation_package_ids=("browser_context",),
            details={"execution_status": execution.status},
        )
    if not observed_query:
        return VerifierResult(
            verifier_id="browser_search_route_completed",
            status="failed",
            message="search verifier did not observe a browser search query",
            observation_package_ids=("browser_context",),
            details={"expected_query": expected_query, "browser_context_status": browser_context.get("status")},
        )
    if observed_query != expected_query:
        return VerifierResult(
            verifier_id="browser_search_route_completed",
            status="failed",
            message="search verifier observed a different query",
            observation_package_ids=("browser_context",),
            details={"expected_query": expected_query, "observed_query": observed_query},
        )
    return VerifierResult(
        verifier_id="browser_search_route_completed",
        status="passed",
        message="browser verifier observed the requested search query",
        observation_package_ids=("browser_context",),
        details={"query": observed_query},
    )


def media_playback_state_available_verifier(plan: TaskPlan, execution: PlanExecutionResult, harness: VerifierHarness) -> VerifierResult:
    observation = harness.observation_for("media_playback_state_available")
    state = str(observation.get("playback_state") or "")
    if execution.status != "succeeded":
        return VerifierResult(
            verifier_id="media_playback_state_available",
            status="skipped",
            message="adapter execution did not succeed",
            observation_package_ids=("media_context",),
            details={"execution_status": execution.status},
        )
    if not state or state == "unavailable":
        return VerifierResult(
            verifier_id="media_playback_state_available",
            status="failed",
            message="media verifier did not observe a usable playback state",
            observation_package_ids=("media_context",),
            details={"playback_state": state or "missing"},
        )
    return VerifierResult(
        verifier_id="media_playback_state_available",
        status="passed",
        message="media verifier observed a playback state",
        observation_package_ids=("media_context",),
        details={"playback_state": state},
    )


def default_verifier_registry() -> VerifierRegistry:
    registry = VerifierRegistry(
        (
            VerifierSpec("browser_url_opened", ("browser_context",), browser_url_opened_verifier),
            VerifierSpec("browser_search_route_completed", ("browser_context",), browser_search_route_completed_verifier),
            VerifierSpec("media_playback_state_available", ("media_context",), media_playback_state_available_verifier),
        )
    )
    errors = registry.validate()
    if errors:
        raise ValueError("; ".join(errors))
    return registry
