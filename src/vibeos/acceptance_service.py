from __future__ import annotations

from dataclasses import asdict

from .acceptance import AcceptanceEngine
from .browser_state import browser_observation_scope
from .domain_models import ObservationReceipt, ObservationRequest
from .domain_registry import DomainRegistry, default_domain_registry
from .models import CommandRequest
from .observation import resolve_post_execution_observation
from .task_models import AcceptanceStatus, ExecutionState, ExecutionStatus, OverallStatus, PlanExecutionResult, StepExecutionResult, TaskPlan
from .verifiers import VerifierHarness, VerifierRegistry, VerifierResult


class AcceptanceService:
    """Owns postcondition collection, verification, and plan acceptance."""

    def __init__(
        self,
        *,
        acceptance_engine: AcceptanceEngine,
        verifier_registry: VerifierRegistry,
        verifier_harness: VerifierHarness,
    ) -> None:
        self.acceptance_engine = acceptance_engine
        self.verifier_registry = verifier_registry
        self.verifier_harness = verifier_harness

    def assess(
        self,
        plan: TaskPlan,
        step_results: tuple[StepExecutionResult, ...],
        request: CommandRequest,
        _run_id: str,
        understanding_id: str | None,
        candidate_set_id: str | None,
        route_decision_id: str | None,
    ) -> PlanExecutionResult:
        attempt_id = next((item.attempt_id for item in reversed(step_results) if item.attempt_id), None)
        with browser_observation_scope(attempt_id):
            return self._assess(
                plan=plan,
                step_results=step_results,
                dry_run=request.dry_run,
                understanding_id=understanding_id,
                candidate_set_id=candidate_set_id,
                route_decision_id=route_decision_id,
            )

    def assess_compatibility(
        self,
        plan: TaskPlan,
        step_results: tuple[StepExecutionResult, ...],
        *,
        request: CommandRequest,
        error: str | None = None,
        understanding_id: str | None = None,
        candidate_set_id: str | None = None,
        route_decision_id: str | None = None,
    ) -> PlanExecutionResult:
        attempt_id = next((item.attempt_id for item in reversed(step_results) if item.attempt_id), None)
        with browser_observation_scope(attempt_id):
            return self._assess(
                plan=plan,
                step_results=step_results,
                dry_run=request.dry_run,
                understanding_id=understanding_id,
                candidate_set_id=candidate_set_id,
                route_decision_id=route_decision_id,
                error=error,
            )

    def _assess(
        self,
        *,
        plan: TaskPlan,
        step_results: tuple[StepExecutionResult, ...],
        dry_run: bool,
        understanding_id: str | None,
        candidate_set_id: str | None,
        route_decision_id: str | None,
        error: str | None = None,
    ) -> PlanExecutionResult:
        verifier_ids = plan.routes[0].default_verifier_ids if plan.routes else ()
        execution = PlanExecutionResult(
            plan_id=plan.plan_id,
            status=_execution_graph_status(step_results),
            step_results=step_results,
            error=error or next((item.error for item in step_results if item.error), None),
        )
        registry = default_domain_registry(self.verifier_registry.ids())
        active_domain_ids = tuple(dict.fromkeys(route.domain_id for route in plan.routes if route.domain_id))
        route_definition = registry.get_route(plan.selected_route_id)
        post_request: ObservationRequest | None = None
        post_receipt: ObservationReceipt | None = None
        verification_harness = self.verifier_harness
        if active_domain_ids or route_definition is not None:
            package_ids = route_definition.required_context_package_ids if route_definition is not None else ()
            if not package_ids and verifier_ids:
                package_ids = tuple(
                    dict.fromkeys(
                        package_id
                        for verifier_id in verifier_ids
                        for package_id in (
                            self.verifier_registry.get(verifier_id).observation_package_ids if self.verifier_registry.get(verifier_id) is not None else ()
                        )
                    )
                )
            post_request = ObservationRequest(
                active_domain_ids=active_domain_ids,
                requested_context_package_ids=(),
                postcondition_package_ids=package_ids,
            )
            post_receipt = resolve_post_execution_observation(post_request, registry, self.verifier_harness)
            verification_harness = self._postcondition_harness(registry=registry, request=post_request, receipt=post_receipt)
        verification_results = self.verifier_registry.verify_plan(plan, execution, verifier_ids, verification_harness)
        acceptance = self.acceptance_engine.evaluate(
            plan=plan,
            execution=execution,
            verification_results=tuple(asdict(item) for item in verification_results),
            observation_request=post_request,
            observation_receipt=post_receipt,
            dry_run=dry_run,
            understanding_id=understanding_id,
            candidate_set_id=candidate_set_id,
            route_decision_id=route_decision_id,
        )
        execution_status: ExecutionStatus = "dry_run" if dry_run else ("succeeded" if execution.status == "succeeded" else "failed")
        overall_status = overall_status_for_outcome(
            execution_status=execution_status,
            acceptance_status=acceptance.status,
            review_status="allowed",
        )
        return PlanExecutionResult(
            plan_id=execution.plan_id,
            status=execution.status,
            step_results=execution.step_results,
            verification_results=tuple(asdict(item) for item in verification_results),
            verification_status=summarize_verification_status(verification_results),
            execution_status=execution_status,
            acceptance_status=acceptance.status,
            overall_status=overall_status,
            acceptance_result=asdict(acceptance),
            error=execution.error,
        )

    def _postcondition_harness(
        self,
        *,
        registry: DomainRegistry,
        request: ObservationRequest,
        receipt: ObservationReceipt | None,
    ) -> VerifierHarness:
        context_packages: dict[str, dict[str, object]] = {}
        for package_id in request.postcondition_package_ids:
            payload = self.verifier_harness.context_package_for(package_id)
            payload_status = str(payload.get("status") or "") if isinstance(payload, dict) else ""
            if not payload or payload_status == "unavailable":
                definition = registry.context_registry.get(package_id)
                if definition is not None:
                    payload = definition.producer()
            if not payload and receipt is not None:
                package = next((item for item in receipt.packages if item.package_id == package_id), None)
                payload = package.payload if package is not None else {}
            if isinstance(payload, dict):
                context_packages[package_id] = dict(payload)
        return VerifierHarness(
            observations={verifier_id: self.verifier_harness.observation_for(verifier_id) for verifier_id in self.verifier_registry.ids()},
            context_packages=context_packages,
        )


def _execution_graph_status(step_results: tuple[StepExecutionResult, ...]) -> ExecutionState:
    if not step_results:
        return "succeeded"
    if any(result.status == "rejected" for result in step_results):
        return "rejected"
    if any(result.status != "succeeded" for result in step_results):
        return "failed"
    return "succeeded"


def overall_status_for_outcome(
    *,
    execution_status: str,
    acceptance_status: AcceptanceStatus,
    review_status: str,
) -> OverallStatus:
    if review_status == "review_required":
        return "needs_review"
    if execution_status == "not_started":
        return "failed"
    if execution_status == "dry_run":
        return "dry_run"
    if execution_status == "failed":
        return "failed"
    if execution_status == "succeeded" and acceptance_status == "passed":
        return "completed"
    if execution_status == "succeeded" and acceptance_status in {"failed", "indeterminate"}:
        return "incomplete"
    return "failed"


def summarize_verification_status(results: tuple[VerifierResult, ...]) -> str | None:
    if not results:
        return None
    statuses = {item.status for item in results}
    if "failed" in statuses:
        return "failed"
    if "unavailable" in statuses:
        return "unavailable"
    if statuses == {"passed"}:
        return "passed"
    if "skipped" in statuses:
        return "skipped"
    return "passed"
