from __future__ import annotations

from dataclasses import dataclass, field
import json
from typing import Any, Literal

from .domain_models import ObservationReceipt, ObservationRequest
from .semantic_acceptance import (
    OpenAICompatibleSemanticAcceptanceProvider,
    SemanticAcceptanceProvider,
    determine_allowed_semantic_decisions,
)
from .task_models import PlanExecutionResult, TaskPlan
from .task_trace import record_model_io, record_trace_event


AcceptanceStatus = Literal["passed", "failed", "indeterminate", "skipped"]


@dataclass(frozen=True)
class AcceptanceEvidence:
    source: str
    status: str
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class AcceptanceResult:
    status: AcceptanceStatus
    message: str = ""
    reasons: tuple[str, ...] = ()
    semantic_decision: str = "skipped"
    semantic_summary_id: str | None = None
    semantic_acceptance_decision_id: str | None = None
    evidence: tuple[AcceptanceEvidence, ...] = ()
    observation_request: ObservationRequest | None = None
    observation_receipt: ObservationReceipt | None = None


class AcceptanceEngine:
    def __init__(self, provider: SemanticAcceptanceProvider | None = None) -> None:
        self.provider = provider or OpenAICompatibleSemanticAcceptanceProvider()
        self._summary_cache: dict[str, object] = {}

    def evaluate(
        self,
        *,
        plan: TaskPlan,
        execution: PlanExecutionResult,
        verification_results: tuple[dict[str, Any], ...],
        observation_request: ObservationRequest | None = None,
        observation_receipt: ObservationReceipt | None = None,
        dry_run: bool = False,
        understanding_id: str | None = None,
        candidate_set_id: str | None = None,
        route_decision_id: str | None = None,
    ) -> AcceptanceResult:
        if dry_run or execution.status == "pending":
            return AcceptanceResult(
                status="skipped",
                message="acceptance skipped for dry-run execution",
                semantic_decision="skipped",
                evidence=(AcceptanceEvidence(source="execution", status="dry_run"),),
                observation_request=observation_request,
                observation_receipt=observation_receipt,
            )
        if execution.status != "succeeded":
            return AcceptanceResult(
                status="skipped",
                message="acceptance skipped because execution did not succeed",
                semantic_decision="skipped",
                evidence=(AcceptanceEvidence(source="execution", status=execution.status),),
                observation_request=observation_request,
                observation_receipt=observation_receipt,
            )

        route_domain = plan.routes[0].domain_id if plan.routes else ""
        verifier_statuses = [str(item.get("status", "skipped")) for item in verification_results]
        browser_payload = _package_payload(observation_receipt, "browser_context")
        error_state = str(browser_payload.get("error_state") or "")
        expected_states = tuple(
            {
                "step_id": step.id,
                "kind": step.expected_state.kind,
                "fields": dict(step.expected_state.fields),
            }
            for step in plan.steps
            if step.expected_state is not None
        )
        browser_context = _sanitize_browser_context(browser_payload)
        verification_evidence = tuple(_verification_evidence_payload(item) for item in verification_results)

        evidence: list[AcceptanceEvidence] = [
            AcceptanceEvidence(
                source="execution",
                status=execution.status,
                details={"step_count": len(execution.step_results)},
            )
        ]
        evidence.extend(
            AcceptanceEvidence(
                source=f"verifier:{item.get('verifier_id', 'unknown')}",
                status=str(item.get("status", "skipped")),
                details=item.get("details", {}) if isinstance(item.get("details"), dict) else {},
            )
            for item in verification_results
        )
        if browser_payload:
            evidence.append(
                AcceptanceEvidence(
                    source="observation:browser_context",
                    status="loaded",
                    details=browser_payload,
                )
            )

        browser_errors = {"tls_error", "dns_error", "network_error", "http_404"}
        browser_observation_missing = any(
            "did not observe" in str(item.get("message", "")) for item in verification_results
        )
        verifier_failures = tuple(str(item.get("verifier_id", "unknown")) for item in verification_results if item.get("status") == "failed")
        verifier_incomplete = any(status in {"skipped", "unavailable"} for status in verifier_statuses)
        summary_input = {
            "understanding_id": understanding_id,
            "candidate_set_id": candidate_set_id,
            "route_decision_id": route_decision_id,
            "plan_id": plan.plan_id,
            "plan_goal": plan.display.goal,
            "step_ids": [step.id for step in plan.steps],
            "expected_states": list(expected_states),
            "route_domain": route_domain,
            "hard_blockers": [error_state] if error_state in browser_errors else [],
            "verifier_failures": list(verifier_failures),
            "verifier_incomplete": verifier_incomplete,
            "browser_observation_missing": browser_observation_missing,
            "browser_context": browser_context,
            "verification_evidence": list(verification_evidence),
            "evidence_sources": [item.source for item in evidence],
        }
        summary_cache_key = json.dumps(summary_input, ensure_ascii=False, sort_keys=True)
        cache_hit = summary_cache_key in self._summary_cache
        if cache_hit:
            summary = self._summary_cache[summary_cache_key]
        else:
            summary = self.provider.summarize(input_payload=summary_input)
            self._summary_cache[summary_cache_key] = summary
        allowed_decisions = determine_allowed_semantic_decisions(
            hard_blockers=summary.hard_blockers,
            verifier_failures=summary.verifier_failures,
            verifier_incomplete=summary.verifier_incomplete,
            browser_observation_missing=summary.browser_observation_missing,
        )
        record_model_io(
            phase="acceptance",
            provider=summary.provider_name,
            model=summary.model_name,
            request_payload=summary_input,
            response_payload=None,
            normalized_output={
                "semantic_summary_id": summary.semantic_summary_id,
                "summary_text": summary.summary_text,
                "structured_findings": summary.structured_findings,
            },
            parse_valid=summary.parse_valid,
            fallback_used=summary.fallback_used,
            error=summary.error,
            actor="semantic_acceptance_summary",
            call_kind="structured_followup",
            cache_hit=cache_hit,
            consumed_artifacts={
                "understanding_id": understanding_id,
                "candidate_set_id": candidate_set_id,
                "route_decision_id": route_decision_id,
                "plan_id": plan.plan_id,
            },
        )
        record_trace_event(
            phase="acceptance",
            event_type="semantic_summary_created",
            status="ok",
            actor="acceptance",
            plan_id=plan.plan_id,
            data={
                "artifact_type": "semantic_summary",
                "artifact_id": summary.semantic_summary_id,
                "understanding_id": understanding_id,
                "candidate_set_id": candidate_set_id,
                "route_decision_id": route_decision_id,
                "evidence_sources": list(summary.evidence_sources),
            },
        )
        decision = self.provider.decide(summary=summary, allowed_decisions=allowed_decisions)
        record_model_io(
            phase="acceptance",
            provider=decision.provider_name,
            model=decision.model_name,
            request_payload={"semantic_summary_id": summary.semantic_summary_id, "allowed_decisions": list(allowed_decisions)},
            response_payload=None,
            normalized_output={
                "semantic_acceptance_decision_id": decision.semantic_acceptance_decision_id,
                "decision": decision.decision,
                "acceptance_status": decision.acceptance_status,
                "reason": decision.reason,
            },
            parse_valid=decision.parse_valid,
            fallback_used=decision.fallback_used,
            error=decision.error,
            actor="semantic_acceptance_decider",
            call_kind="structured_followup",
            consumed_artifacts={
                "understanding_id": understanding_id,
                "candidate_set_id": candidate_set_id,
                "route_decision_id": route_decision_id,
                "semantic_summary_id": summary.semantic_summary_id,
                "plan_id": plan.plan_id,
            },
        )
        record_trace_event(
            phase="acceptance",
            event_type="semantic_acceptance_decided",
            status=decision.decision,
            actor="acceptance",
            plan_id=plan.plan_id,
            data={
                "artifact_type": "semantic_acceptance_decision",
                "artifact_id": decision.semantic_acceptance_decision_id,
                "semantic_summary_id": summary.semantic_summary_id,
                "understanding_id": understanding_id,
                "candidate_set_id": candidate_set_id,
                "route_decision_id": route_decision_id,
                "decision": decision.decision,
                "acceptance_status": decision.acceptance_status,
            },
        )
        reasons: tuple[str, ...]
        if verifier_failures:
            reasons = verifier_failures
        elif error_state in browser_errors:
            reasons = (error_state,)
        elif verifier_incomplete:
            reasons = ("insufficient_verifier_evidence",)
        else:
            reasons = ()
        return AcceptanceResult(
            status=decision.acceptance_status,  # type: ignore[arg-type]
            message=decision.reason,
            reasons=reasons,
            semantic_decision=decision.decision,
            semantic_summary_id=summary.semantic_summary_id,
            semantic_acceptance_decision_id=decision.semantic_acceptance_decision_id,
            evidence=tuple(evidence),
            observation_request=observation_request,
            observation_receipt=observation_receipt,
        )


def _package_payload(receipt: ObservationReceipt | None, package_id: str) -> dict[str, Any]:
    if receipt is None:
        return {}
    for package in receipt.packages:
        if package.package_id == package_id:
            return dict(package.payload)
    return {}


def _sanitize_browser_context(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": str(payload.get("status") or ""),
        "requested_url": str(payload.get("requested_url") or ""),
        "active_url": str(payload.get("active_url") or ""),
        "requested_query": str(payload.get("requested_query") or ""),
        "query": str(payload.get("query") or ""),
        "site": str(payload.get("site") or ""),
        "page_title": str(payload.get("page_title") or ""),
        "app_id": str(payload.get("app_id") or ""),
        "error_state": str(payload.get("error_state") or ""),
        "adapter": str(payload.get("adapter") or ""),
        "captured_at": str(payload.get("captured_at") or ""),
    }


def _verification_evidence_payload(item: dict[str, Any]) -> dict[str, Any]:
    details = item.get("details")
    return {
        "verifier_id": str(item.get("verifier_id") or "unknown"),
        "status": str(item.get("status") or "skipped"),
        "message": str(item.get("message") or ""),
        "details": dict(details) if isinstance(details, dict) else {},
    }
