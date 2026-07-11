from __future__ import annotations

import os
import json
from contextlib import nullcontext
from dataclasses import replace
from dataclasses import asdict
from hashlib import sha256
from time import perf_counter

from .acceptance import AcceptanceEngine
from .agent_runtime import AgentRuntime, EnvironmentProfile, TerminalOutcome
from .app_fixtures import AppSearchFixture
from .apps import AppRegistry
from .assistant_semantics import AssistantIntent, InteractionSurface, assistant_intent_to_payload
from .audit import AuditLog
from .browser_state import browser_attempt_scope, record_browser_navigation
from .candidate_selection import CandidateSelectionProvider, candidate_selection_decision_from_payload, candidate_set_from_payload
from .command_service import CommandPorts, CommandService
from .capabilities import capability_payload, executable_actions, permission_summary
from .clarification import ClarificationProvider
from .clipboard import ClipboardAdapter
from .domain_models import ObservationRequest
from .domain_registry import default_domain_registry
from .execution_graph import execute_plan_graph, overall_status as execution_graph_overall_status
from .failure_classifier import FailureClassifier
from .goal_loop import GoalLoop, loop_state_from_payload, normalize_state_for_plan, sync_loop_state_with_planning
from .goal_loop_adapters import (
    BrokerAcceptancePort,
    BrokerExecutionPort,
    BrokerObservationPort,
    BrokerPlanningPort,
    BrokerRecoveryPort,
    BrokerReviewPort,
)
from .goal_ports import GoalLoopPorts
from .goal_models import GoalSpec, GoalSynthesisProvenance
from .intent import IntentBroker, OpenAICompatibleIntentBroker
from .loop_policy import contextualize_step_review, default_loop_policy
from .loop_models import LoopObservation, LoopPolicy, LoopState
from .notifications import NotificationAdapter
from .models import CommandRequest, CommandResult, Intent, PermissionReview, utc_now_iso
from .observation import resolve_post_execution_observation
from .observation_service import ObservationService, observation_progressed
from .planner import PlanningArtifacts, browser_semantic_uri, plan_turn
from .permissions import PermissionPolicy
from .portal import PortalAdapter
from .replanner import EvidenceDrivenReplanner, Replanner
from .reviews import ReviewStore, review_to_payload
from .projections import project_legacy_runtime_payload
from .run_context import RunContext
from .run_ledger import AttemptLedgerEntry
from .strategy import RecoveryPolicy, StrategyCandidate, StrategyConstraint, StrategySelectionProvider, StrategyStep
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
from .task_trace import TaskTraceStore, bind_trace_session, current_trace_session, record_model_io, record_trace_event
from .task_validation import validate_plan
from .tool_protocol import ToolRegistry
from .tools.apps import app_tool_specs
from .tools.browser import browser_tool_specs
from .tools.clipboard import clipboard_tool_specs
from .tools.fixtures import fixture_tool_specs
from .tools.notifications import notification_tool_specs
from .tools.system import system_tool_specs
from .tools.windows import window_tool_specs
from .understanding import (
    OpenAICompatibleUnderstandingTransitionProvider,
    UnderstandingAnalysisDecision,
    UnderstandingAnalysisProvider,
    UnderstandingArtifact,
    UnderstandingTransitionProvider,
    create_primary_understanding,
    default_understanding_host_hint,
    reconcile_reinterpreted_understanding,
    reconcile_understanding_transition,
    root_understanding_id,
    validated_understanding_from_payload,
)
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
        trace_store: TaskTraceStore | None = None,
        clarification_provider: ClarificationProvider | None = None,
        understanding_analysis_provider: UnderstandingAnalysisProvider | None = None,
        goal_synthesis_provider: GoalSynthesisProvider | None = None,
        route_selection_provider: CandidateSelectionProvider | None = None,
        strategy_selection_provider: StrategySelectionProvider | None = None,
        understanding_transition_provider: UnderstandingTransitionProvider | None = None,
        semantic_acceptance_provider: SemanticAcceptanceProvider | None = None,
        verifier_registry: VerifierRegistry | None = None,
        verifier_harness: VerifierHarness | None = None,
        failure_classifier: FailureClassifier | None = None,
        replanner: Replanner | None = None,
        loop_policy: LoopPolicy | None = None,
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
        self.trace_store = trace_store or TaskTraceStore()
        self.clarification_provider = clarification_provider
        self.understanding_analysis_provider = understanding_analysis_provider
        self.goal_synthesis_provider = goal_synthesis_provider
        self.route_selection_provider = route_selection_provider
        self.strategy_selection_provider = strategy_selection_provider
        self.understanding_transition_provider = understanding_transition_provider or (
            OpenAICompatibleUnderstandingTransitionProvider()
        )
        self.verifier_registry = verifier_registry or default_verifier_registry()
        self.verifier_harness = verifier_harness or VerifierHarness()
        self.acceptance_engine = AcceptanceEngine(provider=semantic_acceptance_provider)
        self.failure_classifier = failure_classifier or FailureClassifier()
        self.replanner = replanner or EvidenceDrivenReplanner()
        self.loop_policy = loop_policy or default_loop_policy()
        self.browser_site_catalog = browser_site_catalog or {}
        self.browser_search_catalog = browser_search_catalog or {}
        self.app_fixture_catalog = app_fixture_catalog or {}
        recovery_policy = RecoveryPolicy(provider=strategy_selection_provider) if strategy_selection_provider is not None else None
        self.agent_runtime = AgentRuntime(self._build_v06_tool_registry(), recovery_policy=recovery_policy)
        self.agent_session = self.agent_runtime.create_session("broker_session")
        self.command_service = CommandService(
            trace_store=self.trace_store,
            ports=CommandPorts(
                make_run_id=self._make_run_id,
                plan=self._handle_task_plan_request,
                approve_review=lambda review_id, dry_run, transport: self.approve_review(review_id, dry_run=dry_run, transport=transport),
                provide_review_input=lambda review_id, supplemental_input, dry_run, transport: self.provide_review_input(review_id, supplemental_input, dry_run=dry_run, transport=transport),
                record_result=self._record_command_result,
                result_metadata=self._command_result_metadata,
            ),
        )

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
            result = TaskPlanReviewResult(
                plan_id=plan.plan_id,
                status="rejected",
                max_risk_level="L3",
                message="task plan failed validation before permission review",
            )
            record_trace_event(
                phase="review",
                event_type="review_decided",
                status=result.status,
                actor="broker",
                plan_id=plan.plan_id,
                data=asdict(result),
            )
            return result

        step_reviews: list[StepReviewRecord] = []
        review_required = False
        allowed = True
        max_risk = "L0"
        rejection_reason = ""

        for step in plan.steps:
            review, step_review = self._step_safety_review_record(plan.plan_id, step, phase="review")
            step_reviews.append(step_review)
            record_trace_event(
                phase="review",
                event_type="step_safety_review_recorded",
                status="allowed" if review.allowed else "rejected",
                actor="broker",
                plan_id=plan.plan_id,
                step_id=step.id,
                data={
                    "artifact_type": "step_safety_review",
                    "artifact_id": step_review.step_safety_review_id,
                    "step_id": step.id,
                    "action": step.action,
                    "risk_level": review.risk_level,
                    "review_required": review.review_required,
                    "allowed": review.allowed,
                    "reason": review.reason,
                },
            )
            max_risk = max_risk_level(max_risk, review.risk_level)
            review_required = review_required or review.review_required
            if not review.allowed and allowed:
                allowed = False
                rejection_reason = review.reason

        if not allowed:
            result = TaskPlanReviewResult(
                plan_id=plan.plan_id,
                status="rejected",
                max_risk_level=max_risk,
                step_reviews=tuple(step_reviews),
                message=rejection_reason or "task plan contains a rejected step",
            )
            record_trace_event(
                phase="review",
                event_type="review_decided",
                status=result.status,
                actor="broker",
                plan_id=plan.plan_id,
                data=asdict(result),
            )
            return result

        if review_required:
            review_request = self.reviews.create_plan_review(
                plan.utterance,
                stored_payload if stored_payload is not None else asdict(plan),
                TaskPlanReviewResult(plan_id=plan.plan_id, status="review_required", max_risk_level=max_risk, step_reviews=tuple(step_reviews)),
            )
            result = TaskPlanReviewResult(
                plan_id=plan.plan_id,
                status="review_required",
                max_risk_level=max_risk,
                review_id=review_request.review_id,
                step_reviews=tuple(step_reviews),
                message=f"explicit approval is required; run `vibe approve {review_request.review_id}` after reviewing the request",
            )
            record_trace_event(
                phase="review",
                event_type="review_decided",
                status=result.status,
                actor="broker",
                plan_id=plan.plan_id,
                review_id=result.review_id,
                data=asdict(result),
            )
            return result

        result = TaskPlanReviewResult(
            plan_id=plan.plan_id,
            status="allowed",
            max_risk_level=max_risk,
            step_reviews=tuple(step_reviews),
            message="task plan is allowed without additional review",
        )
        record_trace_event(
            phase="review",
            event_type="review_decided",
            status=result.status,
            actor="broker",
            plan_id=plan.plan_id,
            data=asdict(result),
        )
        return result

    def review_task_step(
        self,
        plan: TaskPlan,
        step: TaskStep,
        pre_observation: LoopObservation | None = None,
    ) -> tuple[PermissionReview, StepReviewRecord]:
        self._ensure_task_plan(plan)
        return self._step_safety_review_record(plan.plan_id, step, pre_observation=pre_observation, phase="review")

    def create_loop_review(
        self,
        *,
        utterance: str,
        planning,
        loop_state,
        step: TaskStep,
        reason: str,
    ):
        payload = self._planning_payload(planning)
        payload["loop_snapshot"] = asdict(loop_state)
        return self.reviews.create_loop_review(
            utterance,
            plan_payload=payload,
            snapshot_payload=asdict(loop_state),
            pending_reason=reason,
            step_id=step.id,
            review_kind="loop",
        )

    def create_user_input_review(self, *, utterance: str, planning, loop_state, reason: str):
        payload = self._planning_payload(planning)
        payload["loop_snapshot"] = asdict(loop_state)
        return self.reviews.create_loop_review(
            utterance,
            plan_payload=payload,
            snapshot_payload=asdict(loop_state),
            pending_reason=reason,
            step_id=None,
            review_kind="user_input",
        )

    def plan_turn_from_loop(
        self,
        planning,
        request: CommandRequest,
        excluded_route_ids: tuple[str, ...],
        excluded_capability_ids: tuple[str, ...],
        candidate_domain_ids_override: tuple[str, ...],
    ):
        return plan_turn(
            request.utterance,
            self.intent_broker,
            selection_provider=self.route_selection_provider,
            clarification_provider=self.clarification_provider,
            analysis_provider=self.understanding_analysis_provider,
            goal_synthesis_provider=self.goal_synthesis_provider,
            understanding=planning.understanding,
            debug=request.debug,
            candidate_domain_ids_override=candidate_domain_ids_override or None,
            excluded_route_ids=excluded_route_ids,
            excluded_capability_ids=excluded_capability_ids,
        )

    def _make_goal_loop(self) -> GoalLoop:
        return GoalLoop(
            ports=GoalLoopPorts(
                planning=BrokerPlanningPort(self),
                observation=BrokerObservationPort(self, ObservationService(self.verifier_registry, self.verifier_harness)),
                review=BrokerReviewPort(self),
                execution=BrokerExecutionPort(self),
                acceptance=BrokerAcceptancePort(self),
                recovery=BrokerRecoveryPort(self),
                policy=self.loop_policy,
            ),
        )

    def _step_progressed(
        self,
        plan: TaskPlan,
        step: TaskStep,
        step_result: StepExecutionResult,
        pre_observation: LoopObservation,
        post_observation: LoopObservation,
        request: CommandRequest,
    ) -> bool:
        """Apply the route's explicit evidence contract to one completed step.

        Adapter receipts are sufficient for routes that declare no verifier.
        Routes with verifier requirements, notably browser and media routes,
        remain observation-driven and cannot be marked complete merely because
        an adapter returned success.
        """

        if step_result.status != "succeeded":
            return False
        if request.dry_run:
            return True
        registry = default_domain_registry(self.verifier_registry.ids())
        route = registry.get_route(plan.selected_route_id)
        if route is None or not route.default_verifier_ids:
            return True
        return observation_progressed(pre_observation, post_observation)

    def execute_task_plan(
        self,
        plan: TaskPlan,
        dry_run: bool = False,
        transport: str | None = None,
        review_id: str | None = None,
        run_id: str | None = None,
        attempt_id: str | None = None,
        understanding_id: str | None = None,
        candidate_set_id: str | None = None,
        route_decision_id: str | None = None,
    ) -> PlanExecutionResult:
        self._ensure_task_plan(plan)
        validation = validate_plan(plan)
        if not validation.ok:
            return PlanExecutionResult(plan_id=plan.plan_id, status="rejected", error="task plan failed validation")
        def execute_step(step) -> StepExecutionResult:
            return self.execute_task_step(
                plan,
                step,
                dry_run=dry_run,
                transport=transport,
                review_id=review_id,
                attempt_id=scoped_attempt_id,
            )

        scoped_run_id = run_id or self._make_run_id(plan.utterance)
        scoped_attempt_id = attempt_id or self._make_attempt_id(scoped_run_id, 1, plan.selected_route_id or "standalone")
        with browser_attempt_scope(run_id=scoped_run_id, attempt_id=scoped_attempt_id, route_id=plan.selected_route_id):
            execution = execute_plan_graph(plan, execute_step)
            return self.assess_task_plan_execution(
                plan,
                execution.step_results,
                dry_run=dry_run,
                understanding_id=understanding_id,
                candidate_set_id=candidate_set_id,
                route_decision_id=route_decision_id,
                error=execution.error,
            )

    def execute_task_step(
        self,
        plan: TaskPlan,
        step: TaskStep,
        *,
        dry_run: bool = False,
        transport: str | None = None,
        review_id: str | None = None,
        attempt_id: str | None = None,
    ) -> StepExecutionResult:
        intent = self._intent_from_task_step(step)
        review, step_review = self._step_safety_review_record(plan.plan_id, step, phase="execute")
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
            step_safety_review_id=step_review.step_safety_review_id,
            layer=layer,
        )
        record_trace_event(
            phase="execution",
            event_type="step_safety_review_consumed",
            status="allowed" if review.allowed else "rejected",
            actor="broker",
            plan_id=plan.plan_id,
            step_id=step.id,
            data={
                "artifact_type": "step_safety_review",
                "artifact_id": step_review.step_safety_review_id,
                "review_required": review.review_required,
                "allowed": review.allowed,
            },
        )
        return StepExecutionResult(
            step_id=step.id,
            layer=layer,
            status=step_status,
            step_safety_review_id=step_review.step_safety_review_id,
            adapter=adapter_name_for_step(step, command),
            capability_id=step.capability_id,
            attempt=1,
            attempt_id=attempt_id,
            duration_ms=duration_ms,
            adapter_status=adapter_status,
            diagnostics={**diagnostics_for_step(step, command, request, duration_ms), "attempt_id": attempt_id},
            error_code=error_code,
            result=command.result if isinstance(command.result, dict) else {"value": command.result},
            error=command.message or None,
            audit_id=audit_id,
        )

    def assess_task_plan_execution(
        self,
        plan: TaskPlan,
        step_results: tuple[StepExecutionResult, ...],
        *,
        dry_run: bool = False,
        understanding_id: str | None = None,
        candidate_set_id: str | None = None,
        route_decision_id: str | None = None,
        error: str | None = None,
    ) -> PlanExecutionResult:
        verifier_ids = plan.routes[0].default_verifier_ids if plan.routes else ()
        execution = PlanExecutionResult(
            plan_id=plan.plan_id,
            status=execution_graph_overall_status(step_results),
            step_results=step_results,
            error=error or next((item.error for item in step_results if item.error), None),
        )
        registry = default_domain_registry(self.verifier_registry.ids())
        active_domain_ids = tuple(dict.fromkeys(route.domain_id for route in plan.routes if route.domain_id))
        route_definition = registry.get_route(plan.selected_route_id)
        post_request = None
        post_receipt = None
        verification_harness = self.verifier_harness
        if active_domain_ids or route_definition is not None:
            package_ids = route_definition.required_context_package_ids if route_definition is not None else ()
            if not package_ids and verifier_ids:
                package_ids = tuple(
                    dict.fromkeys(
                        package_id
                        for verifier_id in verifier_ids
                        for package_id in (
                            self.verifier_registry.get(verifier_id).observation_package_ids
                            if self.verifier_registry.get(verifier_id) is not None
                            else ()
                        )
                    )
                )
            post_request = ObservationRequest(
                active_domain_ids=active_domain_ids,
                requested_context_package_ids=(),
                postcondition_package_ids=package_ids,
            )
            post_receipt = resolve_post_execution_observation(post_request, registry, self.verifier_harness)
            verification_harness = self._verification_harness_for_postconditions(
                registry=registry,
                request=post_request,
                receipt=post_receipt,
            )
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
        return self.command_service.handle(request)

    def _command_result_metadata(self, result: CommandResult) -> dict[str, object]:
        return {
            "goal_id": self._result_goal_id(result),
            "plan_id": self._result_plan_id(result),
            "selected_strategy_id": self._result_selected_strategy_id(result),
        }

    def _record_command_result(self, request: CommandRequest, result: CommandResult, trace_run_id: str) -> CommandResult:
        audit_id = result.audit_id
        if audit_id is None:
            audit_id = self.audit.record(
                request=request,
                intent=result.intent,
                status=result.status,
                result=result.result,
                selected_target=result.selected_target,
                message=result.message,
                review=result.review,
                review_id=result.review_id,
                plan_id=self._result_plan_id(result),
                execution_status=result.execution_status,
                acceptance_status=result.acceptance_status,
                overall_status=result.overall_status,
                trace_run_id=trace_run_id,
                understanding_id=self._result_understanding_id(result),
                candidate_set_id=self._result_candidate_set_id(result),
                selected_route_decision_id=self._result_route_decision_id(result),
                selected_strategy_decision_id=self._result_selected_strategy_decision_id(result),
                semantic_acceptance_decision_id=self._result_semantic_acceptance_decision_id(result),
                loop_snapshot_id=self._result_loop_snapshot_id(result),
            )
        return CommandResult(
            status=result.status,
            intent=result.intent,
            result=result.result,
            selected_target=result.selected_target,
            trace_run_id=trace_run_id,
            audit_id=audit_id,
            review_id=result.review_id,
            transport=result.transport,
            message=result.message,
            review=result.review,
            execution_status=result.execution_status,
            acceptance_status=result.acceptance_status,
            overall_status=result.overall_status,
        )

    def _result_goal_id(self, result: CommandResult) -> str | None:
        if not isinstance(result.result, dict):
            return None
        run_payload = result.result.get("run")
        if isinstance(run_payload, dict) and run_payload.get("goal_id") is not None:
            return str(run_payload.get("goal_id"))
        goal_runtime = result.result.get("goal_runtime")
        if isinstance(goal_runtime, dict) and goal_runtime.get("goal_id") is not None:
            return str(goal_runtime.get("goal_id"))
        return None

    def _result_plan_id(self, result: CommandResult) -> str | None:
        if not isinstance(result.result, dict):
            return None
        if result.result.get("plan_id") is not None:
            return str(result.result.get("plan_id"))
        plan_payload = result.result.get("plan")
        if isinstance(plan_payload, dict) and plan_payload.get("plan_id") is not None:
            return str(plan_payload.get("plan_id"))
        return None

    def _result_selected_strategy_id(self, result: CommandResult) -> str | None:
        if not isinstance(result.result, dict):
            return None
        if result.result.get("selected_strategy_id") is not None:
            return str(result.result.get("selected_strategy_id"))
        return None

    def _result_selected_strategy_decision_id(self, result: CommandResult) -> str | None:
        if not isinstance(result.result, dict):
            return None
        run_ledger = result.result.get("run_ledger")
        if isinstance(run_ledger, dict):
            strategy_history = run_ledger.get("strategy_history")
            if isinstance(strategy_history, list) and strategy_history:
                last = strategy_history[-1]
                if isinstance(last, dict) and last.get("strategy_decision_id") is not None:
                    return str(last.get("strategy_decision_id"))
        return None

    def _result_understanding_id(self, result: CommandResult) -> str | None:
        if not isinstance(result.result, dict):
            return None
        understanding = result.result.get("understanding")
        if isinstance(understanding, dict):
            if understanding.get("primary_understanding_id") is not None:
                return str(understanding.get("primary_understanding_id"))
            if understanding.get("understanding_id") is not None:
                return str(understanding.get("understanding_id"))
        return None

    def _result_candidate_set_id(self, result: CommandResult) -> str | None:
        if not isinstance(result.result, dict):
            return None
        candidate_set = result.result.get("candidate_set")
        if isinstance(candidate_set, dict) and candidate_set.get("candidate_set_id") is not None:
            return str(candidate_set.get("candidate_set_id"))
        return None

    def _result_route_decision_id(self, result: CommandResult) -> str | None:
        if not isinstance(result.result, dict):
            return None
        route_decision = result.result.get("route_decision")
        if isinstance(route_decision, dict) and route_decision.get("route_decision_id") is not None:
            return str(route_decision.get("route_decision_id"))
        return None

    def _result_semantic_acceptance_decision_id(self, result: CommandResult) -> str | None:
        if not isinstance(result.result, dict):
            return None
        execution = result.result.get("execution")
        if isinstance(execution, dict):
            acceptance_result = execution.get("acceptance_result")
            if isinstance(acceptance_result, dict) and acceptance_result.get("semantic_acceptance_decision_id") is not None:
                return str(acceptance_result.get("semantic_acceptance_decision_id"))
        preview = result.result.get("preview")
        if isinstance(preview, dict):
            acceptance_result = preview.get("acceptance_result")
            if isinstance(acceptance_result, dict) and acceptance_result.get("semantic_acceptance_decision_id") is not None:
                return str(acceptance_result.get("semantic_acceptance_decision_id"))
        return None

    def _result_loop_snapshot_id(self, result: CommandResult) -> str | None:
        if not isinstance(result.result, dict):
            return None
        if result.result.get("loop_snapshot_id") is not None:
            return str(result.result.get("loop_snapshot_id"))
        loop_snapshot = result.result.get("loop_snapshot")
        if isinstance(loop_snapshot, dict) and loop_snapshot.get("loop_snapshot_id") is not None:
            return str(loop_snapshot.get("loop_snapshot_id"))
        return None

    def _record_v06_trace(self, result, *, overall_status: str) -> None:
        goal_id = result.goal_runtime.goal_id
        turn_id = result.turn.turn_id
        current_strategy_history = [item for item in result.ledger.strategy_history if item.turn_id == turn_id]
        for entry in current_strategy_history:
            record_trace_event(
                phase="routing",
                event_type="strategy_selected",
                status=entry.action,
                actor="agent_runtime",
                goal_id=goal_id,
                turn_id=entry.turn_id,
                attempt_id=entry.attempt_id,
                selected_strategy_id=entry.strategy_id,
                data={
                    "strategy_decision_id": entry.strategy_decision_id,
                    "reason": entry.reason,
                    "failure_class": entry.failure_class,
                    "provider_name": entry.provider_name,
                    "model_name": entry.model_name,
                    "parse_valid": entry.parse_valid,
                    "fallback_used": entry.fallback_used,
                    "error": entry.error,
                    "constraints": asdict(entry.constraints),
                },
            )
        current_attempts = [item for item in result.ledger.attempts if item.turn_id == turn_id]
        for attempt in current_attempts:
            record_trace_event(
                phase="execution",
                event_type="attempt_completed",
                status=attempt.outcome_status,
                actor="agent_runtime",
                goal_id=goal_id,
                turn_id=attempt.turn_id,
                attempt_id=attempt.attempt_id,
                plan_id=attempt.task_plan_id,
                selected_strategy_id=attempt.strategy_id,
                data={
                    "route_id": attempt.route_id,
                    "capability_surface": attempt.capability_surface,
                    "interaction_surface": attempt.interaction_surface,
                    "failure_class": attempt.failure_class,
                    "message": attempt.message,
                },
            )
            for envelope in attempt.tool_invocations:
                phase = "execution"
                event_type = "tool_completed"
                if envelope.family == "observer":
                    phase = "observation"
                    event_type = "observation_captured"
                elif envelope.family == "verifier":
                    phase = "verification"
                    event_type = "verifier_completed"
                record_trace_event(
                    phase=phase,
                    event_type=event_type,
                    status=envelope.status,
                    actor="tool_registry",
                    goal_id=goal_id,
                    turn_id=attempt.turn_id,
                    attempt_id=attempt.attempt_id,
                    plan_id=attempt.task_plan_id,
                    step_id=str(envelope.input_payload.get("task_step_id") or ""),
                    selected_strategy_id=attempt.strategy_id,
                    data={
                        "tool_id": envelope.tool_id,
                        "family": envelope.family,
                        "capability_surface": envelope.capability_surface,
                        "message": envelope.message,
                        "failure_class": envelope.failure_class,
                        "input_payload": envelope.input_payload,
                        "output_payload": envelope.output_payload,
                        "evidence": envelope.evidence,
                    },
                )
        record_trace_event(
            phase="acceptance",
            event_type="run_outcome_recorded",
            status=result.terminal_outcome.status,
            actor="agent_runtime",
            goal_id=goal_id,
            turn_id=turn_id,
            selected_strategy_id=result.selected_strategy_id,
            data={
                "reason": result.terminal_outcome.reason,
                "failure_class": result.terminal_outcome.failure_class,
                "verifier_confirmed": result.terminal_outcome.verifier_confirmed,
                "overall_status": overall_status,
            },
        )

    def _record_legacy_execution_trace(self, plan: TaskPlan, execution: PlanExecutionResult) -> None:
        record_trace_event(
            phase="execution",
            event_type="plan_execution_completed",
            status=execution.status,
            actor="broker",
            plan_id=plan.plan_id,
            data={
                "execution_status": execution.execution_status,
                "acceptance_status": execution.acceptance_status,
                "overall_status": execution.overall_status,
                "error": execution.error,
            },
        )
        for step_result in execution.step_results:
            record_trace_event(
                phase="execution",
                event_type="step_completed",
                status=step_result.status,
                actor="broker",
                plan_id=plan.plan_id,
                step_id=step_result.step_id,
                data=asdict(step_result),
            )
        for verification in execution.verification_results:
            record_trace_event(
                phase="verification",
                event_type="verifier_completed",
                status=str(verification.get("status", "unknown")),
                actor="broker",
                plan_id=plan.plan_id,
                data=verification,
            )
        record_trace_event(
            phase="acceptance",
            event_type="acceptance_decided",
            status=execution.acceptance_status,
            actor="broker",
            plan_id=plan.plan_id,
            data=execution.acceptance_result or {},
        )

    def _handle_task_plan_request(self, request: CommandRequest) -> CommandResult | None:
        planning = plan_turn(
            request.utterance,
            self.intent_broker,
            selection_provider=self.route_selection_provider,
            clarification_provider=self.clarification_provider,
            analysis_provider=self.understanding_analysis_provider,
            goal_synthesis_provider=self.goal_synthesis_provider,
            debug=request.debug,
        )
        return self._run_task_plan_goal_loop(request, planning)

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

    def _compatibility_goal_spec(self, planning, plan: TaskPlan, goal_id: str) -> GoalSpec:
        goal_synthesis = getattr(planning, "goal_synthesis", None)
        goal_spec = getattr(goal_synthesis, "goal_spec", None)
        if goal_spec is not None:
            return goal_spec
        return self._goal_spec_from_plan(plan, goal_id)

    def _compatibility_interaction_surface(self, plan: TaskPlan) -> InteractionSurface:
        provenance = plan.provenance if isinstance(plan.provenance, dict) else {}
        surface = str(provenance.get("interaction_surface") or "")
        if surface == "structured" or plan.selected_route_id == "browser_search_followup_route":
            return "structured_ui_action"
        if surface == "shortcut":
            return "computer_use_action"
        return "native_action"

    def _compatibility_strategy_for_plan(self, plan: TaskPlan, goal_spec: GoalSpec) -> StrategyCandidate:
        route = plan.routes[0] if plan.routes else TaskRoute(id=plan.selected_route_id or "route_unresolved", score=0.0, domain_id="unknown")
        capability_surface = "browser" if route.domain_id == "browser" else "desktop-linux"
        interaction_surface = self._compatibility_interaction_surface(plan)
        return StrategyCandidate(
            strategy_id=f"strategy_{plan.selected_route_id}",
            goal_id=goal_spec.goal_id,
            title=plan.display.goal or plan.selected_route_id,
            route_id=plan.selected_route_id,
            capability_surface=capability_surface,
            task_plan=plan,
            steps=(),
            interaction_surface=interaction_surface,
            priority=float(route.score or 1.0),
            requires_desktop_integration=capability_surface == "desktop-linux",
            metadata={"compatibility_runtime": True},
        )

    def _stored_review_payload_for_task_plan(self, request: CommandRequest, planning, plan: TaskPlan) -> dict[str, object]:
        goal_spec = self._compatibility_goal_spec(planning, plan, getattr(planning.goal_synthesis.goal_spec, "goal_id", f"goal_review_{plan.plan_id}") if getattr(planning, "goal_synthesis", None) and getattr(planning.goal_synthesis, "goal_spec", None) else f"goal_review_{plan.plan_id}")
        strategy = self._compatibility_strategy_for_plan(plan, goal_spec)
        environment = self._build_v06_environment_profile(request, planning)
        return self._v06_stored_review_payload(
            plan=plan,
            goal_id=goal_spec.goal_id,
            strategies=(strategy,),
            selected_strategy_id=strategy.strategy_id,
            environment=environment,
            semantic_metadata=self._semantic_strategy_metadata(planning),
        )

    def _compatibility_runtime_result(
        self,
        request: CommandRequest,
        planning,
        attempts: tuple[PlanAttempt, ...],
        *,
        context: RunContext,
        overall_status: str,
        message: str,
    ):
        plan = attempts[-1].task_plan if attempts and attempts[-1].task_plan is not None else planning.plan
        if plan is None:
            return None, None
        goal_spec = self._compatibility_goal_spec(planning, plan, getattr(planning.goal_synthesis.goal_spec, "goal_id", f"goal_{plan.plan_id}") if getattr(planning, "goal_synthesis", None) and getattr(planning.goal_synthesis, "goal_spec", None) else f"goal_{plan.plan_id}")
        environment = self._build_v06_environment_profile(request, planning)
        strategies: list[StrategyCandidate] = []
        strategy_history: list[tuple[object, str | None]] = []
        runtime_attempts: list[AttemptLedgerEntry] = []
        last_failure_class = "none"
        if not attempts:
            strategy = self._compatibility_strategy_for_plan(plan, goal_spec)
            decision = self.agent_runtime.recovery_policy.select_strategy(
                utterance=request.utterance,
                strategies=(strategy,),
                constraints=StrategyConstraint(),
                environment=environment,
                attempts=(),
                last_failure_class="none",
            )
            strategies.append(strategy)
            strategy_history.append((decision, None))
        for attempt in attempts:
            attempt_plan = attempt.task_plan or plan
            strategy = self._compatibility_strategy_for_plan(attempt_plan, goal_spec)
            if all(existing.strategy_id != strategy.strategy_id for existing in strategies):
                strategies.append(strategy)
            decision = self.agent_runtime.recovery_policy.select_strategy(
                utterance=request.utterance,
                strategies=(strategy,),
                constraints=StrategyConstraint(),
                environment=environment,
                attempts=tuple(runtime_attempts),
                last_failure_class=last_failure_class,
            )
            strategy_history.append((decision, attempt.attempt_id))
            failure = attempt.failure
            execution = attempt.execution_result
            runtime_attempts.append(
                AttemptLedgerEntry(
                    attempt_id=attempt.attempt_id,
                    turn_id="external_turn_pending",
                    goal_id=goal_spec.goal_id,
                    strategy_id=strategy.strategy_id,
                    route_id=attempt.selected_route_id or attempt_plan.selected_route_id,
                    trigger=attempt.trigger,
                    task_plan_id=attempt_plan.plan_id,
                    capability_surface=strategy.capability_surface,
                    interaction_surface=strategy.interaction_surface,
                    understanding_id=attempt.understanding_id,
                    candidate_set_id=attempt.candidate_set_id,
                    route_decision_id=attempt.route_decision_id,
                    replan_decision_id=attempt.replan_decision_id,
                    semantic_summary_id=attempt.semantic_summary_id,
                    semantic_acceptance_decision_id=attempt.semantic_acceptance_decision_id,
                    step_safety_review_ids=attempt.step_safety_review_ids,
                    outcome_status=execution.overall_status if execution is not None else "failed",
                    failure_class=failure.failure_class if failure is not None else "none",
                    message=(
                        failure.message
                        if failure is not None and failure.message
                        else (execution.error if execution is not None and execution.error else message)
                    ),
                )
            )
            last_failure_class = failure.failure_class if failure is not None else "none"
        terminal_status = "completed" if overall_status == "dry_run" else overall_status
        terminal = TerminalOutcome(
            status=terminal_status,  # type: ignore[arg-type]
            reason=message,
            failure_class=last_failure_class if last_failure_class != "none" else ("none" if overall_status in {"completed", "incomplete", "needs_user_input", "needs_review"} else "task_plan_failed"),
            verifier_confirmed=overall_status == "completed",
        )
        result = project_legacy_runtime_payload(
            context=context,
            goal_spec=goal_spec,
            utterance=request.utterance,
            strategies=tuple(strategies),
            selected_strategy_id=strategies[-1].strategy_id if strategies else None,
            strategy_history=tuple(strategy_history),
            attempts=tuple(runtime_attempts),
            terminal=terminal,
        )
        return result, environment

    def _task_plan_to_v06_strategy(
        self,
        plan: TaskPlan,
        goal_spec: GoalSpec | str,
        index: int,
        semantic_metadata: dict[str, object] | None = None,
    ) -> StrategyCandidate | None:
        if not plan.routes or not plan.steps:
            return None
        review_records = tuple(self._step_safety_review_record(plan.plan_id, step) for step in plan.steps)
        reviews = tuple(review for review, _ in review_records)
        if any(not review.allowed for review in reviews):
            return None
        review_required = any(review.review_required for review in reviews)
        step_safety_review_ids = tuple(step_review.step_safety_review_id for _, step_review in review_records)
        route = plan.routes[0]
        first_step = plan.steps[0]
        priority = float(route.score or max(1, len(plan.routes) - index))
        resolved_goal_id = goal_spec.goal_id if isinstance(goal_spec, GoalSpec) else goal_spec
        assistant_intent = goal_spec.assistant_intent if isinstance(goal_spec, GoalSpec) else None
        base_metadata = dict(semantic_metadata or {})
        base_metadata.setdefault("step_safety_review_ids", step_safety_review_ids)
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
                metadata={**base_metadata, "acceptance_mode": "action_only", "review_required": review_required},
            )
        if route.domain_id == "app_interaction" and first_step.action == "app.search_history":
            app_name = str(first_step.target.get("app") or first_step.target.get("name") or "")
            query = str(first_step.target.get("query") or "")
            if not app_name or not query:
                return None
            if route.id == "app_structured_search_route":
                return StrategyCandidate(
                    strategy_id=f"strategy_{route.id}",
                    goal_id=resolved_goal_id,
                    title=plan.display.goal or route.id,
                    route_id=route.id,
                    capability_surface="desktop-linux",
                    task_plan=plan,
                    steps=(
                        StrategyStep(tool_id="app.fixture.locate_search_control", input_payload={"app": app_name, "task_step_id": first_step.id}, task_step_id=first_step.id),
                        StrategyStep(tool_id="app.fixture.enter_search_query", input_payload={"app": app_name, "query": query, "task_step_id": first_step.id}, task_step_id=first_step.id),
                        StrategyStep(tool_id="app.fixture.observe_results", input_payload={"app": app_name, "task_step_id": first_step.id}, task_step_id=first_step.id),
                        StrategyStep(tool_id="app.fixture.verify_target_presence", input_payload={"app": app_name, "query": query, "task_step_id": first_step.id}, task_step_id=first_step.id),
                    ),
                    interaction_surface="structured_ui_action",
                    priority=max(priority, 10.0),
                    requires_desktop_integration=True,
                    metadata={**base_metadata, "acceptance_mode": "verification_required", "review_required": review_required, "enable_surface_downgrade": True},
                )
            if route.id == "app_shortcut_search_route":
                return StrategyCandidate(
                    strategy_id=f"strategy_{route.id}",
                    goal_id=resolved_goal_id,
                    title=plan.display.goal or route.id,
                    route_id=route.id,
                    capability_surface="desktop-linux",
                    task_plan=plan,
                    steps=(
                        StrategyStep(tool_id="app.fixture.activate_search_shortcut", input_payload={"app": app_name, "task_step_id": first_step.id}, task_step_id=first_step.id),
                        StrategyStep(tool_id="app.fixture.enter_search_query", input_payload={"app": app_name, "query": query, "task_step_id": first_step.id}, task_step_id=first_step.id),
                        StrategyStep(tool_id="app.fixture.observe_results", input_payload={"app": app_name, "task_step_id": first_step.id}, task_step_id=first_step.id),
                        StrategyStep(tool_id="app.fixture.verify_target_presence", input_payload={"app": app_name, "query": query, "task_step_id": first_step.id}, task_step_id=first_step.id),
                    ),
                    interaction_surface="computer_use_action",
                    priority=max(priority, 7.0),
                    requires_desktop_integration=True,
                    metadata={**base_metadata, "acceptance_mode": "verification_required", "review_required": review_required, "enable_surface_downgrade": True},
                )
            return None
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
                metadata={**base_metadata, "acceptance_mode": "verification_required", "review_required": review_required},
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
                metadata={**base_metadata, "acceptance_mode": "action_only", "review_required": review_required},
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
                metadata={**base_metadata, "acceptance_mode": "action_only", "review_required": review_required},
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
                metadata={**base_metadata, "acceptance_mode": "action_only", "review_required": review_required},
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
                metadata={**base_metadata, "acceptance_mode": "action_only", "review_required": review_required},
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
                metadata={**base_metadata, "acceptance_mode": "action_only", "review_required": review_required},
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

    @staticmethod
    def _semantic_strategy_metadata(planning) -> dict[str, object]:
        return {
            "understanding_id": root_understanding_id(planning.understanding) if getattr(planning, "understanding", None) is not None else None,
            "candidate_set_id": planning.candidate_set.candidate_set_id if getattr(planning, "candidate_set", None) is not None else None,
            "route_decision_id": planning.route_decision.route_decision_id if getattr(planning, "route_decision", None) is not None else None,
        }

    def _v06_stored_review_payload(
        self,
        *,
        plan: TaskPlan,
        goal_id: str,
        strategies: tuple[StrategyCandidate, ...],
        selected_strategy_id: str,
        environment: EnvironmentProfile,
        semantic_metadata: dict[str, object] | None = None,
    ) -> dict[str, object]:
        payload = asdict(plan)
        payload["v0_6_runtime"] = {
            "goal_id": goal_id,
            "selected_strategy_id": selected_strategy_id,
            "strategy_candidates": [self._strategy_payload(item) for item in strategies],
            "environment_profile": asdict(environment),
            "semantic_metadata": dict(semantic_metadata or {}),
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
            "understanding_id": attempt.understanding_id,
            "candidate_set_id": attempt.candidate_set_id,
            "route_decision_id": attempt.route_decision_id,
            "step_safety_review_ids": list(attempt.step_safety_review_ids),
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
        return ToolRegistry(
            (
                *app_tool_specs(self.apps),
                *window_tool_specs(self.windows),
                *notification_tool_specs(self.notifications),
                *clipboard_tool_specs(self.clipboard),
                *system_tool_specs(self.portal, self.capabilities),
                *browser_tool_specs(self.portal, self.verifier_harness),
                *fixture_tool_specs(),
            )
        )

    def _run_task_plan_goal_loop(self, request: CommandRequest, planning) -> CommandResult:
        goal_spec = planning.goal_synthesis.goal_spec if planning.goal_synthesis is not None else None
        loop = self._make_goal_loop()
        run_id = self._make_run_id(request.utterance)
        goal_id = goal_spec.goal_id if goal_spec is not None else "goal_unresolved"
        result = loop.run(
            request=request,
            planning=planning,
            run_id=run_id,
            goal_id=goal_id,
        )
        return self._finalize_goal_loop_result(
            request=request,
            planning=planning,
            run_id=run_id,
            goal_id=goal_id,
            loop_result=result,
        )

    def _finalize_goal_loop_result(self, *, request: CommandRequest, planning, run_id: str, goal_id: str, loop_result) -> CommandResult:
        payload = dict(loop_result.payload)
        result_review_id = loop_result.review_id or request.review_id
        if result_review_id:
            payload["review_id"] = result_review_id
        payload["loop_snapshot"] = asdict(loop_result.state)
        payload["loop_snapshot_id"] = loop_result.state.loop_snapshot_id
        intent = Intent.unknown(loop_result.message or "goal loop result")
        if getattr(planning, "plan", None) is not None and planning.plan.steps:
            intent = self._intent_from_task_step(planning.plan.steps[0])
        review = None
        stored_review = None
        if result_review_id:
            stored_review = self.reviews.get(result_review_id)
            review = stored_review.review if stored_review is not None else None

        if loop_result.overall_status == "needs_review" and getattr(planning, "plan", None) is not None:
            payload["plan_review"] = {
                "plan_id": planning.plan.plan_id,
                "status": "review_required",
                "review_id": result_review_id,
                "max_risk_level": review.risk_level if review is not None else "L2",
                "step_reviews": list(stored_review.step_reviews) if stored_review is not None else [],
                "message": loop_result.message,
            }

        status = "review_required" if loop_result.overall_status == "needs_review" else ("ambiguous" if loop_result.overall_status == "needs_user_input" else ("dry_run" if loop_result.overall_status == "dry_run" else ("executed" if loop_result.overall_status == "completed" else "failed")))
        message = loop_result.message
        execution_status = loop_result.execution_status
        acceptance_status = loop_result.acceptance_status
        overall_status = loop_result.overall_status
        if getattr(planning, "plan", None) is None:
            compatibility_intent = self._compatibility_intent_from_planning(planning)
            compatibility_review = self.policy.review(compatibility_intent)
            if not compatibility_review.allowed:
                intent = compatibility_intent
                review = compatibility_review
                status = "rejected"
                message = compatibility_review.reason
                execution_status = "not_started"
                acceptance_status = "skipped"
                overall_status = "failed"

        compat_result, compat_environment = self._compatibility_runtime_result(
            request,
            planning,
            loop_result.attempt_records,
            context=RunContext.from_request(request, run_id=run_id, goal_id=goal_id),
            overall_status=overall_status,
            message=message,
        )
        if compat_result is not None and compat_environment is not None:
            payload["environment_profile"] = asdict(compat_environment)
            payload["goal_runtime"] = asdict(compat_result.goal_runtime)
            payload["goal_turn"] = asdict(compat_result.turn)
            payload["strategy_candidates"] = [self._strategy_payload(item) for item in compat_result.strategy_candidates]
            payload["selected_strategy_id"] = compat_result.selected_strategy_id
            payload["run_ledger"] = asdict(compat_result.ledger)
            debug_trace = payload.get("debug_trace")
            if isinstance(debug_trace, dict):
                debug_trace["runtime_task_plan"] = {
                    **dict(compat_result.debug_payload),
                    "environment_profile": asdict(compat_environment),
                }
        return self._finalize_task_plan_result(
            request=request,
            run_id=run_id,
            goal_id=goal_id,
            attempts=loop_result.attempt_records,
            payload=payload,
            intent=intent,
            status=status,
            message=message,
            execution_status=execution_status,
            acceptance_status=acceptance_status,
            overall_status=overall_status,
            selected_target=loop_result.selected_target,
            review=review,
            review_id=result_review_id,
        )

    def _resolve_planning_understanding_transition(self, planning, *, trigger: str):
        understanding = getattr(planning, "understanding", None)
        analysis = getattr(planning, "analysis", None)
        if understanding is None or analysis is None:
            return planning
        decision = UnderstandingAnalysisDecision(
            analysis=analysis,
            provider_name="planning_understanding_sync",
            model_name="deterministic-local",
            request_payload={
                "trigger": trigger,
                "understanding_id": root_understanding_id(understanding),
                "active_understanding_id": understanding.understanding_id,
            },
        )
        return self._apply_understanding_transition(
            planning,
            analysis_decision=decision,
            reason=f"planning trigger {trigger} changed the active understanding basis",
        )

    def _planning_from_replan_decision(self, planning, *, decision: ReplanDecision, failure: FailureClassification):
        understanding = getattr(planning, "understanding", None)
        analysis = getattr(planning, "analysis", None)
        if understanding is None or analysis is None:
            return planning
        transition = self.understanding_transition_provider.transition(
            understanding=understanding,
            current_analysis=analysis,
            decision=decision,
            failure=failure,
        )
        return self._apply_understanding_transition(
            planning,
            analysis_decision=transition,
            reason=f"replanning action {decision.action} updated the understanding basis",
        )

    def _apply_understanding_transition(
        self,
        planning,
        *,
        analysis_decision: UnderstandingAnalysisDecision,
        reason: str,
    ):
        understanding = planning.understanding
        analysis = analysis_decision.analysis
        updated_understanding, refinement, supersession = reconcile_understanding_transition(
            understanding,
            analysis,
            reason=reason,
        )
        return self._planning_with_understanding_transition(
            planning,
            previous_understanding=understanding,
            updated_understanding=updated_understanding,
            refinement=refinement,
            supersession=supersession,
            analysis_decision=analysis_decision,
        )

    def _planning_with_understanding_transition(
        self,
        planning,
        *,
        previous_understanding: UnderstandingArtifact,
        updated_understanding: UnderstandingArtifact,
        refinement,
        supersession,
        analysis_decision: UnderstandingAnalysisDecision,
    ):
        if refinement is None and supersession is None:
            return planning
        artifact_id = refinement.refinement_id if refinement is not None else supersession.supersession_id
        artifact_role = "refinement" if refinement is not None else "supersession"
        changed_fields = refinement.changed_fields if refinement is not None else supersession.changed_fields
        reason = refinement.reason if refinement is not None else supersession.reason
        primary_understanding_id = root_understanding_id(updated_understanding)
        source_artifact_ids = [previous_understanding.understanding_id]
        if previous_understanding.source_understanding_id is not None:
            source_artifact_ids.append(previous_understanding.source_understanding_id)
        record_model_io(
            phase="analysis",
            provider=analysis_decision.provider_name,
            model=analysis_decision.model_name,
            request_payload=analysis_decision.request_payload,
            response_payload=analysis_decision.response_payload,
            normalized_output=asdict(updated_understanding),
            parse_valid=analysis_decision.parse_valid,
            fallback_used=analysis_decision.fallback_used,
            error=analysis_decision.error,
            actor="understanding_refiner",
            call_kind="structured_followup",
            consumed_artifacts={
                "understanding_id": primary_understanding_id,
                "active_understanding_id": previous_understanding.understanding_id,
                "candidate_set_id": planning.candidate_set.candidate_set_id if planning.candidate_set else None,
                "route_decision_id": planning.route_decision.route_decision_id if planning.route_decision else None,
            },
        )
        record_trace_event(
            phase="analysis",
            event_type="understanding_refined" if refinement is not None else "understanding_superseded",
            status=updated_understanding.analysis.type,
            actor="broker",
            data={
                "artifact_type": "understanding_transition",
                "artifact_id": artifact_id,
                "source_artifact_ids": source_artifact_ids,
                "artifact_role": artifact_role,
                "primary_understanding_id": updated_understanding.primary_understanding_id,
                "previous_understanding_id": previous_understanding.understanding_id,
                "active_understanding_id": updated_understanding.understanding_id,
                "changed_fields": list(changed_fields),
                "reason": reason,
            },
        )
        return replace(
            planning,
            understanding=updated_understanding,
            analysis=updated_understanding.analysis,
            understanding_refinement=refinement,
            understanding_supersession=supersession,
        )

    def _planning_payload(self, planning) -> dict[str, object]:
        return {
            "understanding": asdict(planning.understanding),
            "analysis": asdict(planning.analysis),
            "understanding_refinement": asdict(planning.understanding_refinement) if planning.understanding_refinement else None,
            "understanding_supersession": asdict(planning.understanding_supersession) if planning.understanding_supersession else None,
            "goal_synthesis": asdict(planning.goal_synthesis) if planning.goal_synthesis else None,
            "assistant_intent": assistant_intent_to_payload(planning.goal_synthesis.goal_spec.assistant_intent) if planning.goal_synthesis and planning.goal_synthesis.goal_spec else None,
            "plan": asdict(planning.plan) if planning.plan else None,
            "candidate_set": asdict(planning.candidate_set) if planning.candidate_set else None,
            "route_decision": asdict(planning.route_decision) if planning.route_decision else None,
            "candidates": [asdict(candidate) for candidate in planning.candidates],
            "domain_routing": asdict(planning.domain_routing) if planning.domain_routing else None,
            "observation_request": asdict(planning.observation_request) if planning.observation_request else None,
            "observation_receipt": asdict(planning.observation_receipt) if planning.observation_receipt else None,
            "capability_exposure": asdict(planning.capability_exposure) if planning.capability_exposure else None,
            "trace": asdict(planning.trace) if planning.trace is not None else {},
            "debug_trace": asdict(planning.debug_trace) if planning.debug_trace is not None else {},
        }

    def _planning_from_loop_payload(self, *, utterance: str, payload: dict[str, object]):
        plan_payload = payload.get("plan") if isinstance(payload.get("plan"), dict) else None
        plan = task_plan_from_payload(plan_payload) if plan_payload is not None else None
        candidate_payloads = payload.get("candidates")
        candidates = tuple(
            task_plan_from_payload(item)
            for item in candidate_payloads
            if isinstance(item, dict)
        ) if isinstance(candidate_payloads, list) else ()
        candidate_set_payload = payload.get("candidate_set") if isinstance(payload.get("candidate_set"), dict) else None
        route_decision_payload = payload.get("route_decision") if isinstance(payload.get("route_decision"), dict) else None
        understanding = self._understanding_from_loop_payload(utterance=utterance, payload=payload)
        analysis = understanding.analysis
        return PlanningArtifacts(
            understanding=understanding,
            analysis=analysis,
            goal_synthesis=None,
            plan=plan,
            candidates=candidates,
            understanding_refinement=None,
            understanding_supersession=None,
            candidate_set=candidate_set_from_payload(candidate_set_payload) if candidate_set_payload is not None else None,
            route_decision=candidate_selection_decision_from_payload(route_decision_payload) if route_decision_payload is not None else None,
            domain_routing=None,
            observation_request=None,
            observation_receipt=None,
            capability_exposure=None,
            trace=None,
            debug_trace=None,
        )

    def _understanding_from_loop_payload(self, *, utterance: str, payload: dict[str, object]) -> UnderstandingArtifact:
        understanding_payload = payload.get("understanding") if isinstance(payload.get("understanding"), dict) else {}
        analysis_payload = payload.get("analysis") if isinstance(payload.get("analysis"), dict) else understanding_payload.get("analysis")
        if not isinstance(analysis_payload, dict):
            plan = task_plan_from_payload(payload["plan"]) if isinstance(payload.get("plan"), dict) else None
            analysis_payload = {
                "type": "task" if plan is not None else "clarification",
                "confidence": 0.5,
                "domains": [route.domain_id for route in plan.routes if route.domain_id] if plan is not None else ["browser"],
                "explanation": "restored from stored goal loop payload",
                "chat_response": None,
            }
        analysis = validated_understanding_from_payload(
            utterance=utterance,
            payload=analysis_payload,
            host_hint=default_understanding_host_hint(utterance),
        )
        understanding_id = str(understanding_payload.get("understanding_id") or f"und_resume_{sha256(utterance.encode('utf-8')).hexdigest()[:10]}")
        primary_understanding_id = understanding_payload.get("primary_understanding_id")
        source_understanding_id = understanding_payload.get("source_understanding_id")
        return UnderstandingArtifact(
            understanding_id=understanding_id,
            utterance=utterance,
            analysis=analysis,
            artifact_role=str(understanding_payload.get("artifact_role", "primary")),
            primary_understanding_id=str(primary_understanding_id) if primary_understanding_id is not None else understanding_id,
            source_understanding_id=str(source_understanding_id) if source_understanding_id is not None else None,
            refinement_id=str(understanding_payload["refinement_id"]) if understanding_payload.get("refinement_id") is not None else None,
            supersession_id=str(understanding_payload["supersession_id"]) if understanding_payload.get("supersession_id") is not None else None,
            provider_intent=None,
            provider_parse_count=int(understanding_payload.get("provider_parse_count", 0)),
            provider_cache_hit_count=int(understanding_payload.get("provider_cache_hit_count", 0)),
            uncertainty_reasons=tuple(str(item) for item in understanding_payload.get("uncertainty_reasons", ())),
            clarification_question_id=str(understanding_payload["clarification_question_id"]) if understanding_payload.get("clarification_question_id") is not None else None,
            clarification_provider_name=str(understanding_payload["clarification_provider_name"]) if understanding_payload.get("clarification_provider_name") is not None else None,
            clarification_model_name=str(understanding_payload["clarification_model_name"]) if understanding_payload.get("clarification_model_name") is not None else None,
            clarification_parse_valid=bool(understanding_payload.get("clarification_parse_valid", True)),
            clarification_fallback_used=bool(understanding_payload.get("clarification_fallback_used", False)),
            clarification_error=str(understanding_payload["clarification_error"]) if understanding_payload.get("clarification_error") is not None else None,
            analysis_provider_name=str(understanding_payload["analysis_provider_name"]) if understanding_payload.get("analysis_provider_name") is not None else None,
            analysis_model_name=str(understanding_payload["analysis_model_name"]) if understanding_payload.get("analysis_model_name") is not None else None,
            analysis_parse_valid=bool(understanding_payload.get("analysis_parse_valid", True)),
            analysis_fallback_used=bool(understanding_payload.get("analysis_fallback_used", False)),
            analysis_error=str(understanding_payload["analysis_error"]) if understanding_payload.get("analysis_error") is not None else None,
        )

    @staticmethod
    def _merge_user_input_utterance(utterance: str, supplemental_input: str) -> str:
        base = utterance.strip()
        detail = supplemental_input.strip()
        return f"{base}\n\nAdditional user detail: {detail}" if base else detail

    @staticmethod
    def _replan_available_domain_ids(planning) -> tuple[str, ...]:
        goal_synthesis = getattr(planning, "goal_synthesis", None)
        goal_spec = getattr(goal_synthesis, "goal_spec", None)
        if goal_spec is not None and getattr(goal_spec, "candidate_domain_ids", ()):
            return tuple(str(item) for item in goal_spec.candidate_domain_ids if str(item))
        candidate_set = getattr(planning, "candidate_set", None)
        if candidate_set is not None and getattr(candidate_set, "candidates", ()):
            return tuple(
                dict.fromkeys(
                    str(candidate.domain_id)
                    for candidate in candidate_set.candidates
                    if getattr(candidate, "domain_id", None)
                )
            )
        candidates = getattr(planning, "candidates", ())
        return tuple(
            dict.fromkeys(
                route.domain_id
                for candidate in candidates
                for route in getattr(candidate, "routes", ())
                if route.domain_id
            )
        )

    def _verification_harness_for_postconditions(self, *, registry, request, receipt) -> VerifierHarness:
        if request is None:
            return self.verifier_harness
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
                trace_run_id=run_id,
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
            "understanding_id": attempt.understanding_id,
            "candidate_set_id": attempt.candidate_set_id,
            "route_decision_id": attempt.route_decision_id,
            "replan_decision_id": attempt.replan_decision_id,
            "semantic_summary_id": attempt.semantic_summary_id,
            "semantic_acceptance_decision_id": attempt.semantic_acceptance_decision_id,
            "step_safety_review_ids": list(attempt.step_safety_review_ids),
            "selected_route_id": attempt.selected_route_id,
        }
        if attempt.task_plan is not None:
            payload["plan_id"] = attempt.task_plan.plan_id
            payload["step_ids"] = [step.id for step in attempt.task_plan.steps]
            payload["capability_ids"] = [step.capability_id for step in attempt.task_plan.steps]
            goal_id = self._goal_spec_from_plan(
                attempt.task_plan,
                attempt.task_plan.provenance.get("goal_id", f"goal_{attempt.task_plan.plan_id}") if isinstance(attempt.task_plan.provenance, dict) else f"goal_{attempt.task_plan.plan_id}",
            ).goal_id
            strategy = self._compatibility_strategy_for_plan(attempt.task_plan, self._goal_spec_from_plan(attempt.task_plan, goal_id))
            payload["strategy_id"] = strategy.strategy_id
            payload["capability_surface"] = strategy.capability_surface
            payload["interaction_surface"] = strategy.interaction_surface
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
        semantic_metadata = runtime_payload.get("semantic_metadata") if isinstance(runtime_payload.get("semantic_metadata"), dict) else {}
        strategy = self._task_plan_to_v06_strategy(plan, goal_id, 0, semantic_metadata=semantic_metadata)
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
        self._record_v06_trace(result, overall_status=overall_status)
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
        self._record_legacy_execution_trace(plan, execution)
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

    def _resume_loop_state(self, review_request) -> object | None:
        if not isinstance(review_request.snapshot_payload, dict):
            return None
        return loop_state_from_payload(review_request.snapshot_payload)

    def _planning_from_user_input_review(self, review_request, state: LoopState, supplemental_input: str) -> tuple[str, PlanningArtifacts]:
        restored = self._planning_from_loop_payload(
            utterance=review_request.utterance,
            payload=review_request.plan_payload or {},
        )
        if state.primary_understanding_id and not isinstance((review_request.plan_payload or {}).get("understanding"), dict):
            restored = replace(
                restored,
                understanding=replace(
                    restored.understanding,
                    understanding_id=state.primary_understanding_id,
                    primary_understanding_id=state.primary_understanding_id,
                    source_understanding_id=restored.understanding.source_understanding_id,
                ),
            )
        resumed_utterance = self._merge_user_input_utterance(review_request.utterance, supplemental_input)
        reinterpreted_understanding, _ = create_primary_understanding(
            resumed_utterance,
            self.intent_broker,
            clarification_provider=self.clarification_provider,
            analysis_provider=self.understanding_analysis_provider,
        )
        updated_understanding, refinement, supersession = reconcile_reinterpreted_understanding(
            restored.understanding,
            reinterpreted_understanding,
            reason="supplemental user input refined the understanding basis",
        )
        transition_decision = UnderstandingAnalysisDecision(
            analysis=updated_understanding.analysis,
            provider_name=updated_understanding.analysis_provider_name or "resume_understanding_refiner",
            model_name=updated_understanding.analysis_model_name or "deterministic-local",
            request_payload={
                "utterance": resumed_utterance,
                "supplemental_input": supplemental_input,
                "resume_kind": "user_input",
            },
            response_payload={"analysis": asdict(updated_understanding.analysis)},
            parse_valid=updated_understanding.analysis_parse_valid,
            fallback_used=updated_understanding.analysis_fallback_used,
            error=updated_understanding.analysis_error,
        )
        transitioned = self._planning_with_understanding_transition(
            restored,
            previous_understanding=restored.understanding,
            updated_understanding=updated_understanding,
            refinement=refinement,
            supersession=supersession,
            analysis_decision=transition_decision,
        )
        planned = plan_turn(
            resumed_utterance,
            self.intent_broker,
            selection_provider=self.route_selection_provider,
            clarification_provider=self.clarification_provider,
            analysis_provider=self.understanding_analysis_provider,
            goal_synthesis_provider=self.goal_synthesis_provider,
            understanding=transitioned.understanding,
        )
        return (
            resumed_utterance,
            replace(
                planned,
                understanding_refinement=transitioned.understanding_refinement,
                understanding_supersession=transitioned.understanding_supersession,
            ),
        )

    def _resume_loop_review(self, review_request, *, dry_run: bool, transport: str | None) -> CommandResult:
        state = self._resume_loop_state(review_request)
        if state is None:
            fallback = Intent.unknown("stored loop snapshot is missing", {"review_id": review_request.review_id})
            return self._with_transport(
                CommandResult(status="failed", intent=fallback, review_id=review_request.review_id, message="stored loop snapshot is missing"),
                transport,
            )
        planning_payload = review_request.plan_payload or {}
        planning = self._planning_from_loop_payload(utterance=review_request.utterance, payload=planning_payload)
        state = sync_loop_state_with_planning(state, planning)
        plan = getattr(planning, "plan", None)
        if plan is None:
            fallback = Intent.unknown("stored loop plan is missing", {"review_id": review_request.review_id})
            return self._with_transport(
                CommandResult(status="failed", intent=fallback, review_id=review_request.review_id, message="stored loop plan is missing"),
                transport,
            )
        loop = self._make_goal_loop()
        request = CommandRequest(
            review_request.utterance,
            dry_run=dry_run,
            approve=True,
            review_id=review_request.review_id,
            transport=transport,
        )
        loop_result = loop.resume_from_review(
            request=request,
            planning=planning,
            state=state,
            run_id=state.trace_run_id,
            goal_id=state.goal_id,
        )
        return self._finalize_goal_loop_result(
            request=request,
            planning=planning,
            run_id=state.trace_run_id,
            goal_id=state.goal_id,
            loop_result=loop_result,
        )

    def _resume_user_input_review(self, review_request, *, dry_run: bool, transport: str | None) -> CommandResult:
        state = self._resume_loop_state(review_request)
        if state is None:
            fallback = Intent.unknown("stored loop snapshot is missing", {"review_id": review_request.review_id})
            return self._with_transport(
                CommandResult(status="failed", intent=fallback, review_id=review_request.review_id, message="stored loop snapshot is missing"),
                transport,
            )
        supplemental_input = (review_request.supplemental_input or "").strip()
        if not supplemental_input:
            fallback = Intent.unknown("supplemental input is required", {"review_id": review_request.review_id})
            return self._with_transport(
                CommandResult(status="rejected", intent=fallback, review_id=review_request.review_id, message="supplemental input is required to resume this review"),
                transport,
            )
        resumed_utterance, planning = self._planning_from_user_input_review(review_request, state, supplemental_input)
        plan = getattr(planning, "plan", None)
        state = sync_loop_state_with_planning(normalize_state_for_plan(state, plan), planning)
        request = CommandRequest(
            resumed_utterance,
            dry_run=dry_run,
            review_id=review_request.review_id,
            supplemental_input=supplemental_input,
            transport=transport,
        )
        loop = self._make_goal_loop()
        loop_result = loop.resume_from_user_input(
            request=request,
            planning=planning,
            run_id=state.trace_run_id,
            goal_id=state.goal_id,
            state=state,
        )
        return self._finalize_goal_loop_result(
            request=request,
            planning=planning,
            run_id=state.trace_run_id,
            goal_id=state.goal_id,
            loop_result=loop_result,
        )

    def provide_review_input(self, review_id: str, supplemental_input: str, dry_run: bool = False, transport: str | None = None) -> CommandResult:
        if dry_run:
            review_request = self.reviews.get(review_id)
            if not review_request:
                fallback = Intent.unknown("review request not found", {"review_id": review_id})
                return self._with_transport(
                    CommandResult(status="rejected", intent=fallback, review_id=review_id, message="review request not found"),
                    transport,
                )
            if review_request.review_kind != "user_input":
                return self._with_transport(
                    CommandResult(
                        status="rejected",
                        intent=review_request.intent,
                        review=review_request.review,
                        review_id=review_id,
                        message="supplemental input is only valid for user-input reviews",
                    ),
                    transport,
                )
            preview_request = replace(review_request, supplemental_input=supplemental_input)
            result = self._resume_user_input_review(preview_request, dry_run=True, transport=transport)
        else:
            review_request = self.reviews.provide_input(review_id, supplemental_input)
            if not review_request:
                fallback = Intent.unknown("review request not found", {"review_id": review_id})
                return self._with_transport(
                    CommandResult(status="rejected", intent=fallback, review_id=review_id, message="review request not found"),
                    transport,
                )
            if review_request.status != "provided":
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
            if review_request.review_kind != "user_input":
                return self._with_transport(
                    CommandResult(
                        status="rejected",
                        intent=review_request.intent,
                        review=review_request.review,
                        review_id=review_id,
                        message="supplemental input is only valid for user-input reviews",
                    ),
                    transport,
                )
            result = self._resume_user_input_review(review_request, dry_run=False, transport=transport)
            if result.status in {"executed", "review_required", "ambiguous"}:
                self.reviews.consume(review_id)
        audit_id = self.audit.record(
            request=CommandRequest("", dry_run=dry_run, review_id=review_id, supplemental_input=supplemental_input, transport=transport),
            intent=result.intent,
            status=result.status,
            result=result.result,
            selected_target=result.selected_target,
            message=result.message,
            review=result.review,
            review_id=review_id,
            execution_status=result.execution_status,
            acceptance_status=result.acceptance_status,
            overall_status=result.overall_status,
            trace_run_id=result.trace_run_id,
            loop_snapshot_id=self._result_loop_snapshot_id(result),
        )
        return replace(result, audit_id=audit_id)

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
            if review_request.review_kind == "loop":
                result = self._resume_loop_review(review_request, dry_run=True, transport=transport)
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
                    layer="goal_loop",
                    execution_status=result.execution_status,
                    acceptance_status=result.acceptance_status,
                    overall_status=result.overall_status,
                    trace_run_id=result.trace_run_id,
                    loop_snapshot_id=self._result_loop_snapshot_id(result),
                )
                return replace(result, audit_id=audit_id)
            if review_request.review_kind == "user_input":
                return self._with_transport(
                    CommandResult(
                        status="rejected",
                        intent=review_request.intent,
                        review=review_request.review,
                        review_id=review_id,
                        message="use supplemental input to resume this review",
                    ),
                    transport,
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
        if review_request.review_kind != "user_input" and not self.reviews.claim_execution(review_id):
            return self._with_transport(
                CommandResult(
                    status="rejected",
                    intent=review_request.intent,
                    review=review_request.review,
                    review_id=review_id,
                    message="review request is already being executed or has been consumed",
                ),
                transport,
            )
        if review_request.review_kind == "plan" and review_request.plan_payload:
            result = self._approve_plan_review_v06(review_request, review_id=review_id, dry_run=dry_run, transport=transport)
            if result.status == "executed":
                self.reviews.consume(review_id)
            else:
                self.reviews.release_execution(review_id)
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
        if review_request.review_kind == "loop":
            result = self._resume_loop_review(review_request, dry_run=dry_run, transport=transport)
            if result.status in {"executed", "review_required", "ambiguous"}:
                self.reviews.consume(review_id)
            else:
                self.reviews.release_execution(review_id)
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
                layer="goal_loop",
                execution_status=result.execution_status,
                acceptance_status=result.acceptance_status,
                overall_status=result.overall_status,
                trace_run_id=result.trace_run_id,
                loop_snapshot_id=self._result_loop_snapshot_id(result),
            )
            return replace(result, audit_id=audit_id)
        if review_request.review_kind == "user_input":
            return self._with_transport(
                CommandResult(
                    status="rejected",
                    intent=review_request.intent,
                    review=review_request.review,
                    review_id=review_id,
                    message="use supplemental input to resume this review",
                ),
                transport,
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
        else:
            self.reviews.release_execution(review_id)
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
        trace_session = current_trace_session()
        created_trace = False
        if trace_session is None:
            trace_session = self.trace_store.start_run(
                run_id=self._make_run_id(review_id),
                command_name="reject",
                utterance="",
                mode="auto_low_risk",
                transport=transport,
                dry_run=False,
                debug=False,
                review_id=review_id,
            )
            created_trace = True
        scope = bind_trace_session(trace_session) if created_trace else nullcontext(trace_session)
        with scope:
            review_request = self.reviews.reject(review_id)
            if not review_request:
                fallback = Intent.unknown("review request not found", {"review_id": review_id})
                result = self._with_transport(
                    CommandResult(status="rejected", intent=fallback, review_id=review_id, message="review request not found"),
                    transport,
                )
            elif review_request.status != "rejected":
                result = self._with_transport(
                    CommandResult(
                        status="rejected",
                        intent=review_request.intent,
                        review=review_request.review,
                        review_id=review_id,
                        message=f"review request is not pending; current status is {review_request.status}",
                    ),
                    transport,
                )
            else:
                request = CommandRequest(
                    utterance=review_request.utterance,
                    approve=False,
                    review_id=review_id,
                    transport=transport,
                )
                audited = self.audit.record(
                    request=request,
                    intent=review_request.intent,
                    status="rejected",
                    result={"review_id": review_id, "review_status": "rejected"},
                    selected_target=None,
                    message="review request rejected by user",
                    review=review_request.review,
                    review_id=review_id,
                )
                result = self._with_transport(
                    CommandResult(
                        status="rejected",
                        intent=review_request.intent,
                        result={"review_id": review_id, "review_status": "rejected"},
                        trace_run_id=trace_session.run_id,
                        audit_id=audited,
                        review_id=review_id,
                        message="review request rejected by user",
                        review=review_request.review,
                    ),
                    transport,
                )
            if created_trace:
                trace_session.finalize(
                    status=result.status,
                    review_id=review_id,
                    message=result.message,
                    overall_status=result.overall_status,
                )
            return result

    def _browser_direct_site_url(self, name: str) -> str:
        return str(self.browser_site_catalog.get(name.lower(), "") or "")

    def _browser_search_official_url(self, query: str) -> str:
        payload = self.browser_search_catalog.get(query.lower(), {})
        if not isinstance(payload, dict):
            return ""
        return str(payload.get("official_url") or "")

    def _browser_named_target_url(self, name: str, *, resolution_mode: str) -> str:
        if resolution_mode == "direct":
            return self._browser_direct_site_url(name)
        if resolution_mode == "search_followup":
            return self._browser_search_official_url(name)
        return self._browser_direct_site_url(name) or self._browser_search_official_url(name)

    def _browser_navigation_command_result(
        self,
        request: CommandRequest,
        intent: Intent,
        review,
        *,
        uri: str,
        query: str | None = None,
        site: str | None = None,
        result_payload: dict[str, object] | None = None,
    ) -> CommandResult:
        payload = dict(result_payload or {})
        payload.setdefault("uri", uri)
        if query is not None:
            payload.setdefault("query", query)
        if site is not None:
            payload.setdefault("site", site)
        if request.dry_run:
            payload.setdefault("status", "dry_run")
            return CommandResult(
                status="dry_run",
                intent=intent,
                selected_target=uri,
                result=payload,
                review=review,
            )
        opened = self.portal.open_uri(uri)
        record_browser_navigation(
            uri=uri,
            query=query,
            site=site,
            adapter=str(opened.get("adapter")) if isinstance(opened, dict) and opened.get("adapter") is not None else None,
            status=str(opened.get("status") or "opened") if isinstance(opened, dict) else "opened",
        )
        status = "executed" if opened.get("status") == "opened" else "failed"
        combined_payload = dict(opened)
        combined_payload.update(payload)
        return CommandResult(status=status, intent=intent, selected_target=uri, result=combined_payload, review=review)

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
            if len(candidates) > 1 and not decisive(candidates[0].name, name):
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

        if intent.action == "app.search_history":
            app_name = str(intent.target.get("app") or intent.target.get("name") or "").strip()
            query = str(intent.target.get("query") or "").strip()
            interaction_surface = str(intent.target.get("interaction_surface") or "").strip() or "structured"
            if not app_name:
                return CommandResult(status="rejected", intent=intent, message="missing app name", review=review)
            if not query:
                return CommandResult(status="rejected", intent=intent, message="missing app search query", review=review)
            fixture = self.app_fixture_catalog.get(app_name.lower())
            if fixture is None:
                return CommandResult(
                    status="failed",
                    intent=intent,
                    message="no app fixture matched the requested application",
                    result={"status": "unavailable", "app": app_name},
                    review=review,
                )
            if request.dry_run:
                return CommandResult(
                    status="dry_run",
                    intent=intent,
                    selected_target=app_name,
                    result={"status": "dry_run", "app": app_name, "query": query, "interaction_surface": interaction_surface},
                    review=review,
                )
            if interaction_surface == "structured":
                if not fixture.has_control("search_box"):
                    return CommandResult(
                        status="failed",
                        intent=intent,
                        message="structured search control was not visible in the app fixture",
                        result={"status": "failed", "app": app_name, "query": query, "interaction_surface": interaction_surface},
                        review=review,
                    )
            elif interaction_surface == "shortcut":
                if not fixture.shortcut_search_enabled:
                    return CommandResult(
                        status="failed",
                        intent=intent,
                        message="shortcut search mode is unavailable in the app fixture",
                        result={"status": "failed", "app": app_name, "query": query, "interaction_surface": interaction_surface},
                        review=review,
                    )
            results = fixture.results_for(query)
            return CommandResult(
                status="executed",
                intent=intent,
                selected_target=app_name,
                result={
                    "status": "succeeded",
                    "adapter": "app.fixture.enter_search_query",
                    "app": app_name,
                    "query": query,
                    "interaction_surface": interaction_surface,
                    "result_count": len(results),
                    "observed_results": list(results),
                },
                review=review,
            )

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

        if intent.action == "browser.open_named_target":
            name = str(intent.target.get("name") or intent.target.get("target_name") or "").strip()
            resolution_mode = str(intent.target.get("resolution_mode") or "").strip() or "direct"
            if not name:
                return CommandResult(status="rejected", intent=intent, message="missing named browser target", review=review)
            official_url = self._browser_named_target_url(name, resolution_mode=resolution_mode)
            if not official_url:
                message = (
                    "browser search results did not provide a follow-up destination"
                    if resolution_mode == "search_followup"
                    else "no local direct-open resolution matched the named website target"
                )
                return CommandResult(
                    status="failed",
                    intent=intent,
                    message=message,
                    result={"status": "failed", "name": name, "resolution_mode": resolution_mode},
                    review=review,
                )
            result_payload = {"name": name, "official_url": official_url, "resolved_url": official_url, "resolution_mode": resolution_mode}
            return self._browser_navigation_command_result(
                request,
                intent,
                review,
                uri=official_url,
                result_payload=result_payload,
            )

        if intent.action in {"browser.open_url", "browser.search_web", "browser.open_site_search"}:
            if intent.action == "browser.search_web" and bool(intent.target.get("follow_search_result")):
                query = str(intent.target.get("query") or "").strip()
                named_target = str(intent.target.get("named_target") or query).strip()
                if not query:
                    return CommandResult(status="rejected", intent=intent, message="missing browser query", review=review)
                search_uri = browser_semantic_uri(Intent(action="browser.search_web", target={"query": query}))
                if not search_uri:
                    return CommandResult(status="rejected", intent=intent, message="missing browser target", review=review)
                official_url = self._browser_named_target_url(named_target, resolution_mode="search_followup")
                if not official_url:
                    return CommandResult(
                        status="failed",
                        intent=intent,
                        message="browser search results did not provide a follow-up destination",
                        result={"status": "failed", "query": query, "named_target": named_target},
                        review=review,
                    )
                if request.dry_run:
                    return CommandResult(
                        status="dry_run",
                        intent=intent,
                        selected_target=official_url,
                        result={
                            "status": "dry_run",
                            "uri": official_url,
                            "search_uri": search_uri,
                            "official_url": official_url,
                            "query": query,
                            "named_target": named_target,
                        },
                        review=review,
                    )
                search_opened = self.portal.open_uri(search_uri)
                record_browser_navigation(
                    uri=search_uri,
                    query=query,
                    adapter=str(search_opened.get("adapter")) if isinstance(search_opened, dict) and search_opened.get("adapter") is not None else None,
                    status=str(search_opened.get("status") or "opened") if isinstance(search_opened, dict) else "opened",
                )
                return self._browser_navigation_command_result(
                    request,
                    intent,
                    review,
                    uri=official_url,
                    query=query,
                    result_payload={
                        "query": query,
                        "named_target": named_target,
                        "search_uri": search_uri,
                        "official_url": official_url,
                        "search_status": str(search_opened.get("status") or "opened") if isinstance(search_opened, dict) else "opened",
                    },
                )
            uri = browser_semantic_uri(intent)
            if not uri:
                return CommandResult(status="rejected", intent=intent, message="missing browser target", review=review)
            result_payload = {}
            if "query" in intent.target:
                result_payload["query"] = intent.target.get("query")
            if "site" in intent.target:
                result_payload["site"] = intent.target.get("site")
            return self._browser_navigation_command_result(
                request,
                intent,
                review,
                uri=uri,
                query=str(intent.target.get("query")) if intent.target.get("query") is not None else None,
                site=str(intent.target.get("site")) if intent.target.get("site") is not None else None,
                result_payload=result_payload,
            )

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

    def _compatibility_intent_from_planning(self, planning) -> Intent:
        understanding = getattr(planning, "understanding", None)
        if understanding is not None and getattr(understanding, "provider_intent", None) is not None:
            return understanding.provider_intent
        plan = getattr(planning, "plan", None)
        if plan is not None and getattr(plan, "steps", ()):
            return self._intent_from_task_step(plan.steps[0])
        candidates = getattr(planning, "candidates", ())
        for candidate in candidates:
            if getattr(candidate, "steps", ()):
                return self._intent_from_task_step(candidate.steps[0])
        return Intent.unknown("planning did not yield a compatibility intent")

    def _step_safety_review_record(
        self,
        plan_id: str,
        step: TaskStep,
        pre_observation: LoopObservation | None = None,
        phase: str | None = None,
    ) -> tuple[object, StepReviewRecord]:
        del phase
        review = self.policy.review(self._intent_from_task_step(step))
        review = contextualize_step_review(
            policy=self.loop_policy,
            step_action=step.action,
            review=review,
            pre_observation=pre_observation,
        )
        review_id = self._make_step_safety_review_id(
            plan_id=plan_id,
            step_id=step.id,
            action=step.action,
            risk_level=review.risk_level,
            review_required=review.review_required,
            allowed=review.allowed,
            reason=review.reason,
            observation_fingerprint=_step_review_observation_fingerprint(pre_observation),
        )
        return review, StepReviewRecord(
            step_safety_review_id=review_id,
            step_id=step.id,
            action=step.action,
            risk_level=review.risk_level,
            review_required=review.review_required,
            allowed=review.allowed,
            reason=review.reason,
            effects=review.effects,
            reversible=review.reversible,
        )

    @staticmethod
    def _make_step_safety_review_id(
        *,
        plan_id: str,
        step_id: str,
        action: str,
        risk_level: str,
        review_required: bool,
        allowed: bool,
        reason: str,
        observation_fingerprint: str | None = None,
    ) -> str:
        digest = sha256(
            f"{plan_id}:{step_id}:{action}:{risk_level}:{review_required}:{allowed}:{reason}:{observation_fingerprint or ''}".encode("utf-8")
        ).hexdigest()[:12]
        return f"srev_{digest}"

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


def _step_review_observation_fingerprint(observation: LoopObservation | None) -> str | None:
    """Create a stable safety-review binding from meaningful observation data.

    A freshly sampled observation has a new id and timestamp even when the
    visible target has not changed. Binding approval to those volatile values
    made every resumed L2 step demand a second approval. The fingerprint keeps
    approval bound to the observed target/context while allowing a genuine
    context change to trigger a re-review.
    """

    if observation is None:
        return None

    volatile = {"attempt_id", "captured_at", "freshness_ts", "run_id"}

    def normalize(value):
        if isinstance(value, dict):
            return {
                str(key): normalize(item)
                for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
                if str(key) not in volatile
            }
        if isinstance(value, (list, tuple)):
            return [normalize(item) for item in value]
        return value

    payload = {
        "route_id": observation.route_id,
        "step_id": observation.step_id,
        "packages": normalize(observation.packages),
    }
    return sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")).hexdigest()[:16]


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
from .semantic_acceptance import SemanticAcceptanceProvider
from .goal_synthesizer import GoalSynthesisProvider
