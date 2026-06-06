from __future__ import annotations

from dataclasses import replace
from dataclasses import asdict
from hashlib import sha256
from time import perf_counter

from .acceptance import AcceptanceEngine
from .apps import AppRegistry
from .audit import AuditLog
from .browser_state import browser_attempt_scope, record_browser_navigation
from .capabilities import capability_payload, executable_actions, permission_summary
from .clipboard import ClipboardAdapter
from .domain_models import ObservationRequest
from .domain_registry import default_domain_registry
from .execution_graph import execute_plan_graph
from .failure_classifier import FailureClassifier
from .intent import IntentBroker, OpenAICompatibleIntentBroker, RuleIntentBroker
from .notifications import NotificationAdapter
from .models import CommandRequest, CommandResult, Intent, utc_now_iso
from .observation import resolve_post_execution_observation
from .planner import browser_semantic_uri, plan_turn
from .permissions import PermissionPolicy
from .portal import PortalAdapter
from .replanner import EvidenceDrivenReplanner, Replanner
from .reviews import ReviewStore, review_to_payload
from .task_models import (
    AgentRun,
    FailureClassification,
    PlanAttempt,
    PlanExecutionResult,
    ReplanDecision,
    StepExecutionResult,
    StepReviewRecord,
    TaskPlan,
    TaskPlanReviewResult,
    canonicalize_target_for_action,
    task_plan_from_payload,
)
from .task_validation import validate_plan
from .verifiers import VerifierHarness, VerifierRegistry, default_verifier_registry
from .windows import WindowRegistry


