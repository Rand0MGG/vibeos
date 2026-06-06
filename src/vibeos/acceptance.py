from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from .domain_models import ObservationReceipt, ObservationRequest
from .task_models import PlanExecutionResult, TaskPlan


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
    evidence: tuple[AcceptanceEvidence, ...] = ()
    observation_request: ObservationRequest | None = None
    observation_receipt: ObservationReceipt | None = None


class AcceptanceEngine:
    def evaluate(
        self,
        *,
        plan: TaskPlan,
        execution: PlanExecutionResult,
        verification_results: tuple[dict[str, Any], ...],
        observation_request: ObservationRequest | None = None,
        observation_receipt: ObservationReceipt | None = None,
        dry_run: bool = False,
    ) -> AcceptanceResult:
        if dry_run or execution.status == "pending":
            return AcceptanceResult(
                status="skipped",
                message="acceptance skipped for dry-run execution",
                evidence=(AcceptanceEvidence(source="execution", status="dry_run"),),
                observation_request=observation_request,
                observation_receipt=observation_receipt,
            )
        if execution.status != "succeeded":
            return AcceptanceResult(
                status="skipped",
                message="acceptance skipped because execution did not succeed",
                evidence=(AcceptanceEvidence(source="execution", status=execution.status),),
                observation_request=observation_request,
                observation_receipt=observation_receipt,
            )

        route_domain = plan.routes[0].domain_id if plan.routes else ""
        verifier_statuses = [str(item.get("status", "skipped")) for item in verification_results]
        browser_payload = _package_payload(observation_receipt, "browser_context")
        error_state = str(browser_payload.get("error_state") or "")

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
        if error_state in browser_errors:
            return AcceptanceResult(
                status="failed",
                message=f"browser postcondition reported {error_state}",
                reasons=(error_state,),
                evidence=tuple(evidence),
                observation_request=observation_request,
                observation_receipt=observation_receipt,
            )

        if "failed" in verifier_statuses:
            browser_observation_missing = any(
                "did not observe" in str(item.get("message", "")) for item in verification_results
            )
            status: AcceptanceStatus = "indeterminate" if route_domain == "browser" and browser_observation_missing else "failed"
            return AcceptanceResult(
                status=status,
                message="verifier checks did not accept the completed user goal",
                reasons=tuple(str(item.get("verifier_id", "unknown")) for item in verification_results if item.get("status") == "failed"),
                evidence=tuple(evidence),
                observation_request=observation_request,
                observation_receipt=observation_receipt,
            )

        if any(status in {"skipped", "unavailable"} for status in verifier_statuses):
            return AcceptanceResult(
                status="indeterminate",
                message="execution succeeded but acceptance evidence is incomplete",
                reasons=("insufficient_verifier_evidence",),
                evidence=tuple(evidence),
                observation_request=observation_request,
                observation_receipt=observation_receipt,
            )

        return AcceptanceResult(
            status="passed",
            message="execution and acceptance evidence satisfy the requested goal",
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
