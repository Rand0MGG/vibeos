from __future__ import annotations

from dataclasses import dataclass, field
from hashlib import sha256
import json
import urllib.error
from typing import Any

from .models import utc_now_iso
from .provider_client import env_flag_enabled, load_openai_compatible_provider_config, request_json_object
from .task_models import SemanticDecision

SEMANTIC_SUMMARY_SYSTEM_PROMPT = """You are VibeOS's bounded semantic evidence summarizer.
Summarize the provided structured evidence only.
Do not invent new observations, tools, routes, or facts.
Return exactly one JSON object with this schema:
{
  "summary_text": "short summary",
  "supports_completion": true,
  "evidence_incomplete": false,
  "contradiction_detected": false,
  "clarification_needed": false
}
Return JSON only."""

SEMANTIC_DECISION_SYSTEM_PROMPT = """You are VibeOS's bounded semantic acceptance decider.
Choose exactly one allowed internal semantic decision.
Do not output public runtime statuses.
Return exactly one JSON object with this schema:
{
  "decision": "complete",
  "reason": "short explanation"
}
Return JSON only."""


@dataclass(frozen=True)
class SemanticEvidenceSummary:
    semantic_summary_id: str
    understanding_id: str | None
    candidate_set_id: str | None
    route_decision_id: str | None
    route_domain: str
    hard_blockers: tuple[str, ...] = ()
    verifier_failures: tuple[str, ...] = ()
    verifier_incomplete: bool = False
    browser_observation_missing: bool = False
    evidence_sources: tuple[str, ...] = ()
    summary_text: str = ""
    structured_findings: dict[str, Any] = field(default_factory=dict)
    provider_name: str = "provider"
    model_name: str = "structured"
    parse_valid: bool = True
    fallback_used: bool = False
    error: str | None = None


@dataclass(frozen=True)
class SemanticAcceptanceDecision:
    semantic_acceptance_decision_id: str
    semantic_summary_id: str
    understanding_id: str | None
    candidate_set_id: str | None
    route_decision_id: str | None
    decision: SemanticDecision
    acceptance_status: str
    reason: str
    provider_name: str
    model_name: str
    parse_valid: bool = True
    fallback_used: bool = False
    error: str | None = None


class SemanticAcceptanceProvider:
    provider_name = "provider"
    model_name = "structured"

    def summarize(self, *, input_payload: dict[str, Any]) -> SemanticEvidenceSummary:
        raise NotImplementedError

    def decide(self, *, summary: SemanticEvidenceSummary, allowed_decisions: tuple[SemanticDecision, ...]) -> SemanticAcceptanceDecision:
        raise NotImplementedError


