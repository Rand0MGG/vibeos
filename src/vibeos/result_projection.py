from __future__ import annotations

import os
from dataclasses import asdict, replace

from .audit import AuditLog
from .loop_models import GoalLoopResult
from .models import CommandRequest, CommandResult, Intent, ReviewRequest
from .permissions import PermissionPolicy
from .planner import PlanningArtifacts
from .review_service import intent_from_task_step
from .reviews import ReviewStore
from .task_models import AgentRun, PlanAttempt, TaskPlan


class CommandResultProjector:
    """Converts GoalLoop outcomes into the stable public command payload."""

    def __init__(self, *, reviews: ReviewStore, policy: PermissionPolicy) -> None:
        self.reviews = reviews
        self.policy = policy

    def project(
        self,
        *,
        request: CommandRequest,
        planning: PlanningArtifacts,
        run_id: str,
        goal_id: str,
        loop_result: GoalLoopResult,
    ) -> CommandResult:
        payload = dict(loop_result.payload)
        result_review_id = loop_result.review_id or request.review_id
        if result_review_id:
            payload["review_id"] = result_review_id
        payload["loop_snapshot"] = asdict(loop_result.state)
        payload["loop_snapshot_id"] = loop_result.state.loop_snapshot_id

        plan = planning.plan
        intent = _intent_from_planning(planning)
        review = None
        stored_review = self.reviews.get(result_review_id) if result_review_id else None
        if stored_review is not None:
            review = stored_review.review
        status = _public_status(loop_result.overall_status)
        message = loop_result.message
        execution_status = loop_result.execution_status
        acceptance_status = loop_result.acceptance_status
        overall_status = loop_result.overall_status
        if plan is None:
            compatibility_review = self.policy.review(intent)
            if not compatibility_review.allowed:
                review = compatibility_review
                status = "rejected"
                message = compatibility_review.reason
                execution_status = "not_started"
                acceptance_status = "skipped"
                overall_status = "failed"

        if overall_status == "needs_review" and plan is not None:
            payload["plan_review"] = {
                "plan_id": plan.plan_id,
                "status": "review_required",
                "review_id": result_review_id,
                "max_risk_level": review.risk_level if review is not None else "L2",
                "step_reviews": list(stored_review.step_reviews) if stored_review is not None else [],
                "message": message,
            }
        if plan is not None:
            payload.setdefault("plan", asdict(plan))
            payload.setdefault("plan_id", plan.plan_id)
        execution = payload.get("execution")
        if isinstance(execution, dict):
            for field in ("status", "step_results", "verification_results", "verification_status", "error"):
                if field in execution:
                    payload.setdefault(field, execution[field])
        self._add_compatibility_runtime(
            payload=payload,
            request=request,
            planning=planning,
            run_id=run_id,
            goal_id=goal_id,
            attempts=loop_result.attempt_records,
            overall_status=overall_status,
            message=message,
        )
        payload["run"] = asdict(
            AgentRun(
                run_id=run_id,
                goal_id=goal_id,
                utterance=request.utterance,
                status=_run_status_for_overall(overall_status),
                selected_transport=request.transport,
                attempt_ids=tuple(item.attempt_id for item in loop_result.attempt_records),
                final_outcome=overall_status,
            )
        )
        payload["attempts"] = [_attempt_payload(item) for item in loop_result.attempt_records]
        return CommandResult(
            status=status,
            intent=intent,
            result=payload,
            selected_target=loop_result.selected_target,
            trace_run_id=run_id,
            review_id=result_review_id,
            message=message,
            review=review,
            execution_status=execution_status,
            acceptance_status=acceptance_status,
            overall_status=overall_status,
            transport=request.transport,
        )

    @staticmethod
    def review_resume_error(
        review_request: ReviewRequest,
        *,
        code: str,
        message: str,
        transport: str | None,
        legacy: bool = False,
    ) -> CommandResult:
        payload: dict[str, object] = {"error_code": code, "review_id": review_request.review_id}
        if legacy:
            payload["fresh_command_required"] = True
        return CommandResult(
            status="failed",
            intent=review_request.intent,
            result=payload,
            review=review_request.review,
            review_id=review_request.review_id,
            message=message,
            execution_status="not_started",
            acceptance_status="skipped",
            overall_status="failed",
            transport=transport,
        )

    def _add_compatibility_runtime(
        self,
        *,
        payload: dict[str, object],
        request: CommandRequest,
        planning: PlanningArtifacts,
        run_id: str,
        goal_id: str,
        attempts: tuple[PlanAttempt, ...],
        overall_status: str,
        message: str,
    ) -> None:
        plan = attempts[-1].task_plan if attempts and attempts[-1].task_plan is not None else getattr(planning, "plan", None)
        if plan is None:
            return
        route = plan.routes[0] if plan.routes else None
        route_id = plan.selected_route_id
        capability_surface = "browser" if route is not None and route.domain_id == "browser" else "desktop-linux"
        interaction_surface = _interaction_surface(plan)
        strategy_id = f"strategy_{route_id}"
        session_id = f"session_{run_id}"
        turn_id = f"turn_{run_id}"
        runtime_status = _run_status_for_overall(overall_status)
        environment = {
            "platform": "linux" if os.name == "posix" else "windows",
            "transport_mode": request.transport or "local",
            "daemon_available": (request.transport or "") in {"dbus", "http"},
            "desktop_integration_available": True,
            "connectivity_limitations": "offline",
            "deployment_profile": "goal_loop",
            "region": "local",
            "search_policy": "browser_first" if capability_surface == "browser" else "balanced",
            "dry_run": request.dry_run,
        }
        strategy = {
            "strategy_id": strategy_id,
            "route_id": route_id,
            "capability_surface": capability_surface,
            "interaction_surface": interaction_surface,
            "task_plan_id": plan.plan_id,
            "tool_ids": [],
            "priority": float(route.score if route is not None else 1.0),
        }
        runtime_attempts = [_runtime_attempt_payload(item, goal_id, turn_id, strategy_id, capability_surface, interaction_surface) for item in attempts]
        terminal = {
            "status": runtime_status,
            "reason": message,
            "failure_class": _last_failure_class(attempts),
            "verifier_confirmed": overall_status == "completed",
        }
        payload["environment_profile"] = environment
        payload["goal_runtime"] = {
            "goal_id": goal_id,
            "session_id": session_id,
            "goal_spec": {"goal_id": goal_id, "goal_text": plan.utterance, "goal_type": route_id},
            "status": runtime_status,
            "current_strategy_id": strategy_id,
            "turn_ids": [turn_id],
            "attempt_ids": [item.attempt_id for item in attempts],
            "terminal_outcome": terminal,
        }
        payload["goal_turn"] = {
            "turn_id": turn_id,
            "goal_id": goal_id,
            "turn_index": 1,
            "utterance": request.utterance,
            "attempt_ids": [item.attempt_id for item in attempts],
            "status": runtime_status,
        }
        payload["strategy_candidates"] = [strategy]
        payload["selected_strategy_id"] = strategy_id
        payload["run_ledger"] = {
            "session_id": session_id,
            "goal_id": goal_id,
            "strategy_history": [],
            "attempts": runtime_attempts,
            "terminal_outcome": terminal,
        }


