from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .acceptance import AcceptanceEngine
from .acceptance_service import AcceptanceService
from .app_fixtures import AppSearchFixture
from .apps import AppRegistry
from .audit import AuditLog, default_audit_path
from .candidate_selection import CandidateSelectionProvider
from .clarification import ClarificationProvider
from .clipboard import ClipboardAdapter
from .command_service import CommandService
from .core.adapters.database import CoreDatabase
from .core.adapters.task_repository import SqliteTaskRepository
from .core.application import FoundationSliceService
from .core.composition import compose_foundation
from .durable_task_engine import DurableTaskEngine
from .durable_task_models import TaskEnginePolicy
from .execution_service import StepExecutionService
from .failure_classifier import FailureClassifier
from .goal_synthesizer import GoalSynthesisProvider
from .intent import IntentBroker, OpenAICompatibleIntentBroker
from .notifications import NotificationAdapter
from .observation_service import ObservationService
from .permissions import PermissionPolicy
from .planner import plan_turn
from .planning_service import PlanningService
from .portal import PortalAdapter
from .recovery_service import RecoveryService
from .replanner import EvidenceDrivenReplanner, Replanner
from .result_projection import AuditResultRecorder, CommandResultProjector
from .review_service import ReviewService
from .semantic_acceptance import SemanticAcceptanceProvider
from .task_application import TaskApplicationService
from .task_trace import TaskTraceStore
from .tool_protocol import ToolRegistry
from .tools.registry import build_tool_registry
from .understanding import OpenAICompatibleUnderstandingTransitionProvider, UnderstandingAnalysisProvider, UnderstandingTransitionProvider
from .verifiers import VerifierHarness, VerifierRegistry, default_verifier_registry
from .windows import WindowRegistry


@dataclass(frozen=True)
class RuntimeComponents:
    command_service: CommandService
    task_handler: TaskApplicationService
    task_engine: DurableTaskEngine
    task_repository: SqliteTaskRepository
    planning: PlanningService
    observation: ObservationService
    review_service: ReviewService
    execution: StepExecutionService
    acceptance: AcceptanceService
    recovery: RecoveryService
    projector: CommandResultProjector
    result_recorder: AuditResultRecorder
    audit: AuditLog
    trace_store: TaskTraceStore
    intent_broker: IntentBroker
    apps: AppRegistry
    windows: WindowRegistry
    portal: PortalAdapter
    notifications: NotificationAdapter
    clipboard: ClipboardAdapter
    policy: PermissionPolicy
    tool_registry: ToolRegistry
    verifier_registry: VerifierRegistry
    verifier_harness: VerifierHarness
    database: CoreDatabase
    foundation_slices: FoundationSliceService


def compose_runtime(
    *,
    intent_broker: IntentBroker | None = None,
    apps: AppRegistry | None = None,
    windows: WindowRegistry | None = None,
    portal: PortalAdapter | None = None,
    notifications: NotificationAdapter | None = None,
    clipboard: ClipboardAdapter | None = None,
    policy: PermissionPolicy | None = None,
    audit: AuditLog | None = None,
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
) -> RuntimeComponents:
    resolved_intent_broker = intent_broker or OpenAICompatibleIntentBroker()
    resolved_apps = apps or AppRegistry()
    resolved_windows = windows or WindowRegistry()
    resolved_portal = portal or PortalAdapter()
    resolved_notifications = notifications or NotificationAdapter()
    resolved_clipboard = clipboard or ClipboardAdapter()
    resolved_policy = policy or PermissionPolicy()
    resolved_audit = audit or AuditLog()
    resolved_database = database or CoreDatabase(_default_database_path())
    resolved_database.upgrade()
    task_repository = SqliteTaskRepository(resolved_database)
    resolved_trace_store = trace_store or TaskTraceStore()
    resolved_verifier_registry = verifier_registry or default_verifier_registry()
    resolved_verifier_harness = verifier_harness or VerifierHarness()
    planning = PlanningService(
        intent_broker=resolved_intent_broker,
        route_selection_provider=route_selection_provider,
        clarification_provider=clarification_provider,
        analysis_provider=understanding_analysis_provider,
        goal_synthesis_provider=goal_synthesis_provider,
        understanding_transition_provider=understanding_transition_provider or OpenAICompatibleUnderstandingTransitionProvider(),
        plan_turner=lambda *args, **kwargs: plan_turn(*args, **kwargs),
    )
    review_service = ReviewService(policy=resolved_policy)
    foundation = compose_foundation(
        database=resolved_database,
        portal=resolved_portal,
        notifications=resolved_notifications,
        capabilities=_capabilities_payload,
    )
    tool_registry = build_tool_registry(
        apps=resolved_apps,
        windows=resolved_windows,
        portal=resolved_portal,
        clipboard=resolved_clipboard,
        verifiers=resolved_verifier_harness,
        foundation_specs=foundation.tool_specs,
    )
    execution = StepExecutionService(
        tools=tool_registry,
        audit=resolved_audit,
        browser_site_catalog=browser_site_catalog or {},
        browser_search_catalog=browser_search_catalog or {},
        app_fixture_catalog=app_fixture_catalog or {},
    )
    observation = ObservationService(resolved_verifier_registry, resolved_verifier_harness)
    acceptance = AcceptanceService(
        acceptance_engine=AcceptanceEngine(provider=semantic_acceptance_provider),
        verifier_registry=resolved_verifier_registry,
        verifier_harness=resolved_verifier_harness,
    )
    recovery = RecoveryService(classifier=failure_classifier or FailureClassifier(), replanner=replanner or EvidenceDrivenReplanner())
    projector = CommandResultProjector()
    task_engine = DurableTaskEngine(
        repository=task_repository,
        planning=planning,
        observation=observation,
        reviews=review_service,
        execution=execution,
        acceptance=acceptance,
        recovery=recovery,
        policy=task_policy,
    )
    task_handler = TaskApplicationService(engine=task_engine, projector=projector)
    result_recorder = AuditResultRecorder(resolved_audit)
    command_service = CommandService(trace_store=resolved_trace_store, task_handler=task_handler, result_recorder=result_recorder)
    return RuntimeComponents(
        command_service=command_service,
        task_handler=task_handler,
        task_engine=task_engine,
        task_repository=task_repository,
        planning=planning,
        observation=observation,
        review_service=review_service,
        execution=execution,
        acceptance=acceptance,
        recovery=recovery,
        projector=projector,
        result_recorder=result_recorder,
        audit=resolved_audit,
        trace_store=resolved_trace_store,
        intent_broker=resolved_intent_broker,
        apps=resolved_apps,
        windows=resolved_windows,
        portal=resolved_portal,
        notifications=resolved_notifications,
        clipboard=resolved_clipboard,
        policy=resolved_policy,
        tool_registry=tool_registry,
        verifier_registry=resolved_verifier_registry,
        verifier_harness=resolved_verifier_harness,
        database=resolved_database,
        foundation_slices=foundation.slices,
    )


def _default_database_path() -> Path:
    return Path(default_audit_path()).with_name("reviews.sqlite3")


def _capabilities_payload() -> dict[str, object]:
    from .capabilities import capability_payload, executable_actions, permission_summary

    return {
        "capabilities": executable_actions(),
        "capability_details": capability_payload(),
        "permission_policy": permission_summary(),
    }