class DeterministicSemanticAcceptanceProvider(SemanticAcceptanceProvider):
    provider_name = "host_semantic_acceptance"
    model_name = "deterministic-local"

    def summarize(self, *, input_payload: dict[str, Any]) -> SemanticEvidenceSummary:
        route_domain = str(input_payload.get("route_domain") or "")
        hard_blockers = tuple(str(item) for item in input_payload.get("hard_blockers", ()))
        verifier_failures = tuple(str(item) for item in input_payload.get("verifier_failures", ()))
        verifier_incomplete = bool(input_payload.get("verifier_incomplete", False))
        browser_context = normalized_browser_context(input_payload.get("browser_context"))
        verification_evidence = normalized_verification_evidence(input_payload.get("verification_evidence"))
        verifier_passed = any(str(item.get("status") or "") == "passed" for item in verification_evidence)
        browser_observation_missing = bool(input_payload.get("browser_observation_missing", False))
        browser_requested = bool(browser_context.get("requested_url") or browser_context.get("requested_query"))
        browser_observed = bool(browser_context.get("active_url") or browser_context.get("query") or browser_context.get("page_title"))
        browser_context_incomplete = route_domain == "browser" and browser_requested and not browser_observed and not verifier_passed
        browser_observation_missing = browser_observation_missing or browser_context_incomplete
        evidence_sources = tuple(str(item) for item in input_payload.get("evidence_sources", ()))
        structured_findings = {
            "route_domain": route_domain,
            "hard_blockers": list(hard_blockers),
            "verifier_failures": list(verifier_failures),
            "verifier_incomplete": verifier_incomplete,
            "browser_observation_missing": browser_observation_missing,
            "browser_context": browser_context,
            "verification_evidence": verification_evidence,
            "supports_completion": not hard_blockers and not verifier_failures and not verifier_incomplete and not browser_context_incomplete,
            "evidence_incomplete": verifier_incomplete or browser_observation_missing,
            "contradiction_detected": bool(hard_blockers) or (bool(verifier_failures) and not browser_observation_missing),
            "clarification_needed": False,
        }
        summary_text = summarize_text(
            route_domain=route_domain,
            hard_blockers=hard_blockers,
            verifier_failures=verifier_failures,
            verifier_incomplete=verifier_incomplete,
            browser_observation_missing=browser_observation_missing,
        )
        return SemanticEvidenceSummary(
            semantic_summary_id=make_semantic_summary_id(input_payload),
            understanding_id=str_or_none(input_payload.get("understanding_id")),
            candidate_set_id=str_or_none(input_payload.get("candidate_set_id")),
            route_decision_id=str_or_none(input_payload.get("route_decision_id")),
            route_domain=route_domain,
            hard_blockers=hard_blockers,
            verifier_failures=verifier_failures,
            verifier_incomplete=verifier_incomplete,
            browser_observation_missing=browser_observation_missing,
            evidence_sources=evidence_sources,
            summary_text=summary_text,
            structured_findings=structured_findings,
            provider_name=self.provider_name,
            model_name=self.model_name,
        )

    def decide(self, *, summary: SemanticEvidenceSummary, allowed_decisions: tuple[SemanticDecision, ...]) -> SemanticAcceptanceDecision:
        contradiction = bool(summary.structured_findings.get("contradiction_detected", False))
        evidence_incomplete = bool(summary.structured_findings.get("evidence_incomplete", False))
        supports_completion = bool(summary.structured_findings.get("supports_completion", False))
        clarification_needed = bool(summary.structured_findings.get("clarification_needed", False))

        if "semantic_failure" in allowed_decisions and contradiction:
            semantic = "semantic_failure"
            reason = contradiction_reason(summary)
        elif "clarification_needed" in allowed_decisions and clarification_needed:
            semantic = "clarification_needed"
            reason = "semantic evidence indicates clarification is needed before claiming success"
        elif "incomplete" in allowed_decisions and evidence_incomplete:
            semantic = "incomplete"
            reason = incomplete_reason(summary)
        elif "complete" in allowed_decisions and supports_completion:
            semantic = "complete"
            reason = "semantic evidence and hard facts support goal completion"
        elif "incomplete" in allowed_decisions:
            semantic = "incomplete"
            reason = "semantic evidence remains insufficient for a completion claim"
        elif "semantic_failure" in allowed_decisions:
            semantic = "semantic_failure"
            reason = contradiction_reason(summary)
        else:
            semantic = allowed_decisions[0]
            reason = f"selected the only allowed semantic decision: {semantic}"
        return make_semantic_acceptance_decision(
            summary=summary,
            semantic=semantic,
            reason=reason,
            provider_name=self.provider_name,
            model_name=self.model_name,
        )