class CapabilityBroker:
    def __init__(
        self,
        intent_broker: IntentBroker | None = None,
        apps: AppRegistry | None = None,
        windows: WindowRegistry | None = None,
        portal: PortalAdapter | None = None,
        notifications: NotificationAdapter | None = None,
        clipboard: ClipboardAdapter | None = None,
        policy: PermissionPolicy | None = None,
        audit: AuditLog | None = None,
        reviews: ReviewStore | None = None,
        verifier_registry: VerifierRegistry | None = None,
        verifier_harness: VerifierHarness | None = None,
        failure_classifier: FailureClassifier | None = None,
        replanner: Replanner | None = None,
    ) -> None:
        self.intent_broker = intent_broker or OpenAICompatibleIntentBroker()
        self.apps = apps or AppRegistry()
        self.windows = windows or WindowRegistry()
        self.portal = portal or PortalAdapter()
        self.notifications = notifications or NotificationAdapter()
        self.clipboard = clipboard or ClipboardAdapter()
        self.policy = policy or PermissionPolicy()
        self.audit = audit or AuditLog()
        self.reviews = reviews or ReviewStore()
        self.verifier_registry = verifier_registry or default_verifier_registry()
        self.verifier_harness = verifier_harness or VerifierHarness()
        self.acceptance_engine = AcceptanceEngine()
        self.failure_classifier = failure_classifier or FailureClassifier()
        self.replanner = replanner or EvidenceDrivenReplanner()

    def capabilities(self) -> dict[str, object]:
        return {
            "capabilities": executable_actions(),
            "capability_details": capability_payload(),
            "permission_policy": permission_summary(),
        }

    def pending_reviews(self) -> list[dict[str, object]]:
        return [review_to_payload(request) for request in self.reviews.list_pending()]

    def review_task_plan(self, plan: TaskPlan) -> TaskPlanReviewResult:
        self._ensure_task_plan(plan)
        validation = validate_plan(plan)
        if not validation.ok:
            return TaskPlanReviewResult(
                plan_id=plan.plan_id,
                status="rejected",
                max_risk_level="L3",
                message="task plan failed validation before permission review",
            )

        step_reviews: list[StepReviewRecord] = []
        review_required = False
        allowed = True
        max_risk = "L0"
        rejection_reason = ""

        for step in plan.steps:
            review = self.policy.review(self._intent_from_task_step(step))
            step_reviews.append(
                StepReviewRecord(
                    step_id=step.id,
                    action=step.action,
                    risk_level=review.risk_level,
                    review_required=review.review_required,
                    allowed=review.allowed,
                    reason=review.reason,
                )
            )
            max_risk = max_risk_level(max_risk, review.risk_level)
            review_required = review_required or review.review_required
            if not review.allowed and allowed:
                allowed = False
                rejection_reason = review.reason

        if not allowed:
            return TaskPlanReviewResult(
                plan_id=plan.plan_id,
                status="rejected",
                max_risk_level=max_risk,
                step_reviews=tuple(step_reviews),
                message=rejection_reason or "task plan contains a rejected step",
            )

        if review_required:
            review_request = self.reviews.create_plan_review(plan.utterance, asdict(plan), TaskPlanReviewResult(plan_id=plan.plan_id, status="review_required", max_risk_level=max_risk, step_reviews=tuple(step_reviews)))
            return TaskPlanReviewResult(
                plan_id=plan.plan_id,
                status="review_required",
                max_risk_level=max_risk,
                review_id=review_request.review_id,
                step_reviews=tuple(step_reviews),
                message=f"explicit approval is required; run `vibe approve {review_request.review_id}` after reviewing the request",
            )

        return TaskPlanReviewResult(
            plan_id=plan.plan_id,
            status="allowed",
            max_risk_level=max_risk,
            step_reviews=tuple(step_reviews),
            message="task plan is allowed without additional review",
        )

    def execute_task_plan(
        self,
        plan: TaskPlan,
        dry_run: bool = False,
        transport: str | None = None,
        review_id: str | None = None,
        run_id: str | None = None,
        attempt_id: str | None = None,
    ) -> PlanExecutionResult:
        self._ensure_task_plan(plan)
        validation = validate_plan(plan)
        if not validation.ok:
            return PlanExecutionResult(plan_id=plan.plan_id, status="rejected", error="task plan failed validation")

        def execute_step(step) -> StepExecutionResult:
            intent = self._intent_from_task_step(step)
            review = self.policy.review(intent)
            request = CommandRequest(
                utterance=plan.utterance,
                dry_run=dry_run,
                approve=review_id is not None,
                review_id=review_id,
                transport=transport,
            )
            started = perf_counter()
            command = self._with_transport(self._execute(request, intent, review), transport)
            duration_ms = max(0, round((perf_counter() - started) * 1000))
            layer = "adapter_execute"
            step_status = command_status_to_execution_state(command.status)
            adapter_status = adapter_status_for_command(command)
            error_code = error_code_for_command(command)
            audit_id = self.audit.record(
                request=request,
                intent=intent,
                status=command.status,
                result=command.result,
                selected_target=command.selected_target,
                message=command.message,
                review=command.review,
                review_id=review_id,
                plan_id=plan.plan_id,
                step_id=step.id,
                layer=layer,
            )
            return StepExecutionResult(
                step_id=step.id,
                layer=layer,
                status=step_status,
                adapter=adapter_name_for_step(step, command),
                capability_id=step.capability_id,
                attempt=1,
                duration_ms=duration_ms,
                adapter_status=adapter_status,
                diagnostics=diagnostics_for_step(step, command, request, duration_ms),
                error_code=error_code,
                result=command.result if isinstance(command.result, dict) else {"value": command.result},
                error=command.message or None,
                audit_id=audit_id,
            )

        scoped_run_id = run_id or self._make_run_id(plan.utterance)
        scoped_attempt_id = attempt_id or self._make_attempt_id(scoped_run_id, 1, plan.selected_route_id or "standalone")
        with browser_attempt_scope(run_id=scoped_run_id, attempt_id=scoped_attempt_id, route_id=plan.selected_route_id):
            execution = execute_plan_graph(plan, execute_step)
            registry = default_domain_registry(self.verifier_registry.ids())
            active_domain_ids = tuple(dict.fromkeys(route.domain_id for route in plan.routes if route.domain_id))
            route_definition = registry.get_route(plan.selected_route_id)
            post_request = None
            post_receipt = None
            if active_domain_ids or route_definition is not None:
                package_ids = route_definition.required_context_package_ids if route_definition is not None else ()
                post_request = ObservationRequest(
                    active_domain_ids=active_domain_ids,
                    requested_context_package_ids=(),
                    postcondition_package_ids=package_ids,
                )
                post_receipt = resolve_post_execution_observation(post_request, registry, self.verifier_harness)
        verifier_ids = plan.routes[0].default_verifier_ids if plan.routes else ()
        verification_harness = self._verification_harness_for_receipt(post_receipt)
        verification_results = self.verifier_registry.verify_plan(plan, execution, verifier_ids, verification_harness)
        acceptance = self.acceptance_engine.evaluate(
            plan=plan,
            execution=execution,
            verification_results=tuple(asdict(item) for item in verification_results),
            observation_request=post_request,
            observation_receipt=post_receipt,
            dry_run=dry_run,
        )
        execution_status = "dry_run" if dry_run else ("succeeded" if execution.status == "succeeded" else "failed")
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

    def handle(self, request: CommandRequest) -> CommandResult:
        if request.review_id:
            return self.approve_review(request.review_id, dry_run=request.dry_run, transport=request.transport)
        if request.approve:
            fallback = Intent.unknown("approval requires a stored review id")
            result = self._with_transport(
                CommandResult(
                    status="rejected",
                    intent=fallback,
                    message="L2 approval must use a stored review id; run without approval first, then `vibe approve <review_id>`",
                ),
                request.transport,
            )
            audit_id = self.audit.record(
                request=request,
                intent=result.intent,
                status=result.status,
                result=result.result,
                selected_target=result.selected_target,
                message=result.message,
                review=result.review,
                review_id=result.review_id,
                execution_status=result.execution_status,
                acceptance_status=result.acceptance_status,
                overall_status=result.overall_status,
            )
            return CommandResult(
                status=result.status,
                intent=result.intent,
                result=result.result,
                selected_target=result.selected_target,
                audit_id=audit_id,
                review_id=result.review_id,
                transport=result.transport,
                message=result.message,
                review=result.review,
                execution_status=result.execution_status,
                acceptance_status=result.acceptance_status,
                overall_status=result.overall_status,
            )

        planned = self._handle_task_plan_request(request)
        if planned is not None:
            result = self._with_transport(planned, request.transport)
            audit_id = self.audit.record(
                request=request,
                intent=result.intent,
                status=result.status,
                result=result.result,
                selected_target=result.selected_target,
                message=result.message,
                review=result.review,
                review_id=result.review_id,
                execution_status=result.execution_status,
                acceptance_status=result.acceptance_status,
                overall_status=result.overall_status,
            )
            return CommandResult(
                status=result.status,
                intent=result.intent,
                result=result.result,
                selected_target=result.selected_target,
                audit_id=audit_id,
                review_id=result.review_id,
                transport=result.transport,
                message=result.message,
                review=result.review,
                execution_status=result.execution_status,
                acceptance_status=result.acceptance_status,
                overall_status=result.overall_status,
            )

        intent = self.intent_broker.parse(request.utterance)
        review = self.policy.review(intent)
        if not review.allowed:
            result = CommandResult(status="rejected", intent=intent, message=review.reason, review=review)
        elif review.review_required and not request.dry_run:
            if request.approve:
                result = CommandResult(
                    status="rejected",
                    intent=intent,
                    message="L2 approval must use a stored review id; run without approval first, then `vibe approve <review_id>`",
                    review=review,
                )
            else:
                review_request = self.reviews.create(request.utterance, intent, review)
                result = CommandResult(
                    status="review_required",
                    intent=intent,
                    result={"review_id": review_request.review_id, "review": asdict(review)},
                    review_id=review_request.review_id,
                    message=f"explicit approval is required; run `vibe approve {review_request.review_id}` after reviewing the request",
                    review=review,
                )
        else:
            result = self._execute(request, intent, review)
        result = self._with_transport(result, request.transport)
        audit_id = self.audit.record(
            request=request,
            intent=intent,
            status=result.status,
            result=result.result,
            selected_target=result.selected_target,
            message=result.message,
            review=result.review,
            review_id=result.review_id,
            execution_status=result.execution_status,
            acceptance_status=result.acceptance_status,
            overall_status=result.overall_status,
        )
        return CommandResult(
            status=result.status,
            intent=result.intent,
            result=result.result,
            selected_target=result.selected_target,
            audit_id=audit_id,
            review_id=result.review_id,
            transport=result.transport,
            message=result.message,
            review=result.review,
            execution_status=result.execution_status,
            acceptance_status=result.acceptance_status,
            overall_status=result.overall_status,
        )

    def _handle_task_plan_request(self, request: CommandRequest) -> CommandResult | None:
        planning = plan_turn(request.utterance, self.intent_broker, debug=request.debug)
        if planning.analysis.type not in {"task", "mixed", "clarification", "rejected"}:
            return None
        compatibility_intent = self.intent_broker.parse(request.utterance) if request.utterance else Intent.unknown("missing utterance")
        if planning.plan is None and not planning.candidates and compatibility_intent.action != "unknown":
            return None
        return self._run_task_plan_loop(request, planning)

    def _run_task_plan_loop(self, request: CommandRequest, planning) -> CommandResult:
        run_id = self._make_run_id(request.utterance)
        goal_id = planning.goal_synthesis.goal_spec.goal_id if planning.goal_synthesis and planning.goal_synthesis.goal_spec else "goal_unresolved"
        attempts: list[PlanAttempt] = []
        excluded_route_ids: tuple[str, ...] = ()
        excluded_capability_ids: tuple[str, ...] = ()
        candidate_domain_ids_override: tuple[str, ...] | None = None
        trigger = "initial_plan"

        while True:
            payload = self._planning_payload(planning)
            analysis = planning.analysis
            plan = planning.plan
            candidates = planning.candidates

            if plan is None:
                compatibility_intent = self.intent_broker.parse(request.utterance) if request.utterance else Intent.unknown("missing utterance")
                if not attempts and not candidates and compatibility_intent.action != "unknown":
                    return self._with_transport(
                        CommandResult(
                            status="failed",
                            intent=compatibility_intent,
                            result=payload,
                            message="planner did not produce a task plan",
                            execution_status="not_started",
                            acceptance_status="skipped",
                            overall_status="failed",
                        ),
                        request.transport,
                    )
                intent = Intent.unknown("no route satisfies required capabilities" if candidates else (analysis.explanation or "no executable task plan was produced"))
                return self._finalize_task_plan_result(
                    request=request,
                    run_id=run_id,
                    goal_id=goal_id,
                    attempts=tuple(attempts),
                    payload=payload,
                    intent=intent,
                    status="rejected" if candidates or analysis.type == "rejected" else "failed",
                    message=intent.reason,
                    execution_status="not_started",
                    acceptance_status="skipped",
                    overall_status="failed",
                    selected_target=None,
                )

            validation = validate_plan(plan)
            payload["validation"] = asdict(validation)
            intent = self._intent_from_task_step(plan.steps[0]) if plan.steps else Intent.unknown("task plan contains no executable steps")
            if not validation.ok:
                review = self.policy.review(intent)
                return self._finalize_task_plan_result(
                    request=request,
                    run_id=run_id,
                    goal_id=goal_id,
                    attempts=tuple(attempts),
                    payload=payload,
                    intent=intent,
                    status="rejected",
                    message="task plan failed validation",
                    execution_status="not_started",
                    acceptance_status="skipped",
                    overall_status="failed",
                    selected_target=None,
                    review=review,
                )

            plan_review = self.review_task_plan(plan)
            payload["plan_review"] = asdict(plan_review)
            payload["trace"]["review"] = asdict(plan_review)
            if plan_review.status == "rejected":
                return self._finalize_task_plan_result(
                    request=request,
                    run_id=run_id,
                    goal_id=goal_id,
                    attempts=tuple(attempts),
                    payload=payload,
                    intent=intent,
                    status="rejected",
                    message=plan_review.message,
                    execution_status="not_started",
                    acceptance_status="skipped",
                    overall_status="failed",
                    selected_target=None,
                )
            if plan_review.status == "review_required":
                review_request = self.reviews.get(plan_review.review_id or "")
                return self._finalize_task_plan_result(
                    request=request,
                    run_id=run_id,
                    goal_id=goal_id,
                    attempts=tuple(attempts),
                    payload=payload,
                    intent=intent,
                    status="review_required",
                    message=plan_review.message,
                    execution_status="not_started",
                    acceptance_status="skipped",
                    overall_status="needs_review",
                    selected_target=None,
                    review=review_request.review if review_request else None,
                    review_id=plan_review.review_id,
                )

            next_attempt_id = self._make_attempt_id(run_id, len(attempts) + 1, plan.selected_route_id)
            execution = self.execute_task_plan(
                plan,
                dry_run=request.dry_run,
                transport=request.transport,
                run_id=run_id,
                attempt_id=next_attempt_id,
            )
            payload["preview" if request.dry_run else "execution"] = asdict(execution)
            payload["trace"]["execution"] = asdict(execution)
            payload["trace"]["verification"] = {
                "status": execution.verification_status,
                "results": list(execution.verification_results),
            }
            payload["trace"]["acceptance"] = execution.acceptance_result or {}
            payload["debug_trace"]["review"] = asdict(plan_review)
            payload["debug_trace"]["execution"] = asdict(execution)
            payload["debug_trace"]["acceptance"] = execution.acceptance_result or {}

            failure = self.failure_classifier.classify(plan, execution)
            decision = self.replanner.decide(
                utterance=request.utterance,
                current_plan=plan,
                attempts=tuple(
                    attempts
                    + [
                        PlanAttempt(
                            attempt_id=next_attempt_id,
                            run_id=run_id,
                            attempt_index=len(attempts) + 1,
                            trigger=trigger,
                            selected_route_id=plan.selected_route_id,
                            task_plan=plan,
                            execution_result=execution,
                            observation_receipt=execution.acceptance_result.get("observation_receipt") if isinstance(execution.acceptance_result, dict) else None,
                            acceptance_result=execution.acceptance_result,
                            failure=failure,
                        )
                    ]
                ),
                failure=failure,
            )
            attempt = PlanAttempt(
                attempt_id=next_attempt_id,
                run_id=run_id,
                attempt_index=len(attempts) + 1,
                trigger=trigger,
                selected_route_id=plan.selected_route_id,
                task_plan=plan,
                execution_result=execution,
                observation_receipt=execution.acceptance_result.get("observation_receipt") if isinstance(execution.acceptance_result, dict) else None,
                acceptance_result=execution.acceptance_result,
                failure=failure,
                replan_decision=decision,
            )
            attempts.append(attempt)
            selected_target = self._selected_target_from_execution(execution)
            if request.dry_run or failure.failure_class == "none" or decision.action in {"stop", "ask_user"}:
                overall_status = execution.overall_status
                if decision.action == "ask_user":
                    overall_status = "needs_user_input"
                elif decision.action == "stop" and failure.failure_class not in {"none", "acceptance_unverified", "acceptance_failed"} and len(attempts) > 1:
                    overall_status = "blocked"
                message = self._task_result_message(request, execution, failure, decision, overall_status)
                return self._finalize_task_plan_result(
                    request=request,
                    run_id=run_id,
                    goal_id=goal_id,
                    attempts=tuple(attempts),
                    payload=payload,
                    intent=intent,
                    status=execution_state_to_command_status(execution.status, dry_run=request.dry_run),
                    message=message,
                    execution_status=execution.execution_status,
                    acceptance_status=execution.acceptance_status,
                    overall_status=overall_status,
                    selected_target=selected_target,
                )

            if decision.action == "retry_same_attempt":
                trigger = "retry_same_attempt"
                continue

            excluded_route_ids = tuple(dict.fromkeys((*excluded_route_ids, *decision.do_not_repeat_route_ids)))
            excluded_capability_ids = tuple(dict.fromkeys((*excluded_capability_ids, *decision.do_not_repeat_capability_ids)))
            candidate_domain_ids_override = decision.candidate_domain_ids or candidate_domain_ids_override
            trigger = "replan_with_constraints"
            planning = plan_turn(
                request.utterance,
                self.intent_broker,
                debug=request.debug,
                candidate_domain_ids_override=candidate_domain_ids_override,
                excluded_route_ids=excluded_route_ids,
                excluded_capability_ids=excluded_capability_ids,
            )

    def _planning_payload(self, planning) -> dict[str, object]:
        return {
            "analysis": asdict(planning.analysis),
            "goal_synthesis": asdict(planning.goal_synthesis) if planning.goal_synthesis else None,
            "plan": asdict(planning.plan) if planning.plan else None,
            "candidates": [asdict(candidate) for candidate in planning.candidates],
            "domain_routing": asdict(planning.domain_routing) if planning.domain_routing else None,
            "observation_request": asdict(planning.observation_request) if planning.observation_request else None,
            "observation_receipt": asdict(planning.observation_receipt) if planning.observation_receipt else None,
            "capability_exposure": asdict(planning.capability_exposure) if planning.capability_exposure else None,
            "trace": asdict(planning.trace) if planning.trace is not None else {},
            "debug_trace": asdict(planning.debug_trace) if planning.debug_trace is not None else {},
        }

    def _verification_harness_for_receipt(self, receipt) -> VerifierHarness:
        if receipt is None:
            return self.verifier_harness
        context_packages = {
            package.package_id: dict(package.payload)
            for package in receipt.packages
        }
        return VerifierHarness(
            observations={
                verifier_id: self.verifier_harness.observation_for(verifier_id)
                for verifier_id in self.verifier_registry.ids()
            },
            context_packages=context_packages,
        )

    def _finalize_task_plan_result(
        self,
        *,
        request: CommandRequest,
        run_id: str,
        goal_id: str,
        attempts: tuple[PlanAttempt, ...],
        payload: dict[str, object],
        intent: Intent,
        status: str,
        message: str,
        execution_status: str,
        acceptance_status: str,
        overall_status: str,
        selected_target: str | None,
        review=None,
        review_id: str | None = None,
    ) -> CommandResult:
        payload["run"] = asdict(
            AgentRun(
                run_id=run_id,
                goal_id=goal_id,
                utterance=request.utterance,
                status=self._run_status_for_overall(overall_status),
                selected_transport=request.transport,
                attempt_ids=tuple(item.attempt_id for item in attempts),
                final_outcome=overall_status,
            )
        )
        payload["attempts"] = [self._attempt_payload(item) for item in attempts]
        return self._with_transport(
            CommandResult(
                status=status,
                intent=intent,
                result=payload,
                selected_target=selected_target,
                review_id=review_id,
                message=message,
                review=review,
                execution_status=execution_status,
                acceptance_status=acceptance_status,
                overall_status=overall_status,
            ),
            request.transport,
        )

    def _task_result_message(
        self,
        request: CommandRequest,
        execution: PlanExecutionResult,
        failure: FailureClassification,
        decision: ReplanDecision,
        overall_status: str,
    ) -> str:
        if request.dry_run:
            return "task plan resolved without executing real adapters"
        if execution.execution_status == "succeeded" and execution.acceptance_status == "passed":
            return "task goal completed"
        if overall_status == "blocked":
            return decision.reason or failure.message or execution.error or "task goal remains blocked after bounded replanning"
        return failure.message or self._acceptance_message(execution) or execution.error or "task goal did not complete"

    def _selected_target_from_execution(self, execution: PlanExecutionResult) -> str | None:
        if not execution.step_results:
            return None
        return next(
            (
                item.diagnostics.get("selected_target")
                if item.diagnostics.get("selected_target") is not None
                else item.result.get("selected_target")
                for item in reversed(execution.step_results)
                if item.diagnostics.get("selected_target") is not None or item.result.get("selected_target") is not None
            ),
            None,
        )

    def _run_status_for_overall(self, overall_status: str) -> str:
        return {
            "completed": "completed",
            "dry_run": "dry_run",
            "needs_review": "needs_review",
            "needs_user_input": "needs_user_input",
            "blocked": "blocked",
            "incomplete": "incomplete",
        }.get(overall_status, "failed")

    def _attempt_payload(self, attempt: PlanAttempt) -> dict[str, object]:
        payload: dict[str, object] = {
            "attempt_id": attempt.attempt_id,
            "run_id": attempt.run_id,
            "attempt_index": attempt.attempt_index,
            "trigger": attempt.trigger,
            "selected_route_id": attempt.selected_route_id,
        }
        if attempt.task_plan is not None:
            payload["plan_id"] = attempt.task_plan.plan_id
            payload["step_ids"] = [step.id for step in attempt.task_plan.steps]
            payload["capability_ids"] = [step.capability_id for step in attempt.task_plan.steps]
        if attempt.execution_result is not None:
            payload["execution"] = {
                "plan_id": attempt.execution_result.plan_id,
                "status": attempt.execution_result.status,
                "execution_status": attempt.execution_result.execution_status,
                "acceptance_status": attempt.execution_result.acceptance_status,
                "overall_status": attempt.execution_result.overall_status,
                "error": attempt.execution_result.error,
            }
        if attempt.failure is not None:
            payload["failure"] = asdict(attempt.failure)
        if attempt.replan_decision is not None:
            payload["replan_decision"] = asdict(attempt.replan_decision)
        return payload

    def _make_run_id(self, utterance: str) -> str:
        digest = sha256(f"run:{utc_now_iso()}:{utterance}:{len(utterance)}".encode("utf-8")).hexdigest()[:12]
        return f"run_{digest}"

    def _make_attempt_id(self, run_id: str, attempt_index: int, route_id: str) -> str:
        digest = sha256(f"{run_id}:{attempt_index}:{route_id}".encode("utf-8")).hexdigest()[:10]
        return f"attempt_{digest}"

    @staticmethod
    def _acceptance_message(execution: PlanExecutionResult) -> str:
        payload = execution.acceptance_result or {}
        if isinstance(payload, dict):
            return str(payload.get("message", ""))
        return ""

    def approve_review(self, review_id: str, dry_run: bool = False, transport: str | None = None) -> CommandResult:
        if dry_run:
            review_request = self.reviews.get(review_id)
            if not review_request:
                fallback = Intent.unknown("review request not found", {"review_id": review_id})
                return self._with_transport(
                    CommandResult(status="rejected", intent=fallback, review_id=review_id, message="review request not found"),
                    transport,
                )
            if review_request.status != "pending":
                return self._with_transport(
                    CommandResult(
                        status="rejected",
                        intent=review_request.intent,
                        review=review_request.review,
                        review_id=review_id,
                        message=f"review request is not pending; current status is {review_request.status}",
                    ),
                    transport,
                )
            if review_request.review_kind == "plan" and review_request.plan_payload:
                plan = task_plan_from_payload(review_request.plan_payload)
                plan_intent = self._intent_from_task_step(plan.steps[0]) if plan.steps else review_request.intent
                dry_run_run_id = self._make_run_id(plan.utterance)
                execution = self.execute_task_plan(
                    plan,
                    dry_run=True,
                    transport=transport,
                    review_id=review_id,
                    run_id=dry_run_run_id,
                    attempt_id=self._make_attempt_id(dry_run_run_id, 1, plan.selected_route_id or "approved_plan"),
                )
                result = self._with_transport(
                    CommandResult(
                        status=execution_state_to_command_status(execution.status, dry_run=True),
                        intent=plan_intent,
                        result=asdict(execution),
                        review=review_request.review,
                        review_id=review_id,
                        message=execution.error or "stored task plan resolved without execution",
                        execution_status=execution.execution_status,
                        acceptance_status=execution.acceptance_status,
                        overall_status=execution.overall_status,
                    ),
                    transport,
                )
                audit_id = self.audit.record(
                    request=CommandRequest("", dry_run=True, approve=True, review_id=review_id, transport=transport),
                    intent=plan_intent,
                    status=result.status,
                    result=result.result,
                    selected_target=None,
                    message=result.message,
                    review=result.review,
                    review_id=review_id,
                    plan_id=plan.plan_id,
                    layer="permission_review",
                )
                return CommandResult(
                    status=result.status,
                    intent=result.intent,
                    result=result.result,
                    selected_target=result.selected_target,
                    audit_id=audit_id,
                    review_id=review_id,
                    transport=result.transport,
                    message=result.message,
                    review=result.review,
                    execution_status=result.execution_status,
                    acceptance_status=result.acceptance_status,
                    overall_status=result.overall_status,
                )
            request = CommandRequest(
                utterance=review_request.utterance,
                dry_run=True,
                approve=True,
                review_id=review_id,
                transport=transport,
            )
            result = self._with_transport(self._execute(request, review_request.intent, review_request.review), transport)
            audit_id = self.audit.record(
                request=request,
                intent=review_request.intent,
                status=result.status,
                result=result.result,
                selected_target=result.selected_target,
                message=result.message,
                review=result.review,
                review_id=review_id,
            )
            return CommandResult(
                status=result.status,
                intent=result.intent,
                result=result.result,
                selected_target=result.selected_target,
                audit_id=audit_id,
                review_id=review_id,
                transport=result.transport,
                message=result.message,
                review=result.review,
            )

        review_request = self.reviews.approve(review_id)
        if not review_request:
            fallback = Intent.unknown("review request not found", {"review_id": review_id})
            return self._with_transport(
                CommandResult(status="rejected", intent=fallback, review_id=review_id, message="review request not found"),
                transport,
            )
        if review_request.status != "approved":
            return self._with_transport(
                CommandResult(
                    status="rejected",
                    intent=review_request.intent,
                    review=review_request.review,
                    review_id=review_id,
                    message=f"review request is not pending; current status is {review_request.status}",
                ),
                transport,
            )
        if review_request.review_kind == "plan" and review_request.plan_payload:
            plan = task_plan_from_payload(review_request.plan_payload)
            plan_intent = self._intent_from_task_step(plan.steps[0]) if plan.steps else review_request.intent
            approved_run_id = self._make_run_id(plan.utterance)
            execution = self.execute_task_plan(
                plan,
                dry_run=dry_run,
                transport=transport,
                review_id=review_id,
                run_id=approved_run_id,
                attempt_id=self._make_attempt_id(approved_run_id, 1, plan.selected_route_id or "approved_plan"),
            )
            result = self._with_transport(
                CommandResult(
                    status=execution_state_to_command_status(execution.status, dry_run=dry_run),
                    intent=plan_intent,
                    result=asdict(execution),
                    review=review_request.review,
                    review_id=review_id,
                    message=execution.error or "stored task plan executed",
                    execution_status=execution.execution_status,
                    acceptance_status=execution.acceptance_status,
                    overall_status=execution.overall_status,
                ),
                transport,
            )
            if result.status == "executed":
                self.reviews.consume(review_id)
            audit_id = self.audit.record(
                request=CommandRequest("", dry_run=dry_run, approve=True, review_id=review_id, transport=transport),
                intent=plan_intent,
                status=result.status,
                result=result.result,
                selected_target=None,
                message=result.message,
                review=result.review,
                review_id=review_id,
                plan_id=plan.plan_id,
                layer="permission_review",
            )
            return CommandResult(
                status=result.status,
                intent=result.intent,
                result=result.result,
                selected_target=result.selected_target,
                audit_id=audit_id,
                review_id=review_id,
                transport=result.transport,
                message=result.message,
                review=result.review,
                execution_status=result.execution_status,
                acceptance_status=result.acceptance_status,
                overall_status=result.overall_status,
            )

        request = CommandRequest(
            utterance=review_request.utterance,
            dry_run=dry_run,
            approve=True,
            review_id=review_id,
            transport=transport,
        )
        result = self._with_transport(self._execute(request, review_request.intent, review_request.review), transport)
        if result.status == "executed":
            self.reviews.consume(review_id)
        audit_id = self.audit.record(
            request=request,
            intent=review_request.intent,
            status=result.status,
            result=result.result,
            selected_target=result.selected_target,
            message=result.message,
            review=result.review,
            review_id=review_id,
        )
        return CommandResult(
            status=result.status,
            intent=result.intent,
            result=result.result,
            selected_target=result.selected_target,
            audit_id=audit_id,
            review_id=review_id,
            transport=result.transport,
            message=result.message,
            review=result.review,
        )

    def reject_review(self, review_id: str, transport: str | None = None) -> CommandResult:
        review_request = self.reviews.reject(review_id)
        if not review_request:
            fallback = Intent.unknown("review request not found", {"review_id": review_id})
            return self._with_transport(
                CommandResult(status="rejected", intent=fallback, review_id=review_id, message="review request not found"),
                transport,
            )
        if review_request.status != "rejected":
            return self._with_transport(
                CommandResult(
                    status="rejected",
                    intent=review_request.intent,
                    review=review_request.review,
                    review_id=review_id,
                    message=f"review request is not pending; current status is {review_request.status}",
                ),
                transport,
            )
        request = CommandRequest(
            utterance=review_request.utterance,
            approve=False,
            review_id=review_id,
            transport=transport,
        )
        audit_id = self.audit.record(
            request=request,
            intent=review_request.intent,
            status="rejected",
            result={"review_id": review_id, "review_status": "rejected"},
            selected_target=None,
            message="review request rejected by user",
            review=review_request.review,
            review_id=review_id,
        )
        return self._with_transport(
            CommandResult(
                status="rejected",
                intent=review_request.intent,
                result={"review_id": review_id, "review_status": "rejected"},
                audit_id=audit_id,
                review_id=review_id,
                message="review request rejected by user",
                review=review_request.review,
            ),
            transport,
        )

    def _execute(self, request: CommandRequest, intent: Intent, review) -> CommandResult:
        if intent.action == "unknown":
            return CommandResult(status="rejected", intent=intent, message=intent.reason or "unsupported request", review=review)
        if intent.requires_confirmation:
            return CommandResult(status="rejected", intent=intent, message="model requested unsupported confirmation flow", review=review)

        if intent.action == "app.list":
            apps = [asdict(app) for app in self.apps.list_apps()]
            return CommandResult(status="dry_run" if request.dry_run else "executed", intent=intent, result=apps, review=review)

        if intent.action == "app.open":
            name = str(intent.target.get("name") or intent.target.get("app") or "").strip()
            if not name:
                return CommandResult(status="rejected", intent=intent, message="missing app name", review=review)
            candidates = self.apps.resolve(name)
            if not candidates:
                if request.dry_run:
                    return CommandResult(
                        status="dry_run",
                        intent=intent,
                        message=f"intent accepted; no local application registry match for {name!r}",
                        review=review,
                    )
                return CommandResult(status="failed", intent=intent, message=f"no application matched {name!r}", review=review)
            if len(candidates) > 1 and not decisive_v2(candidates[0].name, name):
                return CommandResult(
                    status="ambiguous",
                    intent=intent,
                    result=[asdict(app) for app in candidates[:5]],
                    message=f"multiple applications matched {name!r}",
                    review=review,
                )
            app = candidates[0]
            if request.dry_run:
                return CommandResult(status="dry_run", intent=intent, selected_target=app.desktop_id, review=review)
            opened = self.apps.open_app(app)
            status = "executed" if opened.get("status") == "opened" else "failed"
            return CommandResult(status=status, intent=intent, selected_target=app.desktop_id, result=opened, review=review)

        if intent.action == "window.list":
            windows = [asdict(window) for window in self.windows.list_windows()]
            return CommandResult(status="dry_run" if request.dry_run else "executed", intent=intent, result=windows, review=review)

        if intent.action in {"window.focus", "window.minimize", "window.maximize", "window.close"}:
            name = str(intent.target.get("name") or intent.target.get("window") or "").strip()
            if not name:
                name = "current"
            candidates = self.windows.resolve(name)
            if not candidates:
                if request.dry_run:
                    return CommandResult(
                        status="dry_run",
                        intent=intent,
                        message=f"intent accepted; no local window match for {name!r}",
                        review=review,
                    )
                return CommandResult(status="failed", intent=intent, message=f"no window matched {name!r}", review=review)
            if len(candidates) > 1:
                return CommandResult(
                    status="ambiguous",
                    intent=intent,
                    result=[asdict(window) for window in candidates[:5]],
                    message=f"multiple windows matched {name!r}",
                    review=review,
                )
            window = candidates[0]
            if request.dry_run:
                return CommandResult(status="dry_run", intent=intent, selected_target=window.window_id, review=review)
            action = {
                "window.focus": self.windows.focus,
                "window.minimize": self.windows.minimize,
                "window.maximize": self.windows.maximize,
                "window.close": self.windows.close,
            }[intent.action]
            action_result = action(window)
            status = "executed" if action_result.get("status") in {"focused", "minimized", "maximized", "closed"} else "failed"
            return CommandResult(status=status, intent=intent, selected_target=window.window_id, result=action_result, review=review)

        if intent.action == "notification.send":
            title = str(intent.target.get("title") or "VibeOS").strip() or "VibeOS"
            body = str(intent.target.get("body") or intent.target.get("message") or "").strip()
            if request.dry_run:
                return CommandResult(status="dry_run", intent=intent, selected_target=title, review=review)
            sent = self.notifications.send(title, body)
            status = "executed" if sent.get("status") == "sent" else "failed"
            return CommandResult(status=status, intent=intent, selected_target=title, result=sent, review=review)

        if intent.action == "portal.open_uri":
            uri = str(intent.target.get("uri") or intent.target.get("url") or intent.target.get("name") or "").strip()
            if not uri:
                return CommandResult(status="rejected", intent=intent, message="missing URI", review=review)
            if request.dry_run:
                return CommandResult(status="dry_run", intent=intent, selected_target=uri, review=review)
            opened = self.portal.open_uri(uri)
            status = "executed" if opened.get("status") == "opened" else "failed"
            return CommandResult(status=status, intent=intent, selected_target=uri, result=opened, review=review)

        if intent.action in {"browser.open_url", "browser.search_web", "browser.open_site_search"}:
            uri = browser_semantic_uri(intent)
            if not uri:
                return CommandResult(status="rejected", intent=intent, message="missing browser target", review=review)
            if request.dry_run:
                return CommandResult(
                    status="dry_run",
                    intent=intent,
                    selected_target=uri,
                    result={"status": "dry_run", "uri": uri, "query": intent.target.get("query"), "site": intent.target.get("site")},
                    review=review,
                )
            opened = self.portal.open_uri(uri)
            record_browser_navigation(
                uri=uri,
                query=str(intent.target.get("query")) if intent.target.get("query") is not None else None,
                site=str(intent.target.get("site")) if intent.target.get("site") is not None else None,
                adapter=str(opened.get("adapter")) if isinstance(opened, dict) and opened.get("adapter") is not None else None,
                status=str(opened.get("status") or "opened") if isinstance(opened, dict) else "opened",
            )
            status = "executed" if opened.get("status") == "opened" else "failed"
            result_payload = dict(opened)
            result_payload.setdefault("uri", uri)
            if "query" in intent.target:
                result_payload["query"] = intent.target.get("query")
            if "site" in intent.target:
                result_payload["site"] = intent.target.get("site")
            return CommandResult(status=status, intent=intent, selected_target=uri, result=result_payload, review=review)

        if intent.action in {"media.search", "media.play", "media.pause"}:
            target = str(intent.target.get("query") or intent.action)
            if request.dry_run:
                return CommandResult(status="dry_run", intent=intent, selected_target=target, result={"status": "dry_run"}, review=review)
            return CommandResult(
                status="failed",
                intent=intent,
                selected_target=target,
                result={"status": "unavailable", "reason": "dedicated media execution unavailable on local host"},
                message="dedicated media execution unavailable on local host",
                review=review,
            )

        if intent.action == "clipboard.write":
            text = str(intent.target.get("text") or intent.target.get("content") or "").strip()
            if not text:
                return CommandResult(status="rejected", intent=intent, message="missing clipboard text", review=review)
            if request.dry_run:
                return CommandResult(status="dry_run", intent=intent, selected_target="clipboard", review=review)
            written = self.clipboard.write(text)
            status = "executed" if written.get("status") == "written" else "failed"
            return CommandResult(status=status, intent=intent, selected_target="clipboard", result=written, review=review)

        if intent.action == "system.status":
            return CommandResult(
                status="dry_run" if request.dry_run else "executed",
                intent=intent,
                result={
                    "portal": self.portal.status(),
                    **self.capabilities(),
                },
                review=review,
            )

        return CommandResult(status="rejected", intent=intent, message=f"unsupported action {intent.action}", review=review)

    def _intent_from_task_step(self, step) -> Intent:
        return Intent(
            action=step.action,
            target=canonicalize_target_for_action(step.action, dict(step.target)),
            reason=f"task step {step.id}",
            requires_confirmation=False,
        )

    def _ensure_task_plan(self, plan: TaskPlan) -> None:
        if not isinstance(plan, TaskPlan):
            raise TypeError("executors only accept validated TaskPlan objects, never raw utterances or arbitrary payloads")

    def _with_transport(self, result: CommandResult, transport: str | None) -> CommandResult:
        if not transport or result.transport == transport:
            return result
        return replace(result, transport=transport)