class AuditResultRecorder:
    """Persists final command results and extracts their audit metadata."""

    def __init__(self, audit: AuditLog) -> None:
        self.audit = audit

    def record(self, request: CommandRequest, result: CommandResult, trace_run_id: str) -> CommandResult:
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
                plan_id=_result_plan_id(result),
                execution_status=result.execution_status,
                acceptance_status=result.acceptance_status,
                overall_status=result.overall_status,
                trace_run_id=trace_run_id,
                understanding_id=_result_understanding_id(result),
                candidate_set_id=_result_candidate_set_id(result),
                selected_route_decision_id=_result_route_decision_id(result),
                selected_strategy_decision_id=_result_selected_strategy_decision_id(result),
                semantic_acceptance_decision_id=_result_semantic_acceptance_decision_id(result),
                loop_snapshot_id=_result_loop_snapshot_id(result),
            )
        return replace(result, trace_run_id=trace_run_id, audit_id=audit_id)

    @staticmethod
    def metadata(result: CommandResult) -> dict[str, str | None]:
        return {
            "goal_id": _result_goal_id(result),
            "plan_id": _result_plan_id(result),
            "selected_strategy_id": _result_selected_strategy_id(result),
        }


def _public_status(overall_status: str) -> str:
    if overall_status == "needs_review":
        return "review_required"
    if overall_status == "needs_user_input":
        return "ambiguous"
    if overall_status == "dry_run":
        return "dry_run"
    if overall_status == "completed":
        return "executed"
    return "failed"


def _intent_from_planning(planning: PlanningArtifacts) -> Intent:
    plan = planning.plan
    if plan is not None and plan.steps:
        return intent_from_task_step(plan.steps[0])
    understanding = planning.understanding
    if understanding is not None and understanding.provider_intent is not None:
        return understanding.provider_intent
    for candidate in planning.candidates:
        if candidate.steps:
            return intent_from_task_step(candidate.steps[0])
    return Intent.unknown("planning did not yield a compatibility intent")


def _interaction_surface(plan: TaskPlan) -> str:
    provenance = plan.provenance if isinstance(plan.provenance, dict) else {}
    surface = str(provenance.get("interaction_surface") or "")
    if surface == "structured" or plan.selected_route_id == "browser_search_followup_route":
        return "structured_ui_action"
    if surface == "shortcut":
        return "computer_use_action"
    return "native_action"


def _attempt_payload(attempt: PlanAttempt) -> dict[str, object]:
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


