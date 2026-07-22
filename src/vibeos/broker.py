from __future__ import annotations

from dataclasses import asdict
from typing import Protocol

from .app_fixtures import AppSearchFixture
from .apps import AppRegistry
from .audit import AuditLog
from .candidate_selection import CandidateSelectionProvider
from .clarification import ClarificationProvider
from .clipboard import ClipboardAdapter
from .capabilities import capability_payload, effect_policy_summary, executable_actions
from .core.adapters.database import CoreDatabase
from .core.domain import TaskRun, TaskStatus
from .durable_task_models import TaskEnginePolicy
from .failure_classifier import FailureClassifier
from .goal_synthesizer import GoalSynthesisProvider
from .intent import IntentBroker
from .models import CommandRequest, CommandResult
from .notifications import NotificationAdapter
from .permissions import EffectPolicy
from .portal import PortalAdapter
from .replanner import Replanner
from .runtime_composition import RuntimeComponents, compose_runtime
from .semantic_acceptance import SemanticAcceptanceProvider
from .task_models import TaskObservation, TaskPlan, TaskPlanReviewResult, TaskStep
from .task_trace import TaskTraceStore
from .understanding import UnderstandingAnalysisProvider, UnderstandingTransitionProvider
from .verifiers import VerifierHarness, VerifierRegistry
from .windows import WindowRegistry


class _LegacyDatabaseSource(Protocol):
    """Narrow constructor compatibility; no legacy review state is consumed."""

    database: CoreDatabase


