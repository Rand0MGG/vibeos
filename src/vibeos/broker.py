from __future__ import annotations

from .app_fixtures import AppSearchFixture
from .apps import AppRegistry
from .audit import AuditLog
from .candidate_selection import CandidateSelectionProvider
from .clarification import ClarificationProvider
from .clipboard import ClipboardAdapter
from .capabilities import capability_payload, executable_actions, permission_summary
from .failure_classifier import FailureClassifier
from .goal_synthesizer import GoalSynthesisProvider
from .intent import IntentBroker
from .loop_models import LoopObservation, LoopPolicy
from .models import CommandRequest, CommandResult
from .notifications import NotificationAdapter
from .permissions import PermissionPolicy
from .portal import PortalAdapter
from .replanner import Replanner
from .reviews import ReviewStore, review_to_payload
from .runtime_composition import RuntimeComponents, compose_runtime
from .semantic_acceptance import SemanticAcceptanceProvider
from .strategy import StrategySelectionProvider
from .task_models import PlanExecutionResult, StepExecutionResult, TaskPlan, TaskPlanReviewResult, TaskStep
from .task_trace import TaskTraceStore
from .understanding import UnderstandingAnalysisProvider, UnderstandingTransitionProvider
from .verifiers import VerifierHarness, VerifierRegistry
from .windows import WindowRegistry


class CapabilityBroker:
    """Backward-compatible construction facade for the composed task runtime.

    The broker intentionally contains no planning, review-resume, execution,
    acceptance, result-projection, or adapter logic. Its compatibility methods
    delegate to the independently testable application services assembled by
    :func:`compose_runtime`.
    """

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
        self._runtime: RuntimeComponents = compose_runtime(
            intent_broker=intent_broker,
            apps=apps,
            windows=windows,
            portal=portal,
            notifications=notifications,
            clipboard=clipboard,
            policy=policy,
            audit=audit,
            reviews=reviews,
            trace_store=trace_store,
            clarification_provider=clarification_provider,
            understanding_analysis_provider=understanding_analysis_provider,
            goal_synthesis_provider=goal_synthesis_provider,
            route_selection_provider=route_selection_provider,
            strategy_selection_provider=strategy_selection_provider,
            understanding_transition_provider=understanding_transition_provider,
            semantic_acceptance_provider=semantic_acceptance_provider,
            verifier_registry=verifier_registry,
            verifier_harness=verifier_harness,
            failure_classifier=failure_classifier,
            replanner=replanner,
            loop_policy=loop_policy,
            browser_site_catalog=browser_site_catalog,
            browser_search_catalog=browser_search_catalog,
            app_fixture_catalog=app_fixture_catalog,
        )
        # Public component references preserve existing constructor-level
        # inspection and test replacement without making Broker their owner.
        self.command_service = self._runtime.command_service
        self.reviews = self._runtime.reviews
        self.audit = self._runtime.audit
        self.trace_store = self._runtime.trace_store
        self.intent_broker = self._runtime.intent_broker
        self.apps = self._runtime.apps
        self.windows = self._runtime.windows
        self.portal = self._runtime.portal
        self.notifications = self._runtime.notifications
        self.clipboard = self._runtime.clipboard
        self.policy = self._runtime.policy
        self.tool_registry = self._runtime.tool_registry
        self.verifier_registry = self._runtime.verifier_registry
        self.verifier_harness = self._runtime.verifier_harness
        self.loop_policy = self._runtime.loop_policy
        self.database = self._runtime.database
        self.foundation_slices = self._runtime.foundation_slices
        self.planning_service = self._runtime.planning
        self.review_service = self._runtime.review_service
        self.step_execution_service = self._runtime.execution
        self.acceptance_service = self._runtime.acceptance
        self.task_handler = self._runtime.task_handler

    def capabilities(self) -> dict[str, object]:
        return {
            "capabilities": executable_actions(),
            "capability_details": capability_payload(),
            "permission_policy": permission_summary(),
        }

    def pending_reviews(self) -> list[dict[str, object]]:
        return [review_to_payload(request) for request in self.reviews.list_pending()]

    def handle(self, request: CommandRequest) -> CommandResult:
        return self.command_service.handle(request)

    # Narrow compatibility entry points for pre-existing direct callers.
    # Their implementations remain owned by the application services.
    def review_task_plan(self, plan: TaskPlan, stored_payload: dict[str, object] | None = None) -> TaskPlanReviewResult:
        return self.review_service.review_task_plan(plan, stored_payload)

    def review_task_step(
        self,
        plan: TaskPlan,
        step: TaskStep,
        pre_observation: LoopObservation | None = None,
    ):
        return self.review_service.review_step(plan, step, pre_observation)

    def execute_task_plan(
        self,
        plan: TaskPlan,
        *,
        dry_run: bool = False,
        transport: str | None = None,
        review_id: str | None = None,
        run_id: str | None = None,
        attempt_id: str | None = None,
        understanding_id: str | None = None,
        candidate_set_id: str | None = None,
        route_decision_id: str | None = None,
    ) -> PlanExecutionResult:
        return self.task_handler.execute_task_plan(
            plan,
            dry_run=dry_run,
            transport=transport,
            review_id=review_id,
            run_id=run_id,
            attempt_id=attempt_id,
            understanding_id=understanding_id,
            candidate_set_id=candidate_set_id,
            route_decision_id=route_decision_id,
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
        return self.task_handler.execute_task_step(
            plan,
            step,
            dry_run=dry_run,
            transport=transport,
            review_id=review_id,
            attempt_id=attempt_id,
        )

    def approve_review(self, review_id: str, dry_run: bool = False, transport: str | None = None) -> CommandResult:
        return self.command_service.handle(CommandRequest("", dry_run=dry_run, approve=True, review_id=review_id, transport=transport))

    def provide_review_input(
        self,
        review_id: str,
        supplemental_input: str,
        dry_run: bool = False,
        transport: str | None = None,
    ) -> CommandResult:
        return self.command_service.handle(
            CommandRequest(
                "",
                dry_run=dry_run,
                review_id=review_id,
                supplemental_input=supplemental_input,
                transport=transport,
            )
        )

    def reject_review(self, review_id: str, transport: str | None = None) -> CommandResult:
        return self.command_service.reject(review_id, transport=transport)
