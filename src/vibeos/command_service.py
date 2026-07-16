from __future__ import annotations

from contextlib import nullcontext
from dataclasses import replace
from typing import Protocol

from .models import CommandRequest, CommandResult, Intent
from .core.adapters.task_repository import TaskRepositoryError
from .run_context import RunContext
from .task_trace import TaskTraceStore, bind_trace_session, current_trace_session, record_trace_event


class TaskCommandHandler(Protocol):
    """Typed task application boundary used by command ingress."""

    def make_run_id(self, seed: str) -> str: ...

    def start(self, request: CommandRequest, context: RunContext) -> CommandResult: ...

    def approve(self, review_id: str, request: CommandRequest, context: RunContext) -> CommandResult: ...

    def provide_input(
        self,
        review_id: str,
        supplemental_input: str,
        request: CommandRequest,
        context: RunContext,
    ) -> CommandResult: ...

    def reject(self, review_id: str, request: CommandRequest, context: RunContext) -> CommandResult: ...


class CommandResultRecorder(Protocol):
    """Owns final public-result audit metadata and persistence."""

    def record(self, request: CommandRequest, result: CommandResult, trace_run_id: str) -> CommandResult: ...

    def metadata(self, result: CommandResult) -> dict[str, str | None]: ...


class CommandService:
    """Transport-neutral command ingress, trace lifecycle, and result handoff."""

    def __init__(
        self,
        *,
        trace_store: TaskTraceStore,
        task_handler: TaskCommandHandler,
        result_recorder: CommandResultRecorder,
    ) -> None:
        self.trace_store = trace_store
        self.task_handler = task_handler
        self.result_recorder = result_recorder

    def handle(self, request: CommandRequest) -> CommandResult:
        trace_session = current_trace_session()
        created_trace = trace_session is None
        if trace_session is None:
            seed = request.utterance or request.review_id or "command"
            trace_session = self.trace_store.start_run(
                run_id=self.task_handler.make_run_id(seed),
                command_name="approve" if request.review_id else "ask",
                utterance=request.utterance,
                mode=request.mode,
                transport=request.transport,
                dry_run=request.dry_run,
                debug=request.debug,
                review_id=request.review_id,
            )
        scope = bind_trace_session(trace_session) if created_trace else nullcontext(trace_session)
        with scope:
            record_trace_event(
                phase="ingress",
                event_type="request_received",
                status="ok",
                actor="command_service",
                review_id=request.review_id,
                data={"mode": request.mode, "dry_run": request.dry_run, "approve": request.approve, "transport": request.transport, "debug": request.debug},
            )
            try:
                context = RunContext.from_request(request, run_id=trace_session.run_id, goal_id="goal_pending")
                result = self._dispatch(request, context)
            except TaskRepositoryError:
                result = _with_transport(
                    CommandResult(
                        status="failed",
                        intent=Intent.unknown("task persistence is unavailable"),
                        result={"error_code": "task_persistence_unavailable"},
                        message="task persistence is unavailable; unresolved proposals require reconciliation before replay",
                        execution_status="not_started",
                        acceptance_status="skipped",
                        overall_status="blocked",
                    ),
                    request.transport,
                )
            final_result = self.result_recorder.record(request, result, trace_session.run_id)
            metadata = self.result_recorder.metadata(final_result)
            record_trace_event(
                phase="completion",
                event_type="command_result_emitted",
                status=final_result.status,
                actor="command_service",
                goal_id=metadata.get("goal_id"),
                plan_id=metadata.get("plan_id"),
                review_id=final_result.review_id,
                selected_strategy_id=metadata.get("selected_strategy_id"),
                data={
                    "overall_status": final_result.overall_status,
                    "execution_status": final_result.execution_status,
                    "acceptance_status": final_result.acceptance_status,
                    "message": final_result.message,
                },
            )
            if created_trace:
                trace_session.finalize(
                    status=final_result.status,
                    goal_id=metadata.get("goal_id"),
                    review_id=final_result.review_id,
                    message=final_result.message,
                    overall_status=final_result.overall_status,
                    selected_strategy_id=metadata.get("selected_strategy_id"),
                    selected_target=final_result.selected_target,
                    plan_id=metadata.get("plan_id"),
                )
            return final_result

    def reject(self, review_id: str, *, transport: str | None = None) -> CommandResult:
        request = CommandRequest("", review_id=review_id, transport=transport)
        trace_session = self.trace_store.start_run(
            run_id=self.task_handler.make_run_id(review_id),
            command_name="reject",
            utterance="",
            mode=request.mode,
            transport=transport,
            dry_run=False,
            debug=False,
            review_id=review_id,
        )
        with bind_trace_session(trace_session):
            context = RunContext.from_request(request, run_id=trace_session.run_id, goal_id="goal_pending")
            result = self.task_handler.reject(review_id, request, context)
            final_result = self.result_recorder.record(request, result, trace_session.run_id)
            metadata = self.result_recorder.metadata(final_result)
            trace_session.finalize(
                status=final_result.status,
                goal_id=metadata.get("goal_id"),
                review_id=review_id,
                message=final_result.message,
                overall_status=final_result.overall_status,
                selected_strategy_id=metadata.get("selected_strategy_id"),
                selected_target=final_result.selected_target,
                plan_id=metadata.get("plan_id"),
            )
            return final_result

    def _dispatch(self, request: CommandRequest, context: RunContext) -> CommandResult:
        if request.review_id and request.supplemental_input is not None and request.approve:
            return _with_transport(
                CommandResult(
                    status="rejected",
                    intent=Intent.unknown("review resume cannot combine approval and supplemental input"),
                    review_id=request.review_id,
                    message="supplemental input resumes a user-input review; do not combine it with explicit approval",
                ),
                request.transport,
            )
        if request.review_id and request.supplemental_input is not None:
            return self.task_handler.provide_input(request.review_id, request.supplemental_input, request, context)
        if request.review_id:
            return self.task_handler.approve(request.review_id, request, context)
        if request.approve:
            return _with_transport(
                CommandResult(
                    status="rejected",
                    intent=Intent.unknown("approval requires a stored review id"),
                    message="L2 approval must use a stored review id; run without approval first, then `vibe approve <review_id>`",
                ),
                request.transport,
            )
        return _with_transport(self.task_handler.start(request, context), request.transport)


def _with_transport(result: CommandResult, transport: str | None) -> CommandResult:
    return result if result.transport == transport else replace(result, transport=transport)