class CapabilityBroker:
    """Construction facade for the one durable production task path."""

    def __init__(
        self,
        intent_broker: IntentBroker | None = None,
        apps: AppRegistry | None = None,
        windows: WindowRegistry | None = None,
        portal: PortalAdapter | None = None,
        notifications: NotificationAdapter | None = None,
        clipboard: ClipboardAdapter | None = None,
        policy: EffectPolicy | None = None,
        audit: AuditLog | None = None,
        reviews: _LegacyDatabaseSource | None = None,
        trace_store: TaskTraceStore | None = None,
        clarification_provider: ClarificationProvider | None = None,
        understanding_analysis_provider: UnderstandingAnalysisProvider | None = None,
        goal_synthesis_provider: GoalSynthesisProvider | None = None,
        route_selection_provider: CandidateSelectionProvider | None = None,
        understanding_transition_provider: UnderstandingTransitionProvider | None = None,
        semantic_acceptance_provider: SemanticAcceptanceProvider | None = None,
        verifier_registry: VerifierRegistry | None = None,
        verifier_harness: VerifierHarness | None = None,
        failure_classifier: FailureClassifier | None = None,
        replanner: Replanner | None = None,
        task_policy: TaskEnginePolicy | None = None,
        browser_site_catalog: dict[str, str] | None = None,
        browser_search_catalog: dict[str, dict[str, object]] | None = None,
        app_fixture_catalog: dict[str, AppSearchFixture] | None = None,
        database: CoreDatabase | None = None,
    ) -> None:
        resolved_database = database or (reviews.database if reviews is not None else None)
        runtime = compose_runtime(
            intent_broker=intent_broker,
            apps=apps,
            windows=windows,
            portal=portal,
            notifications=notifications,
            clipboard=clipboard,
            policy=policy,
            audit=audit,
            trace_store=trace_store,
            clarification_provider=clarification_provider,
            understanding_analysis_provider=understanding_analysis_provider,
            goal_synthesis_provider=goal_synthesis_provider,
            route_selection_provider=route_selection_provider,
            understanding_transition_provider=understanding_transition_provider,
            semantic_acceptance_provider=semantic_acceptance_provider,
            verifier_registry=verifier_registry,
            verifier_harness=verifier_harness,
            failure_classifier=failure_classifier,
            replanner=replanner,
            task_policy=task_policy,
            browser_site_catalog=browser_site_catalog,
            browser_search_catalog=browser_search_catalog,
            app_fixture_catalog=app_fixture_catalog,
            database=resolved_database,
        )
        self._runtime: RuntimeComponents = runtime
        self.command_service = runtime.command_service
        self.audit = runtime.audit
        self.trace_store = runtime.trace_store
        self.intent_broker = runtime.intent_broker
        self.apps = runtime.apps
        self.windows = runtime.windows
        self.portal = runtime.portal
        self.notifications = runtime.notifications
        self.clipboard = runtime.clipboard
        self.policy = runtime.policy
        self.tool_registry = runtime.tool_registry
        self.verifier_registry = runtime.verifier_registry
        self.verifier_harness = runtime.verifier_harness
        self.database = runtime.database
        self.task_repository = runtime.task_repository
        self.task_engine = runtime.task_engine
        self.foundation_slices = runtime.foundation_slices
        self.planning_service = runtime.planning
        self.review_service = runtime.review_service
        self.step_execution_service = runtime.execution
        self.acceptance_service = runtime.acceptance
        self.task_handler = runtime.task_handler

    def capabilities(self) -> dict[str, object]:
        return {
            "schema_version": "v2",
            "capabilities": executable_actions(),
            "capability_details": capability_payload(),
            "effect_policy": effect_policy_summary(),
        }

    def pending_reviews(self) -> list[dict[str, object]]:
        return [self._pending_payload(state) for state in self.task_handler.pending_interactions()]

    def handle(self, request: CommandRequest) -> CommandResult:
        return self.command_service.handle(request)

    def review_task_plan(self, plan: TaskPlan, stored_payload: dict[str, object] | None = None) -> TaskPlanReviewResult:
        return self.review_service.review_task_plan(plan, stored_payload)

    def review_task_step(
        self,
        plan: TaskPlan,
        step: TaskStep,
        pre_observation: TaskObservation | None = None,
    ):
        return self.review_service.review_step(plan, step, pre_observation)

    def list_apps(self, *, transport: str | None = None) -> list[dict[str, object]]:
        return _collection_from_result(self.handle(CommandRequest("list apps", transport=transport)), "apps")

    def list_windows(self, *, transport: str | None = None) -> list[dict[str, object]]:
        return _collection_from_result(self.handle(CommandRequest("list windows", transport=transport)), "windows")

    def approve_review(self, review_id: str, dry_run: bool = False, transport: str | None = None) -> CommandResult:
        return self.command_service.handle(CommandRequest("", dry_run=dry_run, approve=True, review_id=review_id, transport=transport))

    def provide_review_input(
        self,
        review_id: str,
        supplemental_input: str,
        dry_run: bool = False,
        transport: str | None = None,
    ) -> CommandResult:
        request = CommandRequest("", dry_run=dry_run, review_id=review_id, supplemental_input=supplemental_input, transport=transport)
        return self.command_service.handle(request)

    def reject_review(self, review_id: str, transport: str | None = None) -> CommandResult:
        return self.command_service.reject(review_id, transport=transport)

    def tasks(self, *, status: str | None = None, limit: int = 100) -> list[dict[str, object]]:
        statuses = (TaskStatus(status),) if status else ()
        return [self._task_payload(item) for item in self.task_handler.list_tasks(statuses=statuses, limit=limit)]

    def task(self, task_id: str) -> dict[str, object] | None:
        state = self.task_handler.show_task(task_id)
        return self._task_payload(state) if state is not None else None

    def control_task(
        self,
        task_id: str,
        operation: str,
        *,
        expected_revision: int,
        owner: str | None = None,
        reason: str = "",
    ) -> dict[str, object]:
        return self._task_payload(self.task_handler.control_task(task_id, operation, expected_revision=expected_revision, owner=owner, reason=reason))

    def _task_payload(self, state: TaskRun) -> dict[str, object]:
        import json

        payload: dict[str, object] = asdict(state)
        steps = self.task_repository.steps(state.task_id, state.active_plan_revision_id)
        receipts: list[dict[str, object]] = []
        for receipt in self.task_repository.receipts(state.task_id):
            projected: dict[str, object] = asdict(receipt)
            try:
                projected["result"] = json.loads(receipt.result_json)
            except json.JSONDecodeError:
                projected["result"] = {"error": "invalid_persisted_receipt"}
            projected.pop("result_json", None)
            receipts.append(projected)
        payload["receipts"] = receipts
        payload["progress"] = {
            "completed_steps": len(state.completed_step_ids),
            "total_steps": len(steps),
            "current_step_id": state.current_step_id,
            "next_wake_at": state.next_wake_at,
            "wait_event_key": state.wait_event_key,
            "deadline_at": state.deadline_at,
            "pending_reason": state.pending_reason,
        }
        return payload

    def _pending_payload(self, state: TaskRun) -> dict[str, object]:
        contract = self.task_repository.contract(state.task_id)
        planning = None
        if state.active_plan_revision_id:
            try:
                planning = self.task_engine.planning.from_snapshot(
                    utterance=contract.goal if contract else "",
                    payload=_plan_payload(self.task_repository.plan_payload(state.active_plan_revision_id)),
                )
            except (KeyError, TypeError, ValueError):
                planning = None
        plan = planning.plan if planning is not None else None
        step = next((item for item in plan.steps if item.id == state.current_step_id), None) if plan is not None else None
        review = self.review_service.review_step(plan, step, None)[0] if step is not None and plan is not None else None
        return {
            "schema_version": "v2",
            "review_id": state.pending_interaction_id,
            "task_id": state.task_id,
            "revision": state.revision,
            "review_kind": "user_input" if state.status is TaskStatus.AWAITING_CLARIFICATION else "action",
            "utterance": contract.goal if contract else "",
            "intent": asdict(_intent(step)) if step is not None else {"action": "unknown", "target": {}, "reason": state.pending_reason or ""},
            "review": asdict(review) if review is not None else None,
            "plan_id": plan.plan_id if plan is not None else None,
            "step_id": state.current_step_id,
            "pending_reason": state.pending_reason,
            "status": "pending",
        }


def _plan_payload(raw: str | None) -> dict[str, object]:
    import json

    try:
        payload = json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _intent(step: TaskStep):
    from .review_service import intent_from_task_step

    return intent_from_task_step(step)


def _collection_from_result(result: CommandResult, key: str) -> list[dict[str, object]]:
    payload = result.result
    step_results = payload.get("step_results") if isinstance(payload, dict) else None
    if not isinstance(step_results, (list, tuple)):
        return []
    for step_result in reversed(step_results):
        if not isinstance(step_result, dict):
            continue
        output = step_result.get("result")
        items = output.get(key) if isinstance(output, dict) else None
        if isinstance(items, list):
            return [dict(item) for item in items if isinstance(item, dict)]
    return []