def command_status_to_execution_state(status: str) -> str:
    return {
        "executed": "succeeded",
        "dry_run": "succeeded",
        "failed": "failed",
        "rejected": "rejected",
        "review_required": "needs_user_input",
        "ambiguous": "needs_user_input",
    }.get(status, "failed")


def execution_state_to_command_status(status: str, dry_run: bool = False) -> str:
    if status == "succeeded":
        return "dry_run" if dry_run else "executed"
    if status == "failed":
        return "failed"
    if status == "rejected":
        return "rejected"
    if status in {"blocked", "needs_user_input"}:
        return "failed"
    return "failed"


def overall_status_for_outcome(*, execution_status: str, acceptance_status: str, review_status: str) -> str:
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


def max_risk_level(left: str, right: str) -> str:
    order = {"L0": 0, "L1": 1, "L2": 2, "L3": 3}
    return left if order.get(left, 99) >= order.get(right, 99) else right


def adapter_name_for_step(step, command: CommandResult) -> str:
    action = step.action
    if action == "clipboard.write":
        return str((command.result or {}).get("adapter") or "clipboard.helper")
    if action.startswith("browser."):
        return "browser.semantic"
    if action == "portal.open_uri":
        return "portal.open_uri"
    if action == "notification.send":
        return "notifications.send"
    if action.startswith("window."):
        return "windows.registry"
    if action.startswith("app."):
        return "apps.registry"
    if action == "system.status":
        return "system.status"
    return action


