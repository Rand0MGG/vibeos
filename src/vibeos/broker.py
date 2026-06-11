from __future__ import annotations

import os
from dataclasses import replace
from dataclasses import asdict
from hashlib import sha256
from time import perf_counter

from .acceptance import AcceptanceEngine
from .agent_runtime import AgentRuntime, EnvironmentProfile
from .app_fixtures import AppSearchFixture
from .apps import AppRegistry
from .assistant_semantics import AssistantIntent, InteractionSurface, assistant_intent_to_payload
from .audit import AuditLog
from .browser_state import browser_attempt_scope, browser_context_snapshot, record_browser_navigation
from .capabilities import capability_payload, executable_actions, permission_summary
from .clipboard import ClipboardAdapter
from .domain_models import ObservationRequest
from .domain_registry import default_domain_registry
from .execution_graph import execute_plan_graph
from .failure_classifier import FailureClassifier
from .goal_models import GoalSpec, GoalSynthesisProvenance
from .intent import IntentBroker, OpenAICompatibleIntentBroker, RuleIntentBroker
from .notifications import NotificationAdapter
from .models import CommandRequest, CommandResult, Intent, utc_now_iso
from .observation import resolve_post_execution_observation
from .planner import browser_semantic_uri, plan_turn
from .permissions import PermissionPolicy
from .portal import PortalAdapter
from .replanner import EvidenceDrivenReplanner, Replanner
from .reviews import ReviewStore, review_to_payload
from .strategy import StrategyCandidate, StrategyConstraint, StrategyStep
from .task_models import (
    AgentRun,
    DisplayFields,
    ExpectedState,
    FailureClassification,
    PlanAttempt,
    PlanExecutionResult,
    ReplanDecision,
    StepPrecondition,
    StepProvenance,
    StepExecutionResult,
    StepReviewRecord,
    TaskPlan,
    TaskPlanReviewResult,
    TaskRoute,
    TaskStep,
    canonicalize_target_for_action,
    task_plan_from_payload,
)
from .task_validation import validate_plan
from .tool_protocol import ToolRegistry, ToolResult, ToolSpec
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
        browser_site_catalog: dict[str, str] | None = None,
        browser_search_catalog: dict[str, dict[str, object]] | None = None,
        app_fixture_catalog: dict[str, AppSearchFixture] | None = None,
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
        self.browser_site_catalog = browser_site_catalog or {}
        self.browser_search_catalog = browser_search_catalog or {}
        self.app_fixture_catalog = app_fixture_catalog or {}
        self.agent_runtime = AgentRuntime(self._build_v06_tool_registry())
        self.agent_session = self.agent_runtime.create_session("broker_session")

    def capabilities(self) -> dict[str, object]:
        return {
            "capabilities": executable_actions(),
            "capability_details": capability_payload(),
            "permission_policy": permission_summary(),
        }

    def pending_reviews(self) -> list[dict[str, object]]:
        return [review_to_payload(request) for request in self.reviews.list_pending()]

    def review_task_plan(self, plan: TaskPlan, stored_payload: dict[str, object] | None = None) -> TaskPlanReviewResult:
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
            review_request = self.reviews.create_plan_review(
                plan.utterance,
                stored_payload if stored_payload is not None else asdict(plan),
                TaskPlanReviewResult(plan_id=plan.plan_id, status="review_required", max_risk_level=max_risk, step_reviews=tuple(step_reviews)),
            )
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
        v06_result = self._run_v06_runtime_bridge(request, planning)
        if v06_result is not None:
            return v06_result
        return self._run_task_plan_loop(request, planning)

    def _run_v06_runtime_bridge(self, request: CommandRequest, planning) -> CommandResult | None:
        if planning.goal_synthesis is None or planning.goal_synthesis.goal_spec is None:
            return None
        goal_spec = planning.goal_synthesis.goal_spec
        strategies = self._build_v06_strategy_candidates(tuple(planning.candidates), goal_spec)
        if not strategies:
            return None
        environment = self._build_v06_environment_profile(request, planning)
        if goal_spec.goal_id not in self.agent_session.goals:
            self.agent_runtime.start_goal(self.agent_session.session_id, goal_spec)
        selection = self.agent_runtime.recovery_policy.select_strategy(
            strategies=strategies,
            constraints=StrategyConstraint(),
            environment=environment,
            attempts=(),
            last_failure_class="none",
        )
        selected_strategy = next((candidate for candidate in strategies if candidate.strategy_id == selection.selected_strategy_id), None)
        selected_plan = selected_strategy.task_plan if selected_strategy is not None else planning.plan
        if selected_plan is None:
            return None
        stored_review_payload = self._v06_stored_review_payload(
            plan=selected_plan,
            goal_id=goal_spec.goal_id,
            strategies=strategies,
            selected_strategy_id=selected_strategy.strategy_id if selected_strategy is not None else "",
            environment=environment,
        )
        plan_review = self.review_task_plan(selected_plan, stored_payload=stored_review_payload)
        if plan_review.status in {"review_required", "rejected"}:
            gate_result = self.agent_runtime.gate_goal(
                session_id=self.agent_session.session_id,
                goal_id=goal_spec.goal_id,
                utterance=request.utterance,
                strategies=strategies,
                selected_strategy_id=selected_strategy.strategy_id if selected_strategy is not None else "",
                reason=plan_review.message,
                terminal_status="needs_review" if plan_review.status == "review_required" else "failed",
            )
            payload = self._planning_payload(planning)
            payload["environment_profile"] = asdict(environment)
            payload["goal_runtime"] = asdict(gate_result.goal_runtime)
            payload["goal_turn"] = asdict(gate_result.turn)
            payload["strategy_candidates"] = [self._strategy_payload(item) for item in strategies]
            payload["selected_strategy_id"] = gate_result.selected_strategy_id
            payload["run_ledger"] = asdict(gate_result.ledger)
            payload["plan"] = asdict(selected_plan)
            payload["plan_review"] = asdict(plan_review)
            if request.debug:
                runtime_debug = dict(gate_result.debug_payload)
                runtime_debug["environment_profile"] = asdict(environment)
                runtime_debug["provider_artifacts"] = list(payload["debug_trace"].get("model_exchange", ()))
                payload["debug_trace"]["runtime_v0_6"] = runtime_debug
            payload["run"] = asdict(
                AgentRun(
                    run_id=self._make_run_id(request.utterance),
                    goal_id=gate_result.goal_runtime.goal_id,
                    utterance=request.utterance,
                    status=self._run_status_for_overall("needs_review" if plan_review.status == "review_required" else "failed"),
                    selected_transport=request.transport,
                    attempt_ids=(),
                    final_outcome="needs_review" if plan_review.status == "review_required" else "failed",
                )
            )
            payload["attempts"] = []
            review_request = self.reviews.get(plan_review.review_id or "") if plan_review.status == "review_required" else None
            intent = self._intent_from_task_step(selected_plan.steps[0]) if selected_plan.steps else Intent.unknown("task plan contains no executable steps")
            return self._with_transport(
                CommandResult(
                    status="review_required" if plan_review.status == "review_required" else "rejected",
                    intent=intent,
                    result=payload,
                    review_id=plan_review.review_id,
                    review=review_request.review if review_request else None,
                    message=plan_review.message,
                    execution_status="not_started",
                    acceptance_status="skipped",
                    overall_status="needs_review" if plan_review.status == "review_required" else "failed",
                ),
                request.transport,
            )
        result = self.agent_runtime.continue_goal(
            session_id=self.agent_session.session_id,
            goal_id=goal_spec.goal_id,
            utterance=request.utterance,
            strategies=strategies,
            environment=environment,
        )
        selected_strategy = next((candidate for candidate in strategies if candidate.strategy_id == result.selected_strategy_id), None)
        selected_plan = selected_strategy.task_plan if selected_strategy is not None else planning.plan
        payload = self._planning_payload(planning)
        payload["environment_profile"] = asdict(environment)
        payload["goal_runtime"] = asdict(result.goal_runtime)
        payload["goal_turn"] = asdict(result.turn)
        payload["strategy_candidates"] = [self._strategy_payload(item) for item in strategies]
        payload["selected_strategy_id"] = result.selected_strategy_id
        payload["run_ledger"] = asdict(result.ledger)
        payload["plan"] = asdict(selected_plan) if selected_plan is not None else None
        if request.debug:
            runtime_debug = dict(result.debug_payload)
            runtime_debug["environment_profile"] = asdict(environment)
            runtime_debug["provider_artifacts"] = list(payload["debug_trace"].get("model_exchange", ()))
            payload["debug_trace"]["runtime_v0_6"] = runtime_debug
        if selected_plan is not None:
            payload["plan_review"] = asdict(plan_review)
        execution_payload, selected_target = self._v06_execution_payload(result, selected_strategy)
        payload["preview" if request.dry_run else "execution"] = execution_payload
        overall_status = self._v06_overall_status(request, result)
        execution_status = "dry_run" if request.dry_run else execution_payload["execution_status"]
        acceptance_status = str(execution_payload.get("acceptance_status", "skipped"))
        status = self._v06_command_status(request, overall_status)
        message = self._v06_result_message(request, result, overall_status)
        run_id = self._make_run_id(request.utterance)
        current_turn_attempts = tuple(attempt for attempt in result.ledger.attempts if attempt.turn_id == result.turn.turn_id)
        payload["run"] = asdict(
            AgentRun(
                run_id=run_id,
                goal_id=result.goal_runtime.goal_id,
                utterance=request.utterance,
                status=self._run_status_for_overall(overall_status),
                selected_transport=request.transport,
                attempt_ids=tuple(item.attempt_id for item in current_turn_attempts),
                final_outcome=overall_status,
            )
        )
        payload["attempts"] = [self._v06_attempt_payload(item) for item in current_turn_attempts]
        intent = self._intent_from_task_step(selected_plan.steps[0]) if selected_plan and selected_plan.steps else Intent.unknown("task plan contains no executable steps")
        return self._with_transport(
            CommandResult(
                status=status,
                intent=intent,
                result=payload,
                selected_target=selected_target,
                message=message,
                execution_status=execution_status,
                acceptance_status=acceptance_status,
                overall_status=overall_status,
            ),
            request.transport,
        )

    def _build_v06_environment_profile(self, request: CommandRequest, planning) -> EnvironmentProfile:
        has_app_candidates = any(
            candidate.routes and candidate.routes[0].domain_id == "apps"
            for candidate in planning.candidates
        )
        desktop_available = request.dry_run or not has_app_candidates or bool(self.apps.list_apps())
        search_policy = "browser_first" if planning.plan and planning.plan.routes and planning.plan.routes[0].domain_id == "browser" else "balanced"
        return EnvironmentProfile(
            platform="linux" if os.name == "posix" else "windows",
            transport_mode=request.transport or "local",
            daemon_available=(request.transport or "") in {"dbus", "http"},
            desktop_integration_available=desktop_available,
            connectivity_limitations="offline",
            deployment_profile="broker-main-path",
            region="local",
            search_policy=search_policy,
            dry_run=request.dry_run,
            browser_site_catalog=dict(self.browser_site_catalog),
            browser_search_catalog={key: dict(value) for key, value in self.browser_search_catalog.items()},
            app_fixture_catalog=dict(self.app_fixture_catalog),
        )

    def _build_v06_strategy_candidates(self, candidates: tuple[TaskPlan, ...], goal_spec: GoalSpec) -> tuple[StrategyCandidate, ...]:
        strategies: list[StrategyCandidate] = []
        for index, candidate in enumerate(candidates):
            strategy = self._task_plan_to_v06_strategy(candidate, goal_spec, index)
            if strategy is None:
                continue
            strategies.append(strategy)
        strategies.extend(self._synthetic_v07_strategies(goal_spec, strategies))
        return tuple(strategies)

    def _task_plan_to_v06_strategy(self, plan: TaskPlan, goal_spec: GoalSpec | str, index: int) -> StrategyCandidate | None:
        if not plan.routes or not plan.steps:
            return None
        reviews = tuple(self.policy.review(self._intent_from_task_step(step)) for step in plan.steps)
        if any(not review.allowed for review in reviews):
            return None
        review_required = any(review.review_required for review in reviews)
        route = plan.routes[0]
        first_step = plan.steps[0]
        priority = float(route.score or max(1, len(plan.routes) - index))
        resolved_goal_id = goal_spec.goal_id if isinstance(goal_spec, GoalSpec) else goal_spec
        assistant_intent = goal_spec.assistant_intent if isinstance(goal_spec, GoalSpec) else None
        if route.domain_id == "apps" and first_step.action == "app.open":
            name = str(first_step.target.get("name") or first_step.target.get("app") or "")
            if not name:
                return None
            return StrategyCandidate(
                strategy_id=f"strategy_{route.id}",
                goal_id=resolved_goal_id,
                title=plan.display.goal or name,
                route_id=route.id,
                capability_surface="desktop-linux",
                task_plan=plan,
                steps=(
                    StrategyStep(tool_id="apps.resolve_installed", input_payload={"name": name, "task_step_id": first_step.id}, task_step_id=first_step.id),
                    StrategyStep(tool_id="app.open", input_payload={"name": name, "task_step_id": first_step.id}, task_step_id=first_step.id),
                ),
                interaction_surface="native_action",
                priority=priority,
                requires_desktop_integration=True,
                metadata={"acceptance_mode": "action_only", "review_required": review_required},
            )
        if route.domain_id == "browser" and first_step.action in {"browser.open_url", "browser.search_web", "browser.open_site_search"}:
            tool_id = first_step.action
            steps = [
                StrategyStep(tool_id=tool_id, input_payload={**dict(first_step.target), "task_step_id": first_step.id}, task_step_id=first_step.id),
                StrategyStep(tool_id="browser.observe_context", input_payload={"task_step_id": first_step.id}),
            ]
            verifier_ids = route.default_verifier_ids
            interaction_surface: InteractionSurface = "native_action"
            if assistant_intent is not None and assistant_intent.objective_kind == "open_named_website" and tool_id == "browser.search_web" and (self.browser_site_catalog or self.browser_search_catalog):
                steps.append(StrategyStep(tool_id="browser.verify_goal_page_identity", input_payload={"name": assistant_intent.target.display_name, "task_step_id": first_step.id}))
            elif "browser_url_opened" in verifier_ids:
                steps.append(StrategyStep(tool_id="browser.verify_url_opened", input_payload={**dict(first_step.target), "task_step_id": first_step.id}))
            elif "browser_search_route_completed" in verifier_ids:
                steps.append(StrategyStep(tool_id="browser.verify_query", input_payload={**dict(first_step.target), "task_step_id": first_step.id}))
            return StrategyCandidate(
                strategy_id=f"strategy_{route.id}",
                goal_id=resolved_goal_id,
                title=plan.display.goal or route.id,
                route_id=route.id,
                capability_surface="browser",
                task_plan=plan,
                steps=tuple(steps),
                interaction_surface=interaction_surface,
                priority=priority + 1.0,
                metadata={"acceptance_mode": "verification_required", "review_required": review_required},
            )
        if route.domain_id == "window_management" and first_step.action == "window.list":
            return StrategyCandidate(
                strategy_id=f"strategy_{route.id}",
                goal_id=resolved_goal_id,
                title=plan.display.goal or route.id,
                route_id=route.id,
                capability_surface="desktop-linux",
                task_plan=plan,
                steps=(StrategyStep(tool_id="window.list", input_payload={"task_step_id": first_step.id}, task_step_id=first_step.id),),
                interaction_surface="native_action",
                priority=priority,
                metadata={"acceptance_mode": "action_only", "review_required": review_required},
            )
        if route.domain_id == "window_management" and first_step.action in {"window.focus", "window.minimize", "window.maximize", "window.close"}:
            name = str(first_step.target.get("name") or first_step.target.get("window") or "current")
            return StrategyCandidate(
                strategy_id=f"strategy_{route.id}",
                goal_id=resolved_goal_id,
                title=plan.display.goal or route.id,
                route_id=route.id,
                capability_surface="desktop-linux",
                task_plan=plan,
                steps=(
                    StrategyStep(tool_id="window.resolve", input_payload={"name": name, "task_step_id": first_step.id}, task_step_id=first_step.id),
                    StrategyStep(tool_id=first_step.action, input_payload={"name": name, "task_step_id": first_step.id}, task_step_id=first_step.id),
                ),
                interaction_surface="native_action",
                priority=priority,
                requires_desktop_integration=True,
                metadata={"acceptance_mode": "action_only", "review_required": review_required},
            )
        if route.domain_id == "notification" and first_step.action == "notification.send":
            return StrategyCandidate(
                strategy_id=f"strategy_{route.id}",
                goal_id=resolved_goal_id,
                title=plan.display.goal or route.id,
                route_id=route.id,
                capability_surface="desktop-linux",
                task_plan=plan,
                steps=(StrategyStep(tool_id="notification.send", input_payload={**dict(first_step.target), "task_step_id": first_step.id}, task_step_id=first_step.id),),
                interaction_surface="native_action",
                priority=priority,
                metadata={"acceptance_mode": "action_only", "review_required": review_required},
            )
        if route.domain_id == "system_observation" and first_step.action == "system.status":
            return StrategyCandidate(
                strategy_id=f"strategy_{route.id}",
                goal_id=resolved_goal_id,
                title=plan.display.goal or route.id,
                route_id=route.id,
                capability_surface="desktop-linux",
                task_plan=plan,
                steps=(StrategyStep(tool_id="system.status", input_payload={"task_step_id": first_step.id}, task_step_id=first_step.id),),
                interaction_surface="native_action",
                priority=priority,
                metadata={"acceptance_mode": "action_only", "review_required": review_required},
            )
        if route.domain_id == "clipboard" and first_step.action == "clipboard.write":
            return StrategyCandidate(
                strategy_id=f"strategy_{route.id}",
                goal_id=resolved_goal_id,
                title=plan.display.goal or route.id,
                route_id=route.id,
                capability_surface="desktop-linux",
                task_plan=plan,
                steps=(StrategyStep(tool_id="clipboard.write", input_payload={**dict(first_step.target), "task_step_id": first_step.id}, task_step_id=first_step.id),),
                interaction_surface="native_action",
                priority=priority,
                metadata={"acceptance_mode": "action_only", "review_required": review_required},
            )
        return None

    def _synthetic_v07_strategies(
        self,
        goal_spec: GoalSpec,
        existing: list[StrategyCandidate],
    ) -> list[StrategyCandidate]:
        assistant_intent = goal_spec.assistant_intent
        if assistant_intent is None:
            return []
        synthetic: list[StrategyCandidate] = []
        existing_route_ids = {item.route_id for item in existing}
        if assistant_intent.objective_kind == "open_named_website" and (self.browser_site_catalog or self.browser_search_catalog):
            if "browser_named_direct_open_route" not in existing_route_ids:
                synthetic.append(self._browser_named_direct_open_strategy(goal_spec, assistant_intent))
            if "browser_search_followup_route" not in existing_route_ids:
                synthetic.append(self._browser_search_followup_strategy(goal_spec, assistant_intent))
        if assistant_intent.objective_kind == "in_app_search" and self.app_fixture_catalog:
            if "app_structured_search_route" not in existing_route_ids:
                synthetic.append(self._app_structured_search_strategy(goal_spec, assistant_intent))
            if "app_shortcut_search_route" not in existing_route_ids:
                synthetic.append(self._app_shortcut_search_strategy(goal_spec, assistant_intent))
        return synthetic

    def _browser_named_direct_open_strategy(self, goal_spec: GoalSpec, assistant_intent: AssistantIntent) -> StrategyCandidate:
        target_name = assistant_intent.target.display_name
        step = TaskStep(
            id="browser_open_named_target",
            action="browser.open_named_target",
            capability_id="browser.open_named_target",
            target={"name": target_name},
            expected_state=ExpectedState(kind="named_site_open_requested", fields={"name": target_name}),
            preconditions=(StepPrecondition(kind="capability_available", capability_id="browser.open_named_target"),),
            provenance=StepProvenance(source_span_id="span_1", planner="v0.7_assistant_intent"),
        )
        plan = TaskPlan(
            schema_version="v0.5",
            plan_id=self._make_run_id(f"{goal_spec.goal_id}:browser_named_direct_open"),
            utterance=goal_spec.goal_text,
            display=DisplayFields(
                goal=f"open {target_name}",
                explanation="Resolve the named website target directly before falling back to weaker browser interaction.",
            ),
            selected_route_id="browser_named_direct_open_route",
            routes=(TaskRoute(id="browser_named_direct_open_route", score=9.0, domain_id="browser", required_capabilities=("browser.open_named_target",)),),
            steps=(step,),
            provenance={"planner": "v0.7_assistant_intent", "assistant_intent": assistant_intent_to_payload(assistant_intent)},
        )
        return StrategyCandidate(
            strategy_id="strategy_browser_named_direct_open_route",
            goal_id=goal_spec.goal_id,
            title=plan.display.goal,
            route_id=plan.selected_route_id,
            capability_surface="browser",
            task_plan=plan,
            steps=(
                StrategyStep(tool_id="browser.resolve_named_target", input_payload={"name": target_name, "task_step_id": step.id}, task_step_id=step.id),
                StrategyStep(tool_id="browser.open_resolved_target", input_payload={"name": target_name, "task_step_id": step.id}, task_step_id=step.id),
                StrategyStep(tool_id="browser.observe_context", input_payload={"task_step_id": step.id}),
                StrategyStep(tool_id="browser.verify_goal_page_identity", input_payload={"name": target_name, "task_step_id": step.id}, task_step_id=step.id),
            ),
            interaction_surface="native_action",
            priority=12.0,
            metadata={"assistant_intent": assistant_intent_to_payload(assistant_intent), "acceptance_mode": "verification_required", "enable_surface_downgrade": True},
        )

    def _browser_search_followup_strategy(self, goal_spec: GoalSpec, assistant_intent: AssistantIntent) -> StrategyCandidate:
        query = assistant_intent.target.display_name
        step = TaskStep(
            id="browser_search_followup",
            action="browser.search_web",
            capability_id="browser.search_web",
            target={"query": query},
            expected_state=ExpectedState(kind="search_results_available", fields={"query": query}),
            preconditions=(StepPrecondition(kind="capability_available", capability_id="browser.search_web"),),
            provenance=StepProvenance(source_span_id="span_1", planner="v0.7_assistant_intent"),
        )
        plan = TaskPlan(
            schema_version="v0.5",
            plan_id=self._make_run_id(f"{goal_spec.goal_id}:browser_search_followup"),
            utterance=goal_spec.goal_text,
            display=DisplayFields(
                goal=f"search and continue to {query}",
                explanation="Use browser search as a weaker strategy, then follow the resolved official result before accepting goal completion.",
            ),
            selected_route_id="browser_search_followup_route",
            routes=(TaskRoute(id="browser_search_followup_route", score=8.0, domain_id="browser", required_capabilities=("browser.search_web",)),),
            steps=(step,),
            provenance={"planner": "v0.7_assistant_intent", "assistant_intent": assistant_intent_to_payload(assistant_intent)},
        )
        return StrategyCandidate(
            strategy_id="strategy_browser_search_followup_route",
            goal_id=goal_spec.goal_id,
            title=plan.display.goal,
            route_id=plan.selected_route_id,
            capability_surface="browser",
            task_plan=plan,
            steps=(
                StrategyStep(tool_id="browser.search_web", input_payload={"query": query, "task_step_id": step.id}, task_step_id=step.id),
                StrategyStep(tool_id="browser.observe_context", input_payload={"task_step_id": step.id}),
                StrategyStep(tool_id="browser.observe_search_results", input_payload={"query": query, "task_step_id": step.id}, task_step_id=step.id),
                StrategyStep(tool_id="browser.follow_search_result", input_payload={"query": query, "task_step_id": step.id}, task_step_id=step.id),
                StrategyStep(tool_id="browser.observe_context", input_payload={"task_step_id": step.id}),
                StrategyStep(tool_id="browser.verify_goal_page_identity", input_payload={"name": query, "task_step_id": step.id}, task_step_id=step.id),
            ),
            interaction_surface="structured_ui_action",
            priority=11.0,
            metadata={"assistant_intent": assistant_intent_to_payload(assistant_intent), "acceptance_mode": "verification_required", "enable_surface_downgrade": True},
        )

    def _app_structured_search_strategy(self, goal_spec: GoalSpec, assistant_intent: AssistantIntent) -> StrategyCandidate:
        app_name = str(assistant_intent.target.app_name or "")
        query = str(assistant_intent.target.query_text or assistant_intent.target.display_name)
        step = TaskStep(
            id="app_structured_search",
            action="app.search_history",
            capability_id="app.search_history",
            target={"app": app_name, "query": query},
            expected_state=ExpectedState(kind="search_results_available", fields={"query": query}),
            preconditions=(StepPrecondition(kind="capability_available", capability_id="app.search_history"),),
            provenance=StepProvenance(source_span_id="span_1", planner="v0.7_assistant_intent"),
        )
        plan = TaskPlan(
            schema_version="v0.5",
            plan_id=self._make_run_id(f"{goal_spec.goal_id}:app_structured_search"),
            utterance=goal_spec.goal_text,
            display=DisplayFields(
                goal=f"search {app_name} for {query}",
                explanation="Prefer structured UI controls before falling back to bounded computer-use search actions.",
            ),
            selected_route_id="app_structured_search_route",
            routes=(TaskRoute(id="app_structured_search_route", score=9.0, domain_id="app_interaction", required_capabilities=("app.search_history",)),),
            steps=(step,),
            provenance={"planner": "v0.7_assistant_intent", "assistant_intent": assistant_intent_to_payload(assistant_intent)},
        )
        return StrategyCandidate(
            strategy_id="strategy_app_structured_search_route",
            goal_id=goal_spec.goal_id,
            title=plan.display.goal,
            route_id=plan.selected_route_id,
            capability_surface="desktop-linux",
            task_plan=plan,
            steps=(
                StrategyStep(tool_id="app.fixture.locate_search_control", input_payload={"app": app_name, "task_step_id": step.id}, task_step_id=step.id),
                StrategyStep(tool_id="app.fixture.enter_search_query", input_payload={"app": app_name, "query": query, "task_step_id": step.id}, task_step_id=step.id),
                StrategyStep(tool_id="app.fixture.observe_results", input_payload={"app": app_name, "task_step_id": step.id}, task_step_id=step.id),
                StrategyStep(tool_id="app.fixture.verify_target_presence", input_payload={"app": app_name, "query": query, "task_step_id": step.id}, task_step_id=step.id),
            ),
            interaction_surface="structured_ui_action",
            priority=10.0,
            requires_desktop_integration=True,
            metadata={"assistant_intent": assistant_intent_to_payload(assistant_intent), "acceptance_mode": "verification_required", "enable_surface_downgrade": True},
        )

    def _app_shortcut_search_strategy(self, goal_spec: GoalSpec, assistant_intent: AssistantIntent) -> StrategyCandidate:
        app_name = str(assistant_intent.target.app_name or "")
        query = str(assistant_intent.target.query_text or assistant_intent.target.display_name)
        step = TaskStep(
            id="app_shortcut_search",
            action="app.search_history",
            capability_id="app.search_history",
            target={"app": app_name, "query": query},
            expected_state=ExpectedState(kind="search_results_available", fields={"query": query}),
            preconditions=(StepPrecondition(kind="capability_available", capability_id="app.search_history"),),
            provenance=StepProvenance(source_span_id="span_1", planner="v0.7_assistant_intent"),
        )
        plan = TaskPlan(
            schema_version="v0.5",
            plan_id=self._make_run_id(f"{goal_spec.goal_id}:app_shortcut_search"),
            utterance=goal_spec.goal_text,
            display=DisplayFields(
                goal=f"fallback search {app_name} for {query}",
                explanation="Use a bounded shortcut-driven interaction when structured UI controls are unavailable.",
            ),
            selected_route_id="app_shortcut_search_route",
            routes=(TaskRoute(id="app_shortcut_search_route", score=8.0, domain_id="app_interaction", required_capabilities=("app.search_history",)),),
            steps=(step,),
            provenance={"planner": "v0.7_assistant_intent", "assistant_intent": assistant_intent_to_payload(assistant_intent)},
        )
        return StrategyCandidate(
            strategy_id="strategy_app_shortcut_search_route",
            goal_id=goal_spec.goal_id,
            title=plan.display.goal,
            route_id=plan.selected_route_id,
            capability_surface="desktop-linux",
            task_plan=plan,
            steps=(
                StrategyStep(tool_id="app.fixture.activate_search_shortcut", input_payload={"app": app_name, "task_step_id": step.id}, task_step_id=step.id),
                StrategyStep(tool_id="app.fixture.enter_search_query", input_payload={"app": app_name, "query": query, "task_step_id": step.id}, task_step_id=step.id),
                StrategyStep(tool_id="app.fixture.observe_results", input_payload={"app": app_name, "task_step_id": step.id}, task_step_id=step.id),
                StrategyStep(tool_id="app.fixture.verify_target_presence", input_payload={"app": app_name, "query": query, "task_step_id": step.id}, task_step_id=step.id),
            ),
            interaction_surface="computer_use_action",
            priority=7.0,
            requires_desktop_integration=True,
            metadata={"assistant_intent": assistant_intent_to_payload(assistant_intent), "acceptance_mode": "verification_required", "enable_surface_downgrade": True},
        )

    def _strategy_payload(self, strategy: StrategyCandidate) -> dict[str, object]:
        return {
            "strategy_id": strategy.strategy_id,
            "route_id": strategy.route_id,
            "capability_surface": strategy.capability_surface,
            "interaction_surface": strategy.interaction_surface,
            "task_plan_id": strategy.task_plan.plan_id,
            "tool_ids": list(strategy.tool_ids),
            "priority": strategy.priority,
        }

    def _v06_stored_review_payload(
        self,
        *,
        plan: TaskPlan,
        goal_id: str,
        strategies: tuple[StrategyCandidate, ...],
        selected_strategy_id: str,
        environment: EnvironmentProfile,
    ) -> dict[str, object]:
        payload = asdict(plan)
        payload["v0_6_runtime"] = {
            "goal_id": goal_id,
            "selected_strategy_id": selected_strategy_id,
            "strategy_candidates": [self._strategy_payload(item) for item in strategies],
            "environment_profile": asdict(environment),
        }
        return payload

    def _v06_execution_payload(self, result, selected_strategy: StrategyCandidate | None) -> tuple[dict[str, object], str | None]:
        current_attempts = [attempt for attempt in result.ledger.attempts if attempt.turn_id == result.turn.turn_id]
        last_attempt = current_attempts[-1] if current_attempts else None
        verification_results: list[dict[str, object]] = []
        step_results: list[dict[str, object]] = []
        selected_target: str | None = None
        if last_attempt is not None:
            for invocation in last_attempt.tool_invocations:
                output = dict(invocation.output_payload)
                diagnostics = dict(invocation.evidence)
                if output.get("selected_target") is not None:
                    diagnostics["selected_target"] = output.get("selected_target")
                diagnostics["adapter_result_status"] = output.get("adapter_status") or invocation.status
                if invocation.family == "action":
                    step_results.append(
                        {
                            "step_id": str(invocation.input_payload.get("task_step_id") or invocation.tool_id),
                            "layer": invocation.family,
                            "status": "succeeded" if invocation.status == "succeeded" else "failed",
                            "adapter": output.get("adapter") or invocation.tool_id,
                            "capability_id": self._capability_id_for_tool(invocation.tool_id),
                            "attempt": 1,
                            "duration_ms": None,
                            "adapter_status": output.get("adapter_status") or invocation.status,
                            "diagnostics": diagnostics,
                            "error_code": self._v06_error_code(invocation.failure_class) if invocation.failure_class != "none" else None,
                            "result": output,
                            "error": invocation.message or None,
                            "audit_id": None,
                        }
                    )
                if invocation.family == "verifier":
                    verification_results.append(
                        {
                            "verifier_id": invocation.tool_id.replace("browser.verify_", "browser_"),
                            "status": "passed" if invocation.status == "succeeded" else "failed",
                            "message": invocation.message,
                            "details": diagnostics,
                        }
                    )
                selected_target = str(output.get("selected_target") or output.get("uri") or selected_target or "") or selected_target
        acceptance_status = "skipped"
        execution_status = "failed"
        execution_state = "failed"
        if result.terminal_outcome.status == "completed":
            acceptance_status = "passed"
            execution_status = "succeeded"
            execution_state = "succeeded"
        elif result.terminal_outcome.status == "incomplete":
            acceptance_status = "indeterminate"
            execution_status = "succeeded"
            execution_state = "succeeded"
        elif last_attempt is not None and last_attempt.failure_class in {"semantic_mismatch", "acceptance_failed"}:
            acceptance_status = "failed"
            execution_status = "failed"
        verification_status = "skipped"
        if verification_results:
            verification_status = "passed" if all(item["status"] == "passed" for item in verification_results) else "failed"
        elif selected_strategy is not None and selected_strategy.capability_surface == "browser":
            verification_status = "failed" if result.terminal_outcome.status in {"failed", "incomplete"} else "skipped"
        return (
            {
                "plan_id": selected_strategy.task_plan.plan_id if selected_strategy is not None else "",
                "status": execution_state,
                "step_results": step_results,
                "verification_results": verification_results,
                "verification_status": verification_status,
                "execution_status": execution_status,
                "acceptance_status": acceptance_status,
                "overall_status": result.terminal_outcome.status,
                "acceptance_result": {
                    "status": acceptance_status,
                    "message": result.terminal_outcome.reason,
                    "observation_receipt": None,
                },
                "error": None if result.terminal_outcome.status in {"completed", "incomplete"} else result.terminal_outcome.reason,
            },
            selected_target,
        )

    def _v06_overall_status(self, request: CommandRequest, result) -> str:
        if request.dry_run:
            return "dry_run"
        terminal_status = result.terminal_outcome.status
        if terminal_status == "completed":
            return "completed"
        if terminal_status == "incomplete":
            return "incomplete"
        if terminal_status == "blocked":
            return "blocked"
        if terminal_status == "needs_user_input":
            return "needs_user_input"
        return "failed"

    def _v06_command_status(self, request: CommandRequest, overall_status: str) -> str:
        if request.dry_run:
            return "dry_run"
        if overall_status in {"completed", "incomplete"}:
            return "executed"
        return "failed"

    def _v06_result_message(self, request: CommandRequest, result, overall_status: str) -> str:
        if request.dry_run:
            return "task plan resolved without executing real adapters"
        if overall_status == "completed":
            return "task goal completed"
        if overall_status == "incomplete":
            return result.terminal_outcome.reason or "execution completed but acceptance evidence remains incomplete"
        if overall_status == "blocked":
            return result.terminal_outcome.reason or "task goal remains blocked in the current environment"
        return result.terminal_outcome.reason or "task goal did not complete"

    def _v06_attempt_payload(self, attempt) -> dict[str, object]:
        return {
            "attempt_id": attempt.attempt_id,
            "run_id": attempt.turn_id,
            "attempt_index": 1,
            "trigger": attempt.trigger,
            "selected_route_id": attempt.route_id,
            "plan_id": attempt.task_plan_id,
            "capability_surface": attempt.capability_surface,
            "interaction_surface": attempt.interaction_surface,
            "tool_ids": [invocation.tool_id for invocation in attempt.tool_invocations],
            "failure": {"failure_class": attempt.failure_class, "message": attempt.message},
            "outcome_status": attempt.outcome_status,
        }

    def _capability_id_for_tool(self, tool_id: str) -> str:
        mapping = {
            "apps.resolve_installed": "app.resolve",
            "app.open": "app.open",
            "app.fixture.locate_search_control": "app.search_history",
            "app.fixture.activate_search_shortcut": "app.search_history",
            "app.fixture.enter_search_query": "app.search_history",
            "app.fixture.observe_results": "app.search_history",
            "app.fixture.verify_target_presence": "app.search_history",
            "browser.open_url": "browser.open_url",
            "browser.open_resolved_target": "browser.open_named_target",
            "browser.resolve_named_target": "browser.open_named_target",
            "browser.search_web": "browser.search_web",
            "browser.open_site_search": "browser.open_site_search",
            "browser.observe_context": "browser.observe_context",
            "browser.observe_search_results": "browser.search_web",
            "browser.follow_search_result": "browser.search_web",
            "browser.verify_goal_page_identity": "browser.open_named_target",
            "browser.verify_url_opened": "browser_url_opened",
            "browser.verify_query": "browser_search_route_completed",
            "window.list": "window.list",
            "window.focus": "window.focus",
            "window.minimize": "window.minimize",
            "window.maximize": "window.maximize",
            "window.close": "window.close",
            "notification.send": "notification.send",
            "clipboard.write": "clipboard.write",
            "system.status": "system.status",
        }
        return mapping.get(tool_id, tool_id)

    def _v06_error_code(self, failure_class: str) -> str:
        mapping = {
            "tool_timeout": "adapter_timeout",
            "environment_unreachable": "adapter_unavailable",
        }
        return mapping.get(failure_class, failure_class)

    def _build_v06_tool_registry(self) -> ToolRegistry:
        def preview_window_id(name: str) -> str:
            slug = name.strip().lower() or "current"
            return f"preview:{slug}"

        def apps_resolve(payload, context) -> ToolResult:
            name = str(payload.get("name") or "")
            matches = self.apps.resolve(name)
            selected = matches[0].desktop_id if matches else None
            return ToolResult(
                status="succeeded",
                output={"resolved_desktop_id": selected, "matches": [asdict(item) for item in matches]},
                evidence={"requested_name": name, "match_count": len(matches)},
                state_updates={"resolved_desktop_id": selected},
            )

        def app_open(payload, context) -> ToolResult:
            name = str(payload.get("name") or "")
            selected = str(context.state.get("resolved_desktop_id") or "")
            matches = self.apps.resolve(name) if not selected else []
            if not selected and matches:
                selected = matches[0].desktop_id
            if not selected:
                return ToolResult(
                    status="failed",
                    message="no installed app matches the requested target",
                    evidence={"requested_name": name},
                    failure_class="semantic_mismatch",
                )
            if context.environment.dry_run:
                return ToolResult(
                    status="succeeded",
                    output={"selected_target": selected, "adapter": "apps.registry", "adapter_status": "dry_run"},
                    evidence={"requested_name": name, "desktop_id": selected, "dry_run": True},
                    state_updates={"selected_target": selected},
                )
            app = next((item for item in self.apps.list_apps() if item.desktop_id == selected), None)
            if app is None:
                return ToolResult(
                    status="failed",
                    message="resolved desktop application is unavailable",
                    evidence={"desktop_id": selected},
                    failure_class="environment_unreachable",
                )
            adapter_result = self.apps.open_app(app)
            if adapter_result.get("status") == "opened":
                return ToolResult(
                    status="succeeded",
                    output={"selected_target": selected, "adapter": "apps.registry", "adapter_status": "succeeded", **adapter_result},
                    evidence={"requested_name": name, "desktop_id": selected},
                    state_updates={"selected_target": selected},
                )
            return ToolResult(
                status="failed",
                message=str(adapter_result.get("error") or "app open failed"),
                output={"adapter": "apps.registry", "adapter_status": str(adapter_result.get("status") or "failed"), **adapter_result},
                evidence={"requested_name": name, "desktop_id": selected},
                failure_class="environment_unreachable",
            )

        def browser_action(payload, context) -> ToolResult:
            tool_id = str(payload.get("tool_id") or "")
            uri, query, site = self._browser_runtime_target(tool_id, payload)
            if not uri:
                return ToolResult(status="failed", message="browser route did not produce a URI", failure_class="semantic_mismatch")
            if context.environment.dry_run:
                with browser_attempt_scope(run_id=context.goal_id, attempt_id=context.attempt_id, route_id=context.strategy_id):
                    record_browser_navigation(uri=uri, query=query, site=site, adapter="browser.semantic", status="opened")
                return ToolResult(
                    status="succeeded",
                    output={"selected_target": uri, "uri": uri, "adapter": "browser.semantic", "adapter_status": "dry_run"},
                    evidence={"uri": uri, "query": query, "site": site, "dry_run": True},
                    state_updates={"selected_target": uri},
                )
            with browser_attempt_scope(run_id=context.goal_id, attempt_id=context.attempt_id, route_id=context.strategy_id):
                adapter_result = self.portal.open_uri(uri)
                if query or site:
                    record_browser_navigation(
                        uri=uri,
                        query=query,
                        site=site,
                        adapter=str(adapter_result.get("adapter") or "browser.semantic"),
                        status="opened" if adapter_result.get("status") == "opened" else str(adapter_result.get("status") or "failed"),
                    )
            status = str(adapter_result.get("status") or "failed")
            if status == "opened":
                return ToolResult(
                    status="succeeded",
                    output={"selected_target": uri, "uri": uri, "adapter": "browser.semantic", "adapter_status": "succeeded", **adapter_result},
                    evidence={"uri": uri, "query": query, "site": site},
                    state_updates={"selected_target": uri},
                )
            failure_class = "tool_timeout" if status == "timeout" else "environment_unreachable"
            return ToolResult(
                status="failed",
                message=str(adapter_result.get("error") or "browser action failed"),
                output={"uri": uri, "adapter": "browser.semantic", "adapter_status": status, **adapter_result},
                evidence={"uri": uri, "query": query, "site": site},
                failure_class=failure_class,
            )

        def browser_observe(payload, context) -> ToolResult:
            snapshot = browser_context_snapshot(context.attempt_id)
            if not snapshot.get("active_url"):
                url_observation = self.verifier_harness.observation_for("browser_url_opened")
                if url_observation.get("opened_url"):
                    snapshot["active_url"] = url_observation.get("opened_url")
                elif context.environment.dry_run and snapshot.get("requested_url"):
                    snapshot["active_url"] = snapshot.get("requested_url")
            if not snapshot.get("query"):
                query_observation = self.verifier_harness.observation_for("browser_search_route_completed")
                if query_observation.get("query"):
                    snapshot["query"] = query_observation.get("query")
                elif context.environment.dry_run and snapshot.get("requested_query"):
                    snapshot["query"] = snapshot.get("requested_query")
            return ToolResult(
                status="succeeded",
                output={"observed_url": snapshot.get("active_url"), "observed_query": snapshot.get("query")},
                evidence=snapshot,
                state_updates={"observed_url": snapshot.get("active_url"), "observed_query": snapshot.get("query")},
            )

        def browser_verify_query(payload, context) -> ToolResult:
            expected_query = str(payload.get("query") or "")
            observed_query = str(context.state.get("observed_query") or "") or str(self.verifier_harness.observation_for("browser_search_route_completed").get("query") or "")
            accepted = bool(expected_query) and observed_query == expected_query
            return ToolResult(
                status="succeeded" if accepted else "failed",
                message="browser verifier observed the requested search query" if accepted else "browser verifier did not observe the expected search query",
                evidence={"expected_query": expected_query, "observed_query": observed_query},
                accepted=accepted,
                failure_class="acceptance_failed" if not accepted else "none",
            )

        def browser_verify_url(payload, context) -> ToolResult:
            expected_url = str(payload.get("uri") or "")
            observed_url = str(context.state.get("observed_url") or "") or str(self.verifier_harness.observation_for("browser_url_opened").get("opened_url") or "")
            accepted = bool(expected_url) and observed_url == expected_url
            return ToolResult(
                status="succeeded" if accepted else "failed",
                message="browser verifier observed the requested URL" if accepted else "browser verifier did not observe the expected URL",
                evidence={"expected_url": expected_url, "observed_url": observed_url},
                accepted=accepted,
                failure_class="acceptance_failed" if not accepted else "none",
            )

        def browser_resolve_named_target(payload, context) -> ToolResult:
            name = str(payload.get("name") or payload.get("target_name") or "").strip()
            resolved = str(getattr(context.environment, "browser_site_catalog", {}).get(name.lower(), "") or "")
            if not resolved:
                return ToolResult(
                    status="failed",
                    message="no local direct-open resolution matched the named website target",
                    evidence={"requested_name": name},
                    failure_class="semantic_mismatch",
                )
            return ToolResult(
                status="succeeded",
                output={"resolved_url": resolved},
                evidence={"requested_name": name, "resolved_url": resolved, "resolution_source": "local_catalog"},
                state_updates={"resolved_url": resolved},
            )

        def browser_open_resolved_target(payload, context) -> ToolResult:
            resolved_url = str(context.state.get("resolved_url") or "")
            if not resolved_url:
                return ToolResult(status="failed", message="named browser target has not been resolved", failure_class="semantic_mismatch")
            return browser_action({"tool_id": "browser.open_url", "uri": resolved_url}, context)

        def browser_observe_search_results(payload, context) -> ToolResult:
            query = str(payload.get("query") or context.state.get("observed_query") or "").strip()
            catalog = getattr(context.environment, "browser_search_catalog", {})
            result_payload = catalog.get(query.lower(), {}) if isinstance(catalog, dict) else {}
            official_url = str(result_payload.get("official_url") or "")
            return ToolResult(
                status="succeeded",
                output={"official_result_url": official_url, "result_count": 1 if official_url else 0},
                evidence={"query": query, "official_result_url": official_url, "result_source": "local_catalog"},
                state_updates={"official_result_url": official_url},
            )

        def browser_follow_search_result(payload, context) -> ToolResult:
            official_url = str(context.state.get("official_result_url") or "")
            if not official_url:
                return ToolResult(
                    status="failed",
                    message="browser search results did not provide a follow-up destination",
                    evidence={"query": payload.get("query")},
                    failure_class="semantic_mismatch",
                )
            return browser_action({"tool_id": "browser.open_url", "uri": official_url}, context)

        def browser_verify_goal_page_identity(payload, context) -> ToolResult:
            name = str(payload.get("name") or "").strip()
            observed_url = str(context.state.get("observed_url") or "") or str(self.verifier_harness.observation_for("browser_url_opened").get("opened_url") or "")
            resolved_url = str(context.state.get("resolved_url") or context.state.get("official_result_url") or getattr(context.environment, "browser_site_catalog", {}).get(name.lower(), "") or "")
            accepted = bool(observed_url) and bool(resolved_url) and observed_url == resolved_url
            return ToolResult(
                status="succeeded" if accepted else "failed",
                message="browser verifier observed the resolved goal page identity" if accepted else "browser verifier did not observe the resolved goal page identity",
                evidence={"target_name": name, "observed_url": observed_url, "resolved_url": resolved_url},
                accepted=accepted,
                failure_class="acceptance_failed" if not accepted else "none",
            )

        def fixture_for_app(context, app_name: str) -> AppSearchFixture | None:
            catalog = getattr(context.environment, "app_fixture_catalog", {})
            fixture = catalog.get(app_name.lower()) if isinstance(catalog, dict) else None
            return fixture if isinstance(fixture, AppSearchFixture) else None

        def app_fixture_locate_search_control(payload, context) -> ToolResult:
            app_name = str(payload.get("app") or "").strip()
            fixture = fixture_for_app(context, app_name)
            if fixture is None:
                return ToolResult(status="failed", message="no app fixture matched the requested application", evidence={"app": app_name}, failure_class="environment_unreachable")
            if not fixture.has_control("search_box"):
                return ToolResult(status="failed", message="structured search control was not visible in the app fixture", evidence={"app": app_name, "fixture_id": fixture.fixture_id}, failure_class="semantic_mismatch")
            return ToolResult(
                status="succeeded",
                output={"search_control_id": "search_box"},
                evidence={"app": app_name, "fixture_id": fixture.fixture_id, "control_id": "search_box"},
                state_updates={"search_control_id": "search_box"},
            )

        def app_fixture_activate_search_shortcut(payload, context) -> ToolResult:
            app_name = str(payload.get("app") or "").strip()
            fixture = fixture_for_app(context, app_name)
            if fixture is None:
                return ToolResult(status="failed", message="no app fixture matched the requested application", evidence={"app": app_name}, failure_class="environment_unreachable")
            if not fixture.shortcut_search_enabled:
                return ToolResult(status="failed", message="shortcut search mode is unavailable in the app fixture", evidence={"app": app_name, "fixture_id": fixture.fixture_id}, failure_class="semantic_mismatch")
            return ToolResult(
                status="succeeded",
                output={"search_mode": "shortcut"},
                evidence={"app": app_name, "fixture_id": fixture.fixture_id, "shortcut": "Ctrl+K"},
                state_updates={"search_mode": "shortcut"},
            )

        def app_fixture_enter_search_query(payload, context) -> ToolResult:
            app_name = str(payload.get("app") or "").strip()
            query = str(payload.get("query") or "").strip()
            fixture = fixture_for_app(context, app_name)
            if fixture is None:
                return ToolResult(status="failed", message="no app fixture matched the requested application", evidence={"app": app_name}, failure_class="environment_unreachable")
            if not query:
                return ToolResult(status="failed", message="missing app search query", failure_class="semantic_mismatch")
            if not context.state.get("search_control_id") and context.state.get("search_mode") != "shortcut":
                return ToolResult(status="failed", message="search query entry requires a visible control or active shortcut mode", evidence={"app": app_name}, failure_class="semantic_mismatch")
            results = fixture.results_for(query)
            return ToolResult(
                status="succeeded",
                output={"search_query": query, "result_count": len(results)},
                evidence={"app": app_name, "fixture_id": fixture.fixture_id, "query": query, "result_count": len(results)},
                state_updates={"search_query": query, "observed_results": results},
            )

        def app_fixture_observe_results(payload, context) -> ToolResult:
            app_name = str(payload.get("app") or "").strip()
            results = tuple(str(item) for item in context.state.get("observed_results", ()))
            return ToolResult(
                status="succeeded",
                output={"observed_results": results},
                evidence={"app": app_name, "observed_results": results},
                state_updates={"observed_results": results},
            )

        def app_fixture_verify_target_presence(payload, context) -> ToolResult:
            query = str(payload.get("query") or context.state.get("search_query") or "").strip()
            observed_results = tuple(str(item) for item in context.state.get("observed_results", ()))
            accepted = bool(query) and any(item.lower() == query.lower() for item in observed_results)
            return ToolResult(
                status="succeeded" if accepted else "failed",
                message="app verifier observed the requested target in search results" if accepted else "app verifier did not observe the requested target in search results",
                evidence={"query": query, "observed_results": observed_results},
                accepted=accepted,
                failure_class="acceptance_failed" if not accepted else "none",
            )

        def window_list(payload, context) -> ToolResult:
            windows = [asdict(window) for window in self.windows.list_windows()]
            return ToolResult(
                status="succeeded",
                output={"windows": windows, "adapter": "windows.registry", "adapter_status": "succeeded"},
                evidence={"window_count": len(windows)},
                state_updates={"selected_target": windows[0]["window_id"] if windows else None},
            )

        def window_resolve(payload, context) -> ToolResult:
            name = str(payload.get("name") or "current")
            windows = self.windows.resolve(name)
            selected = windows[0].window_id if windows else None
            if context.environment.dry_run and not selected:
                selected = preview_window_id(name)
            return ToolResult(
                status="succeeded",
                output={"resolved_window_id": selected, "matches": [asdict(item) for item in windows]},
                evidence={"requested_name": name, "match_count": len(windows), "preview_only": context.environment.dry_run and not windows},
                state_updates={"resolved_window_id": selected},
            )

        def window_action(payload, context) -> ToolResult:
            tool_id = str(payload.get("tool_id") or "")
            name = str(payload.get("name") or "current")
            resolved = str(context.state.get("resolved_window_id") or "")
            windows = self.windows.resolve(name) if not resolved else []
            window = None
            if resolved:
                window = next((item for item in self.windows.list_windows() if item.window_id == resolved), None)
            elif windows:
                window = windows[0]
            if context.environment.dry_run:
                synthetic_target = resolved or (window.window_id if window is not None else preview_window_id(name))
                return ToolResult(
                    status="succeeded",
                    output={"selected_target": synthetic_target, "adapter": "windows.registry", "adapter_status": "dry_run"},
                    evidence={"requested_name": name, "window_id": synthetic_target, "dry_run": True, "preview_only": window is None},
                    state_updates={"selected_target": synthetic_target},
                )
            if window is None:
                return ToolResult(
                    status="failed",
                    message=f"no window matched {name!r}",
                    evidence={"requested_name": name},
                    failure_class="semantic_mismatch",
                )
            action = {
                "window.focus": self.windows.focus,
                "window.minimize": self.windows.minimize,
                "window.maximize": self.windows.maximize,
                "window.close": self.windows.close,
            }[tool_id]
            action_result = action(window)
            status = str(action_result.get("status") or "failed")
            if status in {"focused", "minimized", "maximized", "closed"}:
                return ToolResult(
                    status="succeeded",
                    output={"selected_target": window.window_id, "adapter": "windows.registry", "adapter_status": "succeeded", **action_result},
                    evidence={"requested_name": name, "window_id": window.window_id},
                    state_updates={"selected_target": window.window_id},
                )
            return ToolResult(
                status="failed",
                message=str(action_result.get("error") or f"window action {tool_id} failed"),
                output={"adapter": "windows.registry", "adapter_status": status, **action_result},
                evidence={"requested_name": name, "window_id": window.window_id},
                failure_class="environment_unreachable",
            )

        def notification_send(payload, context) -> ToolResult:
            title = str(payload.get("title") or "VibeOS").strip() or "VibeOS"
            body = str(payload.get("body") or payload.get("message") or "").strip()
            if context.environment.dry_run:
                return ToolResult(
                    status="succeeded",
                    output={"selected_target": title, "adapter": "notifications.send", "adapter_status": "dry_run"},
                    evidence={"title": title, "body": body, "dry_run": True},
                    state_updates={"selected_target": title},
                )
            sent = self.notifications.send(title, body)
            status = str(sent.get("status") or "failed")
            if status == "sent":
                return ToolResult(
                    status="succeeded",
                    output={
                        "selected_target": title,
                        "adapter": "notifications.send",
                        "adapter_status": "succeeded",
                        "notification_adapter": sent.get("adapter"),
                        **{key: value for key, value in sent.items() if key != "adapter"},
                    },
                    evidence={"title": title, "body": body, "notification_adapter": sent.get("adapter")},
                    state_updates={"selected_target": title},
                )
            return ToolResult(
                status="failed",
                message=str(sent.get("error") or "notification send failed"),
                output={
                    "adapter": "notifications.send",
                    "adapter_status": status,
                    "notification_adapter": sent.get("adapter"),
                    **{key: value for key, value in sent.items() if key != "adapter"},
                },
                evidence={"title": title, "body": body, "notification_adapter": sent.get("adapter")},
                failure_class="environment_unreachable" if status == "unavailable" else "tool_timeout" if status == "timeout" else "acceptance_failed",
            )

        def system_status(payload, context) -> ToolResult:
            status_payload = {"portal": self.portal.status(), **self.capabilities()}
            return ToolResult(
                status="succeeded",
                output={"adapter": "system.status", "adapter_status": "succeeded", **status_payload},
                evidence={"capability_count": len(status_payload.get("capabilities", []))},
            )

        def clipboard_write(payload, context) -> ToolResult:
            text = str(payload.get("text") or payload.get("content") or "").strip()
            if not text:
                return ToolResult(status="failed", message="missing clipboard text", failure_class="semantic_mismatch")
            if context.environment.dry_run:
                return ToolResult(
                    status="succeeded",
                    output={"selected_target": "clipboard", "adapter": "clipboard.write", "adapter_status": "dry_run"},
                    evidence={"text_length": len(text), "dry_run": True},
                    state_updates={"selected_target": "clipboard"},
                )
            written = self.clipboard.write(text)
            status = str(written.get("status") or "failed")
            if status == "written":
                return ToolResult(
                    status="succeeded",
                    output={
                        "selected_target": "clipboard",
                        "adapter": str(written.get("adapter") or "clipboard.helper"),
                        "capability_adapter": "clipboard.write",
                        "adapter_status": "succeeded",
                        **written,
                    },
                    evidence={"text_length": len(text)},
                    state_updates={"selected_target": "clipboard"},
                )
            return ToolResult(
                status="failed",
                message=str(written.get("error") or "clipboard write failed"),
                output={
                    "adapter": str(written.get("adapter") or "clipboard.helper"),
                    "capability_adapter": "clipboard.write",
                    "adapter_status": status,
                    **written,
                },
                evidence={"text_length": len(text)},
                failure_class="tool_timeout" if status == "timeout" else "environment_unreachable" if status == "unavailable" else "acceptance_failed",
            )

        return ToolRegistry(
            (
                ToolSpec("apps.resolve_installed", "resolver", "desktop-linux", apps_resolve),
                ToolSpec("app.open", "action", "desktop-linux", app_open),
                ToolSpec("window.list", "action", "desktop-linux", window_list),
                ToolSpec("window.resolve", "resolver", "desktop-linux", window_resolve),
                ToolSpec("window.focus", "action", "desktop-linux", lambda payload, context: window_action({**payload, "tool_id": "window.focus"}, context)),
                ToolSpec("window.minimize", "action", "desktop-linux", lambda payload, context: window_action({**payload, "tool_id": "window.minimize"}, context)),
                ToolSpec("window.maximize", "action", "desktop-linux", lambda payload, context: window_action({**payload, "tool_id": "window.maximize"}, context)),
                ToolSpec("window.close", "action", "desktop-linux", lambda payload, context: window_action({**payload, "tool_id": "window.close"}, context)),
                ToolSpec("notification.send", "action", "desktop-linux", notification_send),
                ToolSpec("clipboard.write", "action", "desktop-linux", clipboard_write),
                ToolSpec("system.status", "action", "desktop-linux", system_status),
                ToolSpec("browser.open_url", "action", "browser", lambda payload, context: browser_action({**payload, "tool_id": "browser.open_url"}, context)),
                ToolSpec("browser.resolve_named_target", "resolver", "browser", browser_resolve_named_target),
                ToolSpec("browser.open_resolved_target", "action", "browser", browser_open_resolved_target),
                ToolSpec("browser.search_web", "action", "browser", lambda payload, context: browser_action({**payload, "tool_id": "browser.search_web"}, context)),
                ToolSpec("browser.open_site_search", "action", "browser", lambda payload, context: browser_action({**payload, "tool_id": "browser.open_site_search"}, context)),
                ToolSpec("browser.observe_context", "observer", "browser", browser_observe),
                ToolSpec("browser.observe_search_results", "observer", "browser", browser_observe_search_results),
                ToolSpec("browser.follow_search_result", "action", "browser", browser_follow_search_result),
                ToolSpec("browser.verify_query", "verifier", "browser", browser_verify_query),
                ToolSpec("browser.verify_url_opened", "verifier", "browser", browser_verify_url),
                ToolSpec("browser.verify_goal_page_identity", "verifier", "browser", browser_verify_goal_page_identity),
                ToolSpec("app.fixture.locate_search_control", "resolver", "desktop-linux", app_fixture_locate_search_control),
                ToolSpec("app.fixture.activate_search_shortcut", "action", "desktop-linux", app_fixture_activate_search_shortcut),
                ToolSpec("app.fixture.enter_search_query", "action", "desktop-linux", app_fixture_enter_search_query),
                ToolSpec("app.fixture.observe_results", "observer", "desktop-linux", app_fixture_observe_results),
                ToolSpec("app.fixture.verify_target_presence", "verifier", "desktop-linux", app_fixture_verify_target_presence),
            )
        )

    def _browser_runtime_target(self, tool_id: str, payload: dict[str, object]) -> tuple[str, str | None, str | None]:
        if tool_id == "browser.open_url":
            return str(payload.get("uri") or payload.get("url") or ""), None, None
        if tool_id == "browser.search_web":
            query = str(payload.get("query") or "")
            return browser_semantic_uri(Intent(action="browser.search_web", target={"query": query})), query or None, None
        if tool_id == "browser.open_site_search":
            query = str(payload.get("query") or "")
            site = str(payload.get("site") or "")
            return browser_semantic_uri(Intent(action="browser.open_site_search", target={"query": query, "site": site})), query or None, site or None
        return "", None, None

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
            "assistant_intent": assistant_intent_to_payload(planning.goal_synthesis.goal_spec.assistant_intent) if planning.goal_synthesis and planning.goal_synthesis.goal_spec else None,
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

    def _approve_plan_review_v06(
        self,
        review_request,
        *,
        review_id: str,
        dry_run: bool,
        transport: str | None,
    ) -> CommandResult:
        plan_payload = review_request.plan_payload or {}
        plan = task_plan_from_payload(plan_payload)
        runtime_payload = plan_payload.get("v0_6_runtime") if isinstance(plan_payload.get("v0_6_runtime"), dict) else {}
        goal_id = str(runtime_payload.get("goal_id") or f"goal_review_{plan.plan_id}")
        selected_strategy_id = str(runtime_payload.get("selected_strategy_id") or f"strategy_{plan.selected_route_id}")
        strategy = self._task_plan_to_v06_strategy(plan, goal_id, 0)
        if strategy is not None and strategy.strategy_id != selected_strategy_id:
            strategy = StrategyCandidate(
                strategy_id=selected_strategy_id,
                goal_id=strategy.goal_id,
                title=strategy.title,
                route_id=strategy.route_id,
                capability_surface=strategy.capability_surface,
                task_plan=strategy.task_plan,
                steps=strategy.steps,
                priority=strategy.priority,
                requires_desktop_integration=strategy.requires_desktop_integration,
                requires_network=strategy.requires_network,
                metadata=dict(strategy.metadata),
            )
        if strategy is None:
            return self._approve_plan_review_legacy(review_request, review_id=review_id, dry_run=dry_run, transport=transport)
        stored_environment = runtime_payload.get("environment_profile") if isinstance(runtime_payload.get("environment_profile"), dict) else {}
        environment = EnvironmentProfile(
            platform=str(stored_environment.get("platform") or ("linux" if os.name == "posix" else "windows")),
            transport_mode=transport or str(stored_environment.get("transport_mode") or "local"),
            daemon_available=bool(stored_environment.get("daemon_available", False)),
            desktop_integration_available=bool(stored_environment.get("desktop_integration_available", True)),
            connectivity_limitations=str(stored_environment.get("connectivity_limitations") or "offline"),
            deployment_profile=str(stored_environment.get("deployment_profile") or "permission-review"),
            region=str(stored_environment.get("region") or "local"),
            search_policy=str(stored_environment.get("search_policy") or "balanced"),
            dry_run=dry_run,
        )
        if goal_id not in self.agent_session.goals:
            self.agent_runtime.start_goal(self.agent_session.session_id, self._goal_spec_from_plan(plan, goal_id))
        result = self.agent_runtime.continue_goal(
            session_id=self.agent_session.session_id,
            goal_id=goal_id,
            utterance=plan.utterance,
            strategies=(strategy,),
            environment=environment,
        )
        execution_payload, selected_target = self._v06_execution_payload(result, strategy)
        payload = {
            "environment_profile": asdict(environment),
            "goal_runtime": asdict(result.goal_runtime),
            "goal_turn": asdict(result.turn),
            "strategy_candidates": [self._strategy_payload(strategy)],
            "selected_strategy_id": result.selected_strategy_id,
            "run_ledger": asdict(result.ledger),
            "plan": asdict(plan),
            "plan_id": plan.plan_id,
            "status": execution_payload["status"],
            "step_results": execution_payload["step_results"],
            "verification_results": execution_payload["verification_results"],
            "verification_status": execution_payload["verification_status"],
            "error": execution_payload["error"],
        }
        intent = self._intent_from_task_step(plan.steps[0]) if plan.steps else review_request.intent
        overall_status = self._v06_overall_status(CommandRequest("", dry_run=dry_run), result)
        return self._with_transport(
            CommandResult(
                status=self._v06_command_status(CommandRequest("", dry_run=dry_run), overall_status),
                intent=intent,
                result=payload,
                selected_target=selected_target,
                review=review_request.review,
                review_id=review_id,
                message="stored task plan resolved without execution" if dry_run else (result.terminal_outcome.reason or "stored task plan executed"),
                execution_status="dry_run" if dry_run else execution_payload["execution_status"],
                acceptance_status=str(execution_payload.get("acceptance_status", "skipped")),
                overall_status=overall_status,
            ),
            transport,
        )

    def _approve_plan_review_legacy(
        self,
        review_request,
        *,
        review_id: str,
        dry_run: bool,
        transport: str | None,
    ) -> CommandResult:
        plan = task_plan_from_payload(review_request.plan_payload or {})
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
        return self._with_transport(
            CommandResult(
                status=execution_state_to_command_status(execution.status, dry_run=dry_run),
                intent=plan_intent,
                result=asdict(execution),
                review=review_request.review,
                review_id=review_id,
                message=execution.error or ("stored task plan resolved without execution" if dry_run else "stored task plan executed"),
                execution_status=execution.execution_status,
                acceptance_status=execution.acceptance_status,
                overall_status=execution.overall_status,
            ),
            transport,
        )

    def _goal_spec_from_plan(self, plan: TaskPlan, goal_id: str):
        return GoalSpec(
            goal_id=goal_id,
            goal_text=plan.utterance,
            goal_type=plan.selected_route_id or "approved_plan",
            candidate_domain_ids=tuple(route.domain_id for route in plan.routes if route.domain_id),
            required_capability_ids=tuple(step.capability_id for step in plan.steps),
            synthesis_provenance=GoalSynthesisProvenance(provider_name="permission_review", provider_version="v0.6"),
        )

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
                result = self._approve_plan_review_v06(review_request, review_id=review_id, dry_run=True, transport=transport)
                audit_id = self.audit.record(
                    request=CommandRequest("", dry_run=True, approve=True, review_id=review_id, transport=transport),
                    intent=result.intent,
                    status=result.status,
                    result=result.result,
                    selected_target=result.selected_target,
                    message=result.message,
                    review=result.review,
                    review_id=review_id,
                    plan_id=review_request.plan_id,
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
            result = self._approve_plan_review_v06(review_request, review_id=review_id, dry_run=dry_run, transport=transport)
            if result.status == "executed":
                self.reviews.consume(review_id)
            audit_id = self.audit.record(
                request=CommandRequest("", dry_run=dry_run, approve=True, review_id=review_id, transport=transport),
                intent=result.intent,
                status=result.status,
                result=result.result,
                selected_target=result.selected_target,
                message=result.message,
                review=result.review,
                review_id=review_id,
                plan_id=review_request.plan_id,
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