class OpenAICompatibleSemanticAcceptanceProvider(SemanticAcceptanceProvider):
    def __init__(self, fallback: SemanticAcceptanceProvider | None = None) -> None:
        self.config = load_openai_compatible_provider_config()
        self.provider_name = self.config.provider_name
        self.model_name = self.config.model_name or "unknown-model"
        self.fallback = fallback or DeterministicSemanticAcceptanceProvider()

    def summarize(self, *, input_payload: dict[str, Any]) -> SemanticEvidenceSummary:
        if not self.config.configured or not model_guidance_enabled("VIBEOS_ENABLE_MODEL_SEMANTIC_ACCEPTANCE"):
            return self._fallback_summary(input_payload=input_payload, error="missing_api_key_or_model_or_guidance_disabled")

        try:
            response = request_json_object(
                config=self.config,
                system_prompt=SEMANTIC_SUMMARY_SYSTEM_PROMPT,
                user_content=json.dumps(input_payload, ensure_ascii=False),
                max_tokens=384,
                purpose="semantic_acceptance",
            )
            parsed = response.parsed_object
            summary_text = parsed.get("summary_text")
            if not isinstance(summary_text, str) or not summary_text.strip():
                raise ValueError("summary_text is required")
            fallback_summary = self.fallback.summarize(input_payload=input_payload)
            structured_findings = dict(fallback_summary.structured_findings)
            structured_findings.update(
                {
                    "supports_completion": bool(parsed.get("supports_completion", False)),
                    "evidence_incomplete": bool(parsed.get("evidence_incomplete", False)),
                    "contradiction_detected": bool(parsed.get("contradiction_detected", False)),
                    "clarification_needed": bool(parsed.get("clarification_needed", False)),
                }
            )
            return SemanticEvidenceSummary(
                semantic_summary_id=make_semantic_summary_id(input_payload),
                understanding_id=fallback_summary.understanding_id,
                candidate_set_id=fallback_summary.candidate_set_id,
                route_decision_id=fallback_summary.route_decision_id,
                route_domain=fallback_summary.route_domain,
                hard_blockers=fallback_summary.hard_blockers,
                verifier_failures=fallback_summary.verifier_failures,
                verifier_incomplete=fallback_summary.verifier_incomplete,
                browser_observation_missing=fallback_summary.browser_observation_missing,
                evidence_sources=fallback_summary.evidence_sources,
                summary_text=summary_text.strip(),
                structured_findings=structured_findings,
                provider_name=self.provider_name,
                model_name=self.model_name,
            )
        except (urllib.error.URLError, TimeoutError, KeyError, ValueError, json.JSONDecodeError) as exc:
            return self._fallback_summary(input_payload=input_payload, error=str(exc))

    def decide(self, *, summary: SemanticEvidenceSummary, allowed_decisions: tuple[SemanticDecision, ...]) -> SemanticAcceptanceDecision:
        if len(allowed_decisions) == 1:
            deterministic = self.fallback.decide(summary=summary, allowed_decisions=allowed_decisions)
            return make_semantic_acceptance_decision(
                summary=summary,
                semantic=deterministic.decision,
                reason=deterministic.reason,
                provider_name=summary.provider_name,
                model_name=summary.model_name,
                parse_valid=summary.parse_valid,
                fallback_used=summary.fallback_used,
                error=summary.error,
            )

        if not self.config.configured or not model_guidance_enabled("VIBEOS_ENABLE_MODEL_SEMANTIC_ACCEPTANCE"):
            return self._fallback_decision(summary=summary, allowed_decisions=allowed_decisions, error="missing_api_key_or_model_or_guidance_disabled")

        request_payload = {
            "semantic_summary_id": summary.semantic_summary_id,
            "summary_text": summary.summary_text,
            "structured_findings": summary.structured_findings,
            "allowed_decisions": list(allowed_decisions),
        }
        try:
            response = request_json_object(
                config=self.config,
                system_prompt=SEMANTIC_DECISION_SYSTEM_PROMPT,
                user_content=json.dumps(request_payload, ensure_ascii=False),
                max_tokens=256,
                purpose="semantic_acceptance",
            )
            parsed = response.parsed_object
            decision = str(parsed.get("decision") or "").strip()
            if decision not in allowed_decisions:
                raise ValueError("semantic decision must be one of the allowed decisions")
            reason = parsed.get("reason")
            if not isinstance(reason, str) or not reason.strip():
                raise ValueError("semantic decision reason is required")
            return make_semantic_acceptance_decision(
                summary=summary,
                semantic=decision,  # type: ignore[arg-type]
                reason=reason.strip(),
                provider_name=self.provider_name,
                model_name=self.model_name,
                parse_valid=True,
                fallback_used=summary.fallback_used,
                error=summary.error,
            )
        except (urllib.error.URLError, TimeoutError, KeyError, ValueError, json.JSONDecodeError) as exc:
            return self._fallback_decision(summary=summary, allowed_decisions=allowed_decisions, error=str(exc))

    def _fallback_summary(self, *, input_payload: dict[str, Any], error: str) -> SemanticEvidenceSummary:
        fallback = self.fallback.summarize(input_payload=input_payload)
        return SemanticEvidenceSummary(
            semantic_summary_id=fallback.semantic_summary_id,
            understanding_id=fallback.understanding_id,
            candidate_set_id=fallback.candidate_set_id,
            route_decision_id=fallback.route_decision_id,
            route_domain=fallback.route_domain,
            hard_blockers=fallback.hard_blockers,
            verifier_failures=fallback.verifier_failures,
            verifier_incomplete=fallback.verifier_incomplete,
            browser_observation_missing=fallback.browser_observation_missing,
            evidence_sources=fallback.evidence_sources,
            summary_text=fallback.summary_text,
            structured_findings=fallback.structured_findings,
            provider_name=self.provider_name,
            model_name=self.model_name,
            parse_valid=False,
            fallback_used=True,
            error=error,
        )

    def _fallback_decision(
        self,
        *,
        summary: SemanticEvidenceSummary,
        allowed_decisions: tuple[SemanticDecision, ...],
        error: str,
    ) -> SemanticAcceptanceDecision:
        fallback = self.fallback.decide(summary=summary, allowed_decisions=allowed_decisions)
        return make_semantic_acceptance_decision(
            summary=summary,
            semantic=fallback.decision,
            reason=fallback.reason,
            provider_name=self.provider_name,
            model_name=self.model_name,
            parse_valid=False,
            fallback_used=True,
            error=error,
        )