def adapter_status_for_command(command: CommandResult) -> str:
    if command.status in {"executed", "dry_run"}:
        return "succeeded"
    if command.status == "failed":
        return infer_failed_adapter_status(command)
    if command.status == "rejected":
        return "unsupported"
    if command.status in {"review_required", "ambiguous"}:
        return "cancelled"
    return "failed"


def infer_failed_adapter_status(command: CommandResult) -> str:
    payload = command.result if isinstance(command.result, dict) else {}
    raw_status = str(payload.get("status", "")).lower()
    if raw_status in {"timeout", "unavailable", "unsupported", "cancelled"}:
        return raw_status
    message = f"{command.message} {payload.get('error', '')}".lower()
    if "timed out" in message or "timeout" in message:
        return "timeout"
    if "not found" in message or "unavailable" in message:
        return "unavailable"
    return "failed"


def error_code_for_command(command: CommandResult) -> str | None:
    if command.status in {"executed", "dry_run"}:
        return None
    if command.status == "rejected":
        return "invalid_adapter_payload" if command.review and not command.review.allowed else "unsupported_environment"
    if command.status == "failed":
        adapter_status = infer_failed_adapter_status(command)
        if adapter_status == "timeout":
            return "adapter_timeout"
        if adapter_status == "unavailable":
            return "adapter_unavailable"
        if adapter_status == "unsupported":
            return "unsupported_environment"
        return "external_command_failed"
    if command.status in {"review_required", "ambiguous"}:
        return "external_command_failed"
    return None