def _runtime_attempt_payload(
    attempt: PlanAttempt,
    goal_id: str,
    turn_id: str,
    strategy_id: str,
    capability_surface: str,
    interaction_surface: str,
) -> dict[str, object]:
    execution = attempt.execution_result
    failure = attempt.failure
    return {
        "attempt_id": attempt.attempt_id,
        "turn_id": turn_id,
        "goal_id": goal_id,
        "strategy_id": strategy_id,
        "route_id": attempt.selected_route_id,
        "trigger": attempt.trigger,
        "task_plan_id": attempt.task_plan.plan_id if attempt.task_plan is not None else None,
        "capability_surface": capability_surface,
        "interaction_surface": interaction_surface,
        "understanding_id": attempt.understanding_id,
        "candidate_set_id": attempt.candidate_set_id,
        "route_decision_id": attempt.route_decision_id,
        "replan_decision_id": attempt.replan_decision_id,
        "semantic_summary_id": attempt.semantic_summary_id,
        "semantic_acceptance_decision_id": attempt.semantic_acceptance_decision_id,
        "step_safety_review_ids": list(attempt.step_safety_review_ids),
        "outcome_status": execution.overall_status if execution is not None else "failed",
        "failure_class": failure.failure_class if failure is not None else "none",
        "message": failure.message if failure is not None else (execution.error if execution is not None else ""),
    }


def _last_failure_class(attempts: tuple[PlanAttempt, ...]) -> str:
    for attempt in reversed(attempts):
        if attempt.failure is not None:
            return str(attempt.failure.failure_class)
    return "none"


def _run_status_for_overall(overall_status: str) -> str:
    return {
        "completed": "completed",
        "dry_run": "dry_run",
        "needs_review": "needs_review",
        "needs_user_input": "needs_user_input",
        "blocked": "blocked",
        "incomplete": "incomplete",
    }.get(overall_status, "failed")


def _result_goal_id(result: CommandResult) -> str | None:
    if not isinstance(result.result, dict):
        return None
    run = result.result.get("run")
    if isinstance(run, dict) and run.get("goal_id") is not None:
        return str(run["goal_id"])
    runtime = result.result.get("goal_runtime")
    if isinstance(runtime, dict) and runtime.get("goal_id") is not None:
        return str(runtime["goal_id"])
    return None


def _result_plan_id(result: CommandResult) -> str | None:
    if not isinstance(result.result, dict):
        return None
    if result.result.get("plan_id") is not None:
        return str(result.result["plan_id"])
    plan = result.result.get("plan")
    if isinstance(plan, dict) and plan.get("plan_id") is not None:
        return str(plan["plan_id"])
    return None


def _result_selected_strategy_id(result: CommandResult) -> str | None:
    if isinstance(result.result, dict) and result.result.get("selected_strategy_id") is not None:
        return str(result.result["selected_strategy_id"])
    return None


def _result_selected_strategy_decision_id(result: CommandResult) -> str | None:
    if not isinstance(result.result, dict):
        return None
    ledger = result.result.get("run_ledger")
    history = ledger.get("strategy_history") if isinstance(ledger, dict) else None
    if isinstance(history, list) and history and isinstance(history[-1], dict) and history[-1].get("strategy_decision_id") is not None:
        return str(history[-1]["strategy_decision_id"])
    return None


def _result_understanding_id(result: CommandResult) -> str | None:
    if not isinstance(result.result, dict):
        return None
    understanding = result.result.get("understanding")
    if isinstance(understanding, dict):
        for key in ("primary_understanding_id", "understanding_id"):
            if understanding.get(key) is not None:
                return str(understanding[key])
    return None


def _result_candidate_set_id(result: CommandResult) -> str | None:
    if isinstance(result.result, dict):
        candidate_set = result.result.get("candidate_set")
        if isinstance(candidate_set, dict) and candidate_set.get("candidate_set_id") is not None:
            return str(candidate_set["candidate_set_id"])
    return None


def _result_route_decision_id(result: CommandResult) -> str | None:
    if isinstance(result.result, dict):
        route = result.result.get("route_decision")
        if isinstance(route, dict) and route.get("route_decision_id") is not None:
            return str(route["route_decision_id"])
    return None


def _result_semantic_acceptance_decision_id(result: CommandResult) -> str | None:
    if not isinstance(result.result, dict):
        return None
    for key in ("execution", "preview"):
        execution = result.result.get(key)
        acceptance = execution.get("acceptance_result") if isinstance(execution, dict) else None
        if isinstance(acceptance, dict) and acceptance.get("semantic_acceptance_decision_id") is not None:
            return str(acceptance["semantic_acceptance_decision_id"])
    return None


def _result_loop_snapshot_id(result: CommandResult) -> str | None:
    if not isinstance(result.result, dict):
        return None
    if result.result.get("loop_snapshot_id") is not None:
        return str(result.result["loop_snapshot_id"])
    snapshot = result.result.get("loop_snapshot")
    if isinstance(snapshot, dict) and snapshot.get("loop_snapshot_id") is not None:
        return str(snapshot["loop_snapshot_id"])
    return None
