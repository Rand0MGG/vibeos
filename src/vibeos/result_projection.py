from __future__ import annotations

from dataclasses import asdict, replace

from .audit import AuditLog
from .durable_task_models import DurableTaskResult
from .models import CommandRequest, CommandResult, Intent
from .review_service import intent_from_task_step
from .task_models import AgentRun, PlanAttempt, TaskPlan


class CommandResultProjector:
    """Projects durable task state into the stable CLI and transport contract."""

    def project(self, result: DurableTaskResult, *, run_id: str) -> CommandResult:
        planning = result.planning
        plan = planning.plan if planning is not None else None
        intent = _intent_from_result(result)
        payload: dict[str, object] = {
            "task": _task_payload(result),
            "task_id": result.task.task_id,
            "task_revision": result.task.revision,
        }
        if planning is not None:
            payload.update(_planning_payload(planning))
        if plan is not None:
            payload["plan"] = asdict(plan)
            payload["plan_id"] = plan.plan_id
        if result.execution is not None:
            execution = asdict(result.execution)
            payload["execution"] = execution
            for field in ("status", "step_results", "verification_results", "verification_status", "error"):
                payload[field] = execution.get(field)
        if result.review_id is not None:
            payload["review_id"] = result.review_id
        if result.overall_status == "needs_review" and plan is not None:
            payload["plan_review"] = {
                "plan_id": plan.plan_id,
                "status": "review_required",
                "review_id": result.review_id,
                "max_risk_level": result.review.risk_level if result.review is not None else "L2",
                "message": result.message,
            }
        payload["run"] = asdict(
            AgentRun(
                run_id=run_id,
                goal_id=result.task.contract_id,
                utterance=result.request.utterance,
                status=_run_status(result.overall_status),
                selected_transport=result.request.transport,
                attempt_ids=tuple(item.attempt_id for item in result.attempts),
                final_outcome=result.overall_status,
            )
        )
        payload["attempts"] = [_attempt_payload(item) for item in result.attempts]
        _add_selected_strategy_projection(payload, plan)
        status = _public_status(result.overall_status)
        if result.task.task_id.startswith("missing_"):
            status = "rejected"
        return CommandResult(
            status=status,
            intent=intent,
            result=payload,
            selected_target=result.selected_target,
            trace_run_id=run_id,
            review_id=result.review_id,
            transport=result.request.transport,
            message=result.message,
            review=result.review,
            execution_status=result.execution_status,
            acceptance_status=result.acceptance_status,
            overall_status=result.overall_status,
        )


class AuditResultRecorder:
    """Persists the final public projection without becoming task-state authority."""

    def __init__(self, audit: AuditLog) -> None:
        self.audit = audit

    def record(self, request: CommandRequest, result: CommandResult, trace_run_id: str) -> CommandResult:
        if result.audit_id is not None:
            return result
        audit_id = self.audit.record(
            request=request,
            intent=result.intent,
            status=result.status,
            result=result.result,
            selected_target=result.selected_target,
            message=result.message,
            review=result.review,
            review_id=result.review_id,
            plan_id=_result_plan_id(result),
            execution_status=result.execution_status,
            acceptance_status=result.acceptance_status,
            overall_status=result.overall_status,
            trace_run_id=trace_run_id,
        )
        return replace(result, trace_run_id=trace_run_id, audit_id=audit_id)

    @staticmethod
    def metadata(result: CommandResult) -> dict[str, str | None]:
        return {
            "goal_id": _result_goal_id(result),
            "plan_id": _result_plan_id(result),
            "selected_strategy_id": _result_selected_strategy_id(result),
        }


def _task_payload(result: DurableTaskResult) -> dict[str, object]:
    state = result.task
    return {
        "task_id": state.task_id,
        "contract_id": state.contract_id,
        "status": state.status.value,
        "revision": state.revision,
        "current_step_id": state.current_step_id,
        "completed_step_ids": list(state.completed_step_ids),
        "pending_interaction_id": state.pending_interaction_id,
        "next_wake_at": state.next_wake_at,
        "wait_event_key": state.wait_event_key,
        "deadline_at": state.deadline_at,
        "last_event": state.last_event,
        "terminal_outcome": asdict(state.terminal_outcome) if state.terminal_outcome is not None else None,
    }


def _planning_payload(planning: object) -> dict[str, object]:
    payload: dict[str, object] = {}
    for name in (
        "understanding",
        "analysis",
        "goal_synthesis",
        "candidate_set",
        "route_decision",
        "domain_routing",
        "observation_request",
        "observation_receipt",
        "capability_exposure",
    ):
        value = getattr(planning, name, None)
        if value is not None:
            payload[name] = asdict(value)
    candidates = getattr(planning, "candidates", ())
    payload["candidates"] = [asdict(item) for item in candidates]
    return payload


def _intent_from_result(result: DurableTaskResult) -> Intent:
    planning = result.planning
    plan = planning.plan if planning is not None else None
    if plan is not None and plan.steps:
        return intent_from_task_step(plan.steps[0])
    if planning is not None and planning.understanding.provider_intent is not None:
        return planning.understanding.provider_intent
    return Intent.unknown(result.message or "durable task did not yield an executable intent")


def _public_status(overall: str) -> str:
    return {
        "needs_review": "review_required",
        "needs_user_input": "ambiguous",
        "dry_run": "dry_run",
        "completed": "executed",
        "rejected": "rejected",
        "blocked": "rejected",
    }.get(overall, "failed")


def _run_status(overall: str) -> str:
    return {
        "completed": "completed",
        "dry_run": "dry_run",
        "needs_review": "needs_review",
        "needs_user_input": "needs_user_input",
        "blocked": "blocked",
        "incomplete": "incomplete",
        "rejected": "rejected",
    }.get(overall, "failed")


def _attempt_payload(attempt: PlanAttempt) -> dict[str, object]:
    payload = asdict(attempt)
    if attempt.task_plan is not None:
        payload["plan_id"] = attempt.task_plan.plan_id
        payload["step_ids"] = [step.id for step in attempt.task_plan.steps]
        payload["capability_ids"] = [step.capability_id for step in attempt.task_plan.steps]
        payload.pop("task_plan", None)
    return payload


def _add_selected_strategy_projection(payload: dict[str, object], plan: TaskPlan | None) -> None:
    if plan is None:
        return
    payload["selected_strategy_id"] = f"strategy_{plan.selected_route_id}"


def _result_goal_id(result: CommandResult) -> str | None:
    if not isinstance(result.result, dict):
        return None
    run = result.result.get("run")
    return str(run["goal_id"]) if isinstance(run, dict) and run.get("goal_id") is not None else None


def _result_plan_id(result: CommandResult) -> str | None:
    if not isinstance(result.result, dict):
        return None
    value = result.result.get("plan_id")
    return str(value) if value is not None else None


def _result_selected_strategy_id(result: CommandResult) -> str | None:
    if not isinstance(result.result, dict):
        return None
    value = result.result.get("selected_strategy_id")
    return str(value) if value is not None else None