def summarize_text(
    *,
    route_domain: str,
    hard_blockers: tuple[str, ...],
    verifier_failures: tuple[str, ...],
    verifier_incomplete: bool,
    browser_observation_missing: bool,
) -> str:
    if hard_blockers:
        return f"{route_domain or 'unknown'} route hit hard blockers: {', '.join(hard_blockers)}"
    if verifier_failures:
        if browser_observation_missing:
            return f"{route_domain or 'unknown'} route lacks required browser observation evidence"
        return f"{route_domain or 'unknown'} route has verifier failures: {', '.join(verifier_failures)}"
    if verifier_incomplete:
        return f"{route_domain or 'unknown'} route completed but evidence is incomplete"
    return f"{route_domain or 'unknown'} route evidence supports completion"


def contradiction_reason(summary: SemanticEvidenceSummary) -> str:
    if summary.hard_blockers:
        return f"hard blocking evidence: {', '.join(summary.hard_blockers)}"
    if summary.verifier_failures:
        return "verifier failures contradict goal completion"
    return "semantic evidence contradicts the requested goal outcome"


def incomplete_reason(summary: SemanticEvidenceSummary) -> str:
    if summary.route_domain == "browser" and summary.browser_observation_missing:
        return "browser route completed mechanically but semantic evidence is incomplete"
    return "execution succeeded but semantic evidence remains incomplete"


def make_semantic_summary_id(input_payload: dict[str, Any]) -> str:
    digest = sha256(f"{input_payload}:{utc_now_iso()}".encode("utf-8")).hexdigest()[:12]
    return f"ssum_{digest}"


def make_semantic_acceptance_decision(
    *,
    summary: SemanticEvidenceSummary,
    semantic: SemanticDecision,
    reason: str,
    provider_name: str,
    model_name: str,
    parse_valid: bool = True,
    fallback_used: bool = False,
    error: str | None = None,
) -> SemanticAcceptanceDecision:
    return SemanticAcceptanceDecision(
        semantic_acceptance_decision_id=make_semantic_acceptance_decision_id(summary, semantic),
        semantic_summary_id=summary.semantic_summary_id,
        understanding_id=summary.understanding_id,
        candidate_set_id=summary.candidate_set_id,
        route_decision_id=summary.route_decision_id,
        decision=semantic,
        acceptance_status=acceptance_status_for_semantic_decision(semantic),
        reason=reason,
        provider_name=provider_name,
        model_name=model_name,
        parse_valid=parse_valid,
        fallback_used=fallback_used,
        error=error,
    )


def make_semantic_acceptance_decision_id(summary: SemanticEvidenceSummary, semantic: SemanticDecision) -> str:
    digest = sha256(f"{summary.semantic_summary_id}:{semantic}:{utc_now_iso()}".encode("utf-8")).hexdigest()[:12]
    return f"sacc_{digest}"


def acceptance_status_for_semantic_decision(semantic: SemanticDecision) -> str:
    if semantic == "complete":
        return "passed"
    if semantic == "semantic_failure":
        return "failed"
    if semantic in {"incomplete", "clarification_needed"}:
        return "indeterminate"
    return "skipped"


def determine_allowed_semantic_decisions(
    *,
    hard_blockers: tuple[str, ...],
    verifier_failures: tuple[str, ...],
    verifier_incomplete: bool,
    browser_observation_missing: bool,
) -> tuple[SemanticDecision, ...]:
    if hard_blockers:
        return ("semantic_failure",)
    if verifier_failures:
        if browser_observation_missing:
            return ("incomplete", "semantic_failure")
        return ("semantic_failure",)
    if verifier_incomplete:
        return ("incomplete",)
    return ("complete", "incomplete")


def model_guidance_enabled(env_name: str) -> bool:
    return env_flag_enabled(env_name)


def str_or_none(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def normalized_browser_context(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {}
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


def normalized_verification_evidence(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, list):
        return []
    normalized: list[dict[str, Any]] = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        details = item.get("details")
        normalized.append(
            {
                "verifier_id": str(item.get("verifier_id") or "unknown"),
                "status": str(item.get("status") or "skipped"),
                "message": str(item.get("message") or ""),
                "details": dict(details) if isinstance(details, dict) else {},
            }
        )
    return normalized
