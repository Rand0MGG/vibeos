from __future__ import annotations

from contextlib import nullcontext
from dataclasses import dataclass, replace
from typing import Any, Callable

from .models import CommandRequest, CommandResult, Intent
from .task_trace import TaskTraceStore, bind_trace_session, current_trace_session, record_trace_event


@dataclass(frozen=True)
class CommandPorts:
    make_run_id: Callable[[str], str]
    plan: Callable[[CommandRequest], CommandResult]
    approve_review: Callable[[str, bool, str | None], CommandResult]
    provide_review_input: Callable[[str, str, bool, str | None], CommandResult]
    record_result: Callable[[CommandRequest, CommandResult, str], CommandResult]
    result_metadata: Callable[[CommandResult], dict[str, Any]]


class CommandService:
    """Transport-neutral command ingress, trace lifecycle, and result handoff."""

    def __init__(self, *, trace_store: TaskTraceStore, ports: CommandPorts) -> None:
        self.trace_store = trace_store
        self.ports = ports

    def handle(self, request: CommandRequest) -> CommandResult:
        trace_session = current_trace_session()
        created_trace = trace_session is None
        if trace_session is None:
            seed = request.utterance or request.review_id or "command"
            trace_session = self.trace_store.start_run(
                run_id=self.ports.make_run_id(seed),
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
            result = self._dispatch(request)
            final_result = self.ports.record_result(request, result, trace_session.run_id)
            metadata = self.ports.result_metadata(final_result)
            record_trace_event(
                phase="completion",
                event_type="command_result_emitted",
                status=final_result.status,
                actor="command_service",
                goal_id=metadata.get("goal_id"),
                plan_id=metadata.get("plan_id"),
                review_id=final_result.review_id,
                selected_strategy_id=metadata.get("selected_strategy_id"),
                data={"overall_status": final_result.overall_status, "execution_status": final_result.execution_status, "acceptance_status": final_result.acceptance_status, "message": final_result.message},
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

    def _dispatch(self, request: CommandRequest) -> CommandResult:
        if request.review_id and request.supplemental_input is not None and request.approve:
            return _with_transport(
                CommandResult(status="rejected", intent=Intent.unknown("review resume cannot combine approval and supplemental input"), review_id=request.review_id, message="supplemental input resumes a user-input review; do not combine it with explicit approval"),
                request.transport,
            )
        if request.review_id and request.supplemental_input is not None:
            return self.ports.provide_review_input(request.review_id, request.supplemental_input, request.dry_run, request.transport)
        if request.review_id:
            return self.ports.approve_review(request.review_id, request.dry_run, request.transport)
        if request.approve:
            return _with_transport(CommandResult(status="rejected", intent=Intent.unknown("approval requires a stored review id"), message="L2 approval must use a stored review id; run without approval first, then `vibe approve <review_id>`"), request.transport)
        return _with_transport(self.ports.plan(request), request.transport)


def _with_transport(result: CommandResult, transport: str | None) -> CommandResult:
    return result if result.transport == transport else replace(result, transport=transport)
