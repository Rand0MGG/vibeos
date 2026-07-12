from __future__ import annotations

from dataclasses import replace
from hashlib import sha256

from .acceptance_service import AcceptanceService
from .browser_state import browser_attempt_scope
from .execution_graph import execute_plan_graph
from .execution_service import StepExecutionService
from .goal_loop import GoalLoop
from .loop_models import LoopPolicy
from .goal_ports import GoalLoopPorts
from .models import CommandRequest, CommandResult, Intent, ReviewRequest, utc_now_iso
from .observation_service import ObservationService
from .planning_service import PlanningService
from .recovery_service import RecoveryService
from .result_projection import CommandResultProjector
from .review_resume_service import ResumedGoalLoop, ReviewResumeError, ReviewResumeService
from .review_service import ReviewService
from .reviews import ReviewStore, review_execution_binding
from .run_context import RunContext
from .task_models import PlanExecutionResult, StepExecutionResult, TaskPlan, TaskStep
from .task_validation import validate_plan


class TaskApplicationService:
    """The transport-neutral application boundary for all task lifecycles."""

    def __init__(
        self,
        *,
        planning: PlanningService,
        observation: ObservationService,
        reviews: ReviewService,
        review_store: ReviewStore,
        execution: StepExecutionService,
        acceptance: AcceptanceService,
        recovery: RecoveryService,
        projector: CommandResultProjector,
        loop_policy: LoopPolicy,
    ) -> None:
        self.planning = planning
        self.observation = observation
        self.reviews = reviews
        self.review_store = review_store
        self.execution = execution
        self.acceptance = acceptance
        self.recovery = recovery
        self.projector = projector
        self.loop_policy = loop_policy
        self.review_resume = ReviewResumeService(
            planning=self.planning,
            goal_loop_factory=self.make_goal_loop,
        )

    def make_run_id(self, seed: str) -> str:
        digest = sha256(f"run:{utc_now_iso()}:{seed}:{len(seed)}".encode("utf-8")).hexdigest()[:12]
        return f"run_{digest}"

    def make_goal_loop(self) -> GoalLoop:
        return GoalLoop(
            ports=GoalLoopPorts(
                planning=self.planning,
                observation=self.observation,
                review=self.reviews,
                execution=self.execution,
                acceptance=self.acceptance,
                recovery=self.recovery,
                policy=self.loop_policy,
            )
        )

    def start(self, request: CommandRequest, context: RunContext) -> CommandResult:
        planning = self.planning.plan(request)
        goal_spec = planning.goal_synthesis.goal_spec if planning.goal_synthesis is not None else None
        goal_id = goal_spec.goal_id if goal_spec is not None else "goal_unresolved"
        result = self.make_goal_loop().run(
            request=request,
            planning=planning,
            run_id=context.run_id,
            goal_id=goal_id,
        )
        return self.projector.project(
            request=request,
            planning=planning,
            run_id=context.run_id,
            goal_id=goal_id,
            loop_result=result,
        )

    def approve(self, review_id: str, request: CommandRequest, context: RunContext) -> CommandResult:
        if request.dry_run:
            review_request = self.review_store.get(review_id)
            if review_request is None:
                return _review_not_found(review_id, request.transport)
            if review_request.status != "pending":
                return _review_wrong_status(review_request, request.transport)
            return self._preview_approval(review_request, request, context)

        pending = self.review_store.get(review_id)
        if pending is not None and pending.review_kind == "user_input":
            return _user_input_required(pending, request.transport)
        review_request = self.review_store.approve(review_id)
        if review_request is None:
            return _review_not_found(review_id, request.transport)
        if review_request.status != "approved":
            return _review_wrong_status(review_request, request.transport)
        if review_request.review_kind != "user_input" and not self.review_store.claim_execution(
            review_id,
            expected_binding=review_execution_binding(review_request),
        ):
            return CommandResult(
                status="rejected",
                intent=review_request.intent,
                review=review_request.review,
                review_id=review_id,
                message="review request is already being executed or has been consumed",
                transport=request.transport,
            )
        result = self._resume_approval(review_request, request, context)
        if review_request.review_kind in {"plan", "loop"}:
            if result.status in {"executed", "review_required", "ambiguous"}:
                self.review_store.complete_execution(review_id)
            else:
                self.review_store.release_execution(review_id)
        return result

    def provide_input(
        self,
        review_id: str,
        supplemental_input: str,
        request: CommandRequest,
        context: RunContext,
    ) -> CommandResult:
        if request.dry_run:
            review_request = self.review_store.get(review_id)
            if review_request is None:
                return _review_not_found(review_id, request.transport)
            if review_request.review_kind != "user_input":
                return _wrong_input_kind(review_request, request.transport)
            preview = replace(review_request, supplemental_input=supplemental_input)
            return self._resume_user_input(preview, request, context)

        review_request = self.review_store.provide_input(review_id, supplemental_input)
        if review_request is None:
            return _review_not_found(review_id, request.transport)
        if review_request.status != "provided":
            return _review_wrong_status(review_request, request.transport)
        if review_request.review_kind != "user_input":
            return _wrong_input_kind(review_request, request.transport)
        result = self._resume_user_input(review_request, request, context)
        if result.status in {"executed", "review_required", "ambiguous"}:
            self.review_store.consume_input(review_id)
        return result

    def reject(self, review_id: str, request: CommandRequest, context: RunContext) -> CommandResult:
        del context
        review_request = self.review_store.reject(review_id)
        if review_request is None:
            return _review_not_found(review_id, request.transport)
        if review_request.status != "rejected":
            return _review_wrong_status(review_request, request.transport)
        return CommandResult(
            status="rejected",
            intent=review_request.intent,
            result={"review_id": review_id, "review_status": "rejected"},
            review=review_request.review,
            review_id=review_id,
            message="review request rejected by user",
            transport=request.transport,
        )

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
        _ensure_task_plan(plan)
        if not validate_plan(plan).ok:
            return PlanExecutionResult(plan_id=plan.plan_id, status="rejected", error="task plan failed validation")
        scoped_run_id = run_id or self.make_run_id(plan.utterance)
        scoped_attempt_id = attempt_id or _make_attempt_id(scoped_run_id, 1, plan.selected_route_id or "standalone")
        request = CommandRequest(
            plan.utterance,
            dry_run=dry_run,
            approve=review_id is not None,
            review_id=review_id,
            transport=transport,
        )

        def execute_step(step: TaskStep) -> StepExecutionResult:
            return self.execution.execute_step(
                context=RunContext.from_request(request, run_id=scoped_run_id, goal_id=f"goal_{plan.plan_id}"),
                plan=plan,
                step=step,
                request=request,
                attempt_id=scoped_attempt_id,
            )

        with browser_attempt_scope(run_id=scoped_run_id, attempt_id=scoped_attempt_id, route_id=plan.selected_route_id):
            execution = execute_plan_graph(plan, execute_step)
        return self.acceptance.assess_compatibility(
            plan,
            execution.step_results,
            request=request,
            error=execution.error,
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
        _ensure_task_plan(plan)
        request = CommandRequest(plan.utterance, dry_run=dry_run, approve=review_id is not None, review_id=review_id, transport=transport)
        scoped_attempt_id = attempt_id or _make_attempt_id(self.make_run_id(plan.utterance), 1, step.id)
        return self.execution.execute_step(
            context=RunContext.from_request(request, run_id=f"run_{scoped_attempt_id}", goal_id=f"goal_{plan.plan_id}"),
            plan=plan,
            step=step,
            request=request,
            attempt_id=scoped_attempt_id,
        )

    def _preview_approval(self, review_request: ReviewRequest, request: CommandRequest, context: RunContext) -> CommandResult:
        if review_request.review_kind == "user_input":
            return _user_input_required(review_request, request.transport)
        return self._resume_approval(review_request, request, context)

    def _resume_approval(self, review_request: ReviewRequest, request: CommandRequest, context: RunContext) -> CommandResult:
        if review_request.review_kind == "plan" and review_request.plan_payload:
            try:
                resumed = self.review_resume.resume_legacy_plan_review(
                    review_request,
                    dry_run=request.dry_run,
                    transport=request.transport,
                )
            except ReviewResumeError as exc:
                return self.projector.review_resume_error(
                    review_request,
                    code=exc.code,
                    message=str(exc),
                    transport=request.transport,
                    legacy=True,
                )
            return self._project_resumed(resumed)
        if review_request.review_kind == "loop":
            try:
                resumed = self.review_resume.resume_execution_review(
                    review_request,
                    dry_run=request.dry_run,
                    transport=request.transport,
                )
            except ReviewResumeError as exc:
                return self.projector.review_resume_error(
                    review_request,
                    code=exc.code,
                    message=str(exc),
                    transport=request.transport,
                )
            return self._project_resumed(resumed)
        return _unsupported_review_kind(review_request, request.transport)

    def _resume_user_input(self, review_request: ReviewRequest, request: CommandRequest, context: RunContext) -> CommandResult:
        try:
            resumed = self.review_resume.resume_user_input_review(
                review_request,
                dry_run=request.dry_run,
                transport=request.transport,
            )
        except ReviewResumeError as exc:
            status = "rejected" if exc.code == "supplemental_input_required" else "failed"
            result = self.projector.review_resume_error(
                review_request,
                code=exc.code,
                message=str(exc),
                transport=request.transport,
            )
            return replace(result, status=status)
        return self._project_resumed(resumed)

    def _project_resumed(self, resumed: ResumedGoalLoop) -> CommandResult:
        return self.projector.project(
            request=resumed.request,
            planning=resumed.planning,
            run_id=resumed.run_id,
            goal_id=resumed.goal_id,
            loop_result=resumed.loop_result,
        )


def _ensure_task_plan(plan: TaskPlan) -> None:
    if not isinstance(plan, TaskPlan):
        raise TypeError("executors only accept validated TaskPlan objects, never raw utterances or arbitrary payloads")


def _make_attempt_id(run_id: str, attempt_index: int, route_id: str) -> str:
    digest = sha256(f"{run_id}:{attempt_index}:{route_id}".encode("utf-8")).hexdigest()[:10]
    return f"attempt_{digest}"


def _review_not_found(review_id: str, transport: str | None) -> CommandResult:
    return CommandResult(
        status="rejected",
        intent=Intent.unknown("review request not found", {"review_id": review_id}),
        review_id=review_id,
        message="review request not found",
        transport=transport,
    )


def _review_wrong_status(review_request: ReviewRequest, transport: str | None) -> CommandResult:
    return CommandResult(
        status="rejected",
        intent=review_request.intent,
        review=review_request.review,
        review_id=review_request.review_id,
        message=f"review request is not pending; current status is {review_request.status}",
        transport=transport,
    )


def _user_input_required(review_request: ReviewRequest, transport: str | None) -> CommandResult:
    return CommandResult(
        status="rejected",
        intent=review_request.intent,
        review=review_request.review,
        review_id=review_request.review_id,
        message="use supplemental input to resume this review",
        transport=transport,
    )


def _wrong_input_kind(review_request: ReviewRequest, transport: str | None) -> CommandResult:
    return CommandResult(
        status="rejected",
        intent=review_request.intent,
        review=review_request.review,
        review_id=review_request.review_id,
        message="supplemental input is only valid for user-input reviews",
        transport=transport,
    )


def _unsupported_review_kind(review_request: ReviewRequest, transport: str | None) -> CommandResult:
    return CommandResult(
        status="failed",
        intent=review_request.intent,
        result={
            "error_code": "unsupported_review_kind",
            "review_kind": review_request.review_kind,
            "review_id": review_request.review_id,
        },
        review=review_request.review,
        review_id=review_request.review_id,
        message="stored review kind is not executable through the current runtime",
        execution_status="not_started",
        acceptance_status="skipped",
        overall_status="failed",
        transport=transport,
    )