def diagnostics_for_step(step, command: CommandResult, request: CommandRequest, duration_ms: int) -> dict[str, object]:
    payload = command.result if isinstance(command.result, dict) else {}
    diagnostics: dict[str, object] = {
        "action": step.action,
        "transport": request.transport,
        "dry_run": request.dry_run,
        "duration_ms": duration_ms,
        "selected_target": command.selected_target,
        "command_status": command.status,
    }
    if request.review_id is not None:
        diagnostics["review_id"] = request.review_id
    if step.action == "clipboard.write":
        diagnostics["timeout_ms"] = 10000
        diagnostics["text_length"] = len(str(step.target.get("text") or step.target.get("content") or ""))
    elif step.action == "browser.open_url":
        diagnostics["uri"] = str(step.target.get("uri") or "")
    elif step.action == "browser.search_web":
        diagnostics["query"] = str(step.target.get("query") or "")
    elif step.action == "browser.open_site_search":
        diagnostics["site"] = str(step.target.get("site") or "")
        diagnostics["query"] = str(step.target.get("query") or "")
    elif step.action == "portal.open_uri":
        diagnostics["uri"] = str(step.target.get("uri") or step.target.get("url") or step.target.get("name") or "")
    elif step.action == "notification.send":
        diagnostics["title"] = str(step.target.get("title") or "VibeOS")
    elif step.action.startswith("window."):
        diagnostics["window"] = str(step.target.get("name") or step.target.get("window") or "current")
    elif step.action.startswith("app."):
        diagnostics["app"] = str(step.target.get("name") or step.target.get("app") or "")
    if "error" in payload:
        diagnostics["adapter_error"] = payload.get("error")
    if "status" in payload:
        diagnostics["adapter_result_status"] = payload.get("status")
    return diagnostics


def decisive(candidate_name: str, query: str) -> bool:
    candidate = candidate_name.strip().lower()
    query_norm = query.strip().lower()
    return candidate == query_norm or query_norm in {"browser", "\u6d4f\u89c8\u5668", "terminal", "\u7ec8\u7aef"}


def summarize_verification_status(results) -> str | None:
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


def decisive_v2(candidate_name: str, query: str) -> bool:
    candidate = candidate_name.strip().lower()
    query_norm = query.strip().lower()
    return candidate == query_norm or query_norm in {"browser", "\u6d4f\u89c8\u5668", "terminal", "\u7ec8\u7aef"}


def decisive(candidate_name: str, query: str) -> bool:
    candidate = candidate_name.strip().lower()
    query_norm = query.strip().lower()
    return candidate == query_norm or query_norm in {"browser", "浏览器", "terminal", "终端"}
