from __future__ import annotations

from .task_models import FailureClassification, PlanExecutionResult, TaskPlan


class FailureClassifier:
    def classify(self, plan: TaskPlan, execution: PlanExecutionResult) -> FailureClassification:
        if execution.execution_status == "dry_run":
            return FailureClassification(failure_class="none", message="dry-run execution does not require failure classification")

        if execution.execution_status == "succeeded":
            if isinstance(execution.acceptance_result, dict) and bool(execution.acceptance_result.get("same_action_no_progress", False)):
                return FailureClassification(
                    failure_class="same_action_no_progress",
                    message=self._acceptance_message(execution) or "execution succeeded but did not change the observed state",
                    replannable=True,
                    details={"acceptance_status": execution.acceptance_status},
                )
            if execution.acceptance_status == "passed":
                return FailureClassification(failure_class="none", message="execution and acceptance completed")
            if execution.acceptance_status == "indeterminate":
                return FailureClassification(
                    failure_class="acceptance_unverified",
                    message=self._acceptance_message(execution) or "execution succeeded but acceptance evidence is incomplete",
                    replannable=True,
                    details={"acceptance_status": execution.acceptance_status},
                )
            if execution.acceptance_status == "failed":
                return FailureClassification(
                    failure_class="acceptance_failed",
                    message=self._acceptance_message(execution) or "execution succeeded but acceptance failed",
                    replannable=True,
                    details={"acceptance_status": execution.acceptance_status},
                )
            return FailureClassification(failure_class="none", message="execution completed")

        step = next((item for item in execution.step_results if item.status == "failed"), None)
        if step is None:
            return FailureClassification(
                failure_class="unsupported_request",
                message=execution.error or "execution failed before any step-specific diagnostics were captured",
            )

        adapter_status = str(step.adapter_status or "")
        error = f"{step.error or ''} {step.error_code or ''}".lower()
        action = str(step.capability_id or "")

        if adapter_status == "timeout" or "timeout" in error or "timed out" in error:
            details = {"adapter": step.adapter, "capability_id": action}
            if str(step.adapter or "").startswith("transport.") or "commandrequest timed out" in error:
                return FailureClassification(
                    failure_class="transport_timeout",
                    message=step.error or execution.error or "transport request timed out",
                    retryable=True,
                    details=details,
                )
            return FailureClassification(
                failure_class="tool_timeout",
                message=step.error or execution.error or "tool execution timed out",
                retryable=True,
                details=details,
            )

        if adapter_status == "unavailable":
            return FailureClassification(
                failure_class="environment_unreachable",
                message=step.error or execution.error or "required environment capability is unavailable",
                retryable=False,
                details={"adapter": step.adapter, "capability_id": action},
            )

        if action == "app.open" and "no application matched" in error:
            return FailureClassification(
                failure_class="semantic_mismatch",
                message=step.error or execution.error or "requested target does not look like a local application",
                replannable=True,
                details={"capability_id": action, "selected_route_id": plan.selected_route_id},
            )

        if action.startswith("window.") and "no window matched" in error:
            return FailureClassification(
                failure_class="semantic_mismatch",
                message=step.error or execution.error or "requested target does not look like a resolvable window",
                replannable=True,
                details={"capability_id": action, "selected_route_id": plan.selected_route_id},
            )

        if action == "app.search_history" and any(
            phrase in error
            for phrase in (
                "structured search control was not visible",
                "shortcut search mode is unavailable",
                "search query entry requires a visible control",
            )
        ):
            return FailureClassification(
                failure_class="semantic_mismatch",
                message=step.error or execution.error or "requested in-app interaction surface is unavailable",
                replannable=True,
                details={"capability_id": action, "selected_route_id": plan.selected_route_id},
            )

        if action in {"browser.open_named_target", "browser.search_web"} and any(
            phrase in error
            for phrase in (
                "no local direct-open resolution matched the named website target",
                "browser search results did not provide a follow-up destination",
            )
        ):
            return FailureClassification(
                failure_class="semantic_mismatch",
                message=step.error or execution.error or "requested named website route did not resolve to an official target",
                replannable=True,
                details={"capability_id": action, "selected_route_id": plan.selected_route_id},
            )

        if execution.status in {"rejected", "needs_user_input"}:
            return FailureClassification(
                failure_class="permission_blocked",
                message=step.error or execution.error or "execution requires additional permission or user disambiguation",
                replannable=False,
                details={"capability_id": action},
            )

        return FailureClassification(
            failure_class="unsupported_request",
            message=step.error or execution.error or "execution failed",
            retryable=False,
            replannable=False,
            details={"capability_id": action, "adapter_status": adapter_status},
        )

    @staticmethod
    def _acceptance_message(execution: PlanExecutionResult) -> str:
        payload = execution.acceptance_result or {}
        if isinstance(payload, dict):
            return str(payload.get("message", ""))
        return ""
