from __future__ import annotations

import os
from dataclasses import asdict, dataclass, field
from time import perf_counter
from typing import Any

from .app_fixtures import AppSearchFixture
from .audit import AuditLog
from .models import CommandRequest, Intent
from .run_context import RunContext
from .task_models import StepExecutionResult, TaskPlan, TaskStep, canonicalize_target_for_action
from .task_trace import record_trace_event
from .tool_protocol import ToolExecutionContext, ToolInvocationEnvelope, ToolRegistry, ToolResult
from .tools.registry import CapabilityRecipeRegistry


@dataclass(frozen=True)
class ExecutionEnvironment:
    platform: str
    transport_mode: str
    dry_run: bool
    browser_site_catalog: dict[str, str] = field(default_factory=dict)
    browser_search_catalog: dict[str, dict[str, object]] = field(default_factory=dict)
    app_fixture_catalog: dict[str, AppSearchFixture] = field(default_factory=dict)


class StepExecutionService:
    """Run one reviewed task step through registered, host-owned tool recipes."""

    def __init__(
        self,
        *,
        tools: ToolRegistry,
        audit: AuditLog,
        recipes: CapabilityRecipeRegistry | None = None,
        browser_site_catalog: dict[str, str] | None = None,
        browser_search_catalog: dict[str, dict[str, object]] | None = None,
        app_fixture_catalog: dict[str, AppSearchFixture] | None = None,
    ) -> None:
        self._tools = tools
        self._audit = audit
        self._recipes = recipes or CapabilityRecipeRegistry()
        self._browser_site_catalog = dict(browser_site_catalog or {})
        self._browser_search_catalog = {key: dict(value) for key, value in (browser_search_catalog or {}).items()}
        self._app_fixture_catalog = dict(app_fixture_catalog or {})

    def execute_step(
        self,
        context: RunContext,
        plan: TaskPlan,
        step: TaskStep,
        request: CommandRequest,
        attempt_id: str,
    ) -> StepExecutionResult:
        started = perf_counter()
        calls = self._recipes.calls_for(plan, step)
        if not calls:
            return self._result(
                context=context,
                plan=plan,
                step=step,
                request=request,
                attempt_id=attempt_id,
                duration_ms=0,
                status="failed",
                adapter=None,
                adapter_status=None,
                result={},
                error=f"no registered execution recipe for {step.action}",
                error_code="unsupported_request",
            )

        environment = ExecutionEnvironment(
            platform="linux" if os.name == "posix" else "windows",
            transport_mode=context.transport or "local",
            dry_run=context.dry_run,
            browser_site_catalog=dict(self._browser_site_catalog),
            browser_search_catalog={key: dict(value) for key, value in self._browser_search_catalog.items()},
            app_fixture_catalog=dict(self._app_fixture_catalog),
        )
        state: dict[str, Any] = {}
        envelopes: list[ToolInvocationEnvelope] = []
        final_result: ToolResult | None = None
        for call in calls:
            tool_context = ToolExecutionContext(
                session_id=f"session_{context.run_id}",
                goal_id=context.goal_id,
                turn_id=context.run_id,
                attempt_id=attempt_id,
                strategy_id=plan.selected_route_id or step.action,
                environment=environment,
                state=state,
            )
            envelope, tool_result = self._tools.invoke(call.tool_id, dict(call.payload), tool_context)
            envelopes.append(envelope)
            final_result = tool_result
            state.update(tool_result.output)
            state.update(tool_result.state_updates)
            record_trace_event(
                phase="execution",
                event_type="registered_tool_completed",
                status=envelope.status,
                actor="step_execution_service",
                plan_id=plan.plan_id,
                step_id=step.id,
                attempt_id=attempt_id,
                data={"tool_id": envelope.tool_id, "family": envelope.family, "failure_class": envelope.failure_class},
            )
            if tool_result.status != "succeeded":
                return self._result(
                    context=context,
                    plan=plan,
                    step=step,
                    request=request,
                    attempt_id=attempt_id,
                    duration_ms=max(0, round((perf_counter() - started) * 1000)),
                    status="failed",
                    adapter=_string_value(tool_result.output, "adapter") or envelope.tool_id,
                    adapter_status=_string_value(tool_result.output, "adapter_status") or envelope.status,
                    result={**state, **tool_result.output, "tool_invocations": [asdict(item) for item in envelopes]},
                    error=tool_result.message or "registered tool failed",
                    error_code=tool_result.failure_class or envelope.failure_class,
                )

        assert final_result is not None
        final_envelope = envelopes[-1]
        result_payload = {**state, **final_result.output, "tool_invocations": [asdict(item) for item in envelopes]}
        selected_target = _selected_target(final_result.output, state)
        if selected_target is not None:
            result_payload.setdefault("selected_target", selected_target)
        return self._result(
            context=context,
            plan=plan,
            step=step,
            request=request,
            attempt_id=attempt_id,
            duration_ms=max(0, round((perf_counter() - started) * 1000)),
            status="succeeded",
            adapter=_string_value(final_result.output, "adapter") or final_envelope.tool_id,
            adapter_status=_string_value(final_result.output, "adapter_status") or ("dry_run" if context.dry_run else "succeeded"),
            result=result_payload,
            error=None,
            error_code=None,
        )

    def _result(
        self,
        *,
        context: RunContext,
        plan: TaskPlan,
        step: TaskStep,
        request: CommandRequest,
        attempt_id: str,
        duration_ms: int,
        status: str,
        adapter: str | None,
        adapter_status: str | None,
        result: dict[str, Any],
        error: str | None,
        error_code: str | None,
    ) -> StepExecutionResult:
        selected_target = _selected_target(result, {})
        command_status = "failed" if status != "succeeded" else ("dry_run" if context.dry_run else "executed")
        audit_id = self._audit.record(
            request=request,
            intent=_intent_from_step(step),
            status=command_status,
            result=result,
            selected_target=selected_target,
            message=error or "",
            review_id=context.review_id,
            plan_id=plan.plan_id,
            step_id=step.id,
            layer="registered_tool_execute",
            execution_status="failed" if status != "succeeded" else ("dry_run" if context.dry_run else "succeeded"),
            acceptance_status="skipped",
            overall_status="failed" if status != "succeeded" else ("dry_run" if context.dry_run else "incomplete"),
            trace_run_id=context.run_id,
        )
        return StepExecutionResult(
            step_id=step.id,
            layer="registered_tool_execute",
            status="succeeded" if status == "succeeded" else "failed",
            adapter=adapter,
            capability_id=step.capability_id,
            attempt=1,
            attempt_id=attempt_id,
            duration_ms=duration_ms,
            adapter_status=adapter_status,
            diagnostics={
                "action": step.action,
                "transport": context.transport,
                "dry_run": context.dry_run,
                "selected_target": selected_target,
                "attempt_id": attempt_id,
            },
            error_code=error_code,
            result=result,
            error=error,
            audit_id=audit_id,
        )


def _intent_from_step(step: TaskStep) -> Intent:
    return Intent(
        action=step.action,
        target=canonicalize_target_for_action(step.action, dict(step.target)),
        reason=f"task step {step.id}",
        requires_confirmation=False,
    )


def _string_value(payload: dict[str, Any], key: str) -> str | None:
    value = payload.get(key)
    return str(value) if value is not None else None


def _selected_target(payload: dict[str, Any], state: dict[str, Any]) -> str | None:
    for source in (payload, state):
        for key in ("selected_target", "uri", "resolved_url"):
            value = source.get(key)
            if value is not None and str(value):
                return str(value)
    return None
