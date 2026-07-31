from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
import urllib.error
from typing import Literal

from .models import utc_now_iso
from .provider_client import env_flag_enabled, load_openai_compatible_provider_config, request_json_object
from .routes import route_is_satisfied
from .task_models import TaskPlan
from .understanding import UnderstandingArtifact, root_understanding_id


CandidateSelectionAction = Literal["select", "clarify", "unsupported", "blocked"]
ROUTE_SELECTION_SYSTEM_PROMPT = """You are VibeOS's bounded route selector.
Choose exactly one JSON object.
You may only select among the provided candidate ids and allowed actions.
Never invent a new route, capability, tool, or authority.
Schema:
{
  "action": "select",
  "selected_candidate_id": "cand_example",
  "reason": "short explanation"
}
Return JSON only."""


@dataclass(frozen=True)
class CandidateDescriptor:
    candidate_id: str
    plan_id: str
    route_id: str
    domain_id: str
    score: float
    satisfiable: bool
    required_capabilities: tuple[str, ...]
    default_verifier_ids: tuple[str, ...]
    step_ids: tuple[str, ...]


@dataclass(frozen=True)
class CandidateSet:
    candidate_set_id: str
    understanding_id: str
    generated_by: str
    candidates: tuple[CandidateDescriptor, ...]


@dataclass(frozen=True)
class CandidateSelectionDecision:
    route_decision_id: str
    candidate_set_id: str
    understanding_id: str
    action: CandidateSelectionAction
    selected_candidate_id: str | None
    reason: str
    provider_name: str
    model_name: str
    parse_valid: bool = True
    fallback_used: bool = False
    error: str | None = None


def candidate_set_from_payload(payload: dict[str, object]) -> CandidateSet:
    candidates_payload = payload.get("candidates")
    descriptors = (
        tuple(
            CandidateDescriptor(
                candidate_id=str(item["candidate_id"]),
                plan_id=str(item["plan_id"]),
                route_id=str(item["route_id"]),
                domain_id=str(item["domain_id"]),
                score=float(item["score"]),
                satisfiable=bool(item.get("satisfiable", False)),
                required_capabilities=tuple(str(capability) for capability in item.get("required_capabilities", ())),
                default_verifier_ids=tuple(str(verifier) for verifier in item.get("default_verifier_ids", ())),
                step_ids=tuple(str(step_id) for step_id in item.get("step_ids", ())),
            )
            for item in candidates_payload
            if isinstance(item, dict)
        )
        if isinstance(candidates_payload, list)
        else ()
    )
    return CandidateSet(
        candidate_set_id=str(payload["candidate_set_id"]),
        understanding_id=str(payload["understanding_id"]),
        generated_by=str(payload.get("generated_by", "restored_payload")),
        candidates=descriptors,
    )


def candidate_selection_decision_from_payload(payload: dict[str, object]) -> CandidateSelectionDecision:
    selected_candidate_id = payload.get("selected_candidate_id")
    return CandidateSelectionDecision(
        route_decision_id=str(payload["route_decision_id"]),
        candidate_set_id=str(payload["candidate_set_id"]),
        understanding_id=str(payload["understanding_id"]),
        action=str(payload["action"]),
        selected_candidate_id=str(selected_candidate_id) if selected_candidate_id is not None else None,
        reason=str(payload.get("reason", "")),
        provider_name=str(payload.get("provider_name", "restored_payload")),
        model_name=str(payload.get("model_name", "restored-payload")),
        parse_valid=bool(payload.get("parse_valid", True)),
        fallback_used=bool(payload.get("fallback_used", False)),
        error=str(payload["error"]) if payload.get("error") is not None else None,
    )


class CandidateSelectionProvider:
    provider_name = "provider"
    model_name = "structured"

    def decide(self, *, understanding: UnderstandingArtifact, candidate_set: CandidateSet) -> CandidateSelectionDecision:
        raise NotImplementedError


class DeterministicCandidateSelectionProvider(CandidateSelectionProvider):
    provider_name = "host_candidate_selector"
    model_name = "deterministic-local"

    def decide(self, *, understanding: UnderstandingArtifact, candidate_set: CandidateSet) -> CandidateSelectionDecision:
        satisfiable = [item for item in candidate_set.candidates if item.satisfiable]
        if understanding.analysis.type == "clarification":
            return CandidateSelectionDecision(
                route_decision_id=make_route_decision_id(candidate_set, "clarify"),
                candidate_set_id=candidate_set.candidate_set_id,
                understanding_id=candidate_set.understanding_id,
                action="clarify",
                selected_candidate_id=None,
                reason=understanding.analysis.chat_response or understanding.analysis.explanation or "clarification required",
                provider_name=self.provider_name,
                model_name=self.model_name,
            )
        requested_domains = tuple(dict.fromkeys(understanding.analysis.domains))
        if len(requested_domains) > 1:
            return CandidateSelectionDecision(
                route_decision_id=make_route_decision_id(candidate_set, "clarify"),
                candidate_set_id=candidate_set.candidate_set_id,
                understanding_id=candidate_set.understanding_id,
                action="clarify",
                selected_candidate_id=None,
                reason=(
                    "The request spans multiple capability domains, but no single host-generated plan covers the whole goal. "
                    "Please split it into ordered bounded tasks or clarify the required condition and execution order."
                ),
                provider_name=self.provider_name,
                model_name=self.model_name,
            )
        if not satisfiable:
            action: CandidateSelectionAction = "unsupported" if not candidate_set.candidates else "blocked"
            reason = "no candidate satisfies the current capability boundary" if candidate_set.candidates else "no candidate was generated"
            return CandidateSelectionDecision(
                route_decision_id=make_route_decision_id(candidate_set, action),
                candidate_set_id=candidate_set.candidate_set_id,
                understanding_id=candidate_set.understanding_id,
                action=action,
                selected_candidate_id=None,
                reason=reason,
                provider_name=self.provider_name,
                model_name=self.model_name,
            )
        selected = sorted(satisfiable, key=lambda item: (-item.score, item.route_id, item.candidate_id))[0]
        return CandidateSelectionDecision(
            route_decision_id=make_route_decision_id(candidate_set, "select"),
            candidate_set_id=candidate_set.candidate_set_id,
            understanding_id=candidate_set.understanding_id,
            action="select",
            selected_candidate_id=selected.candidate_id,
            reason="selected highest-scoring satisfiable host-generated candidate",
            provider_name=self.provider_name,
            model_name=self.model_name,
        )


class OpenAICompatibleCandidateSelectionProvider(CandidateSelectionProvider):
    def __init__(self, fallback: CandidateSelectionProvider | None = None) -> None:
        self.config = load_openai_compatible_provider_config()
        self.provider_name = self.config.provider_name
        self.model_name = self.config.model_name or "unknown-model"
        self.fallback = fallback or DeterministicCandidateSelectionProvider()

    def decide(self, *, understanding: UnderstandingArtifact, candidate_set: CandidateSet) -> CandidateSelectionDecision:
        request_payload = build_route_selection_request_payload(understanding=understanding, candidate_set=candidate_set)
        host_boundary = self.fallback.decide(understanding=understanding, candidate_set=candidate_set)
        if host_boundary.action == "clarify" and len(set(understanding.analysis.domains)) > 1:
            return host_boundary
        if not self.config.configured or not model_guidance_enabled("VIBEOS_ENABLE_MODEL_ROUTE_SELECTION"):
            return self._fallback(understanding=understanding, candidate_set=candidate_set, error="missing_api_key_or_model_or_guidance_disabled")

        try:
            response = request_json_object(
                config=self.config,
                system_prompt=ROUTE_SELECTION_SYSTEM_PROMPT,
                user_content=json.dumps(request_payload, ensure_ascii=False),
                max_tokens=384,
                purpose="route_selection",
            )
            parsed = response.parsed_object
            allowed_actions = set(str(item) for item in request_payload["allowed_actions"])
            action = str(parsed.get("action") or "").strip()
            if action not in allowed_actions:
                raise ValueError("route selection action must be allowed by the host")
            selected_candidate_id = parsed.get("selected_candidate_id")
            satisfiable_ids = {item.candidate_id for item in candidate_set.candidates if item.satisfiable}
            if action == "select":
                if not isinstance(selected_candidate_id, str) or selected_candidate_id not in satisfiable_ids:
                    raise ValueError("selected_candidate_id must be one of the satisfiable candidates")
            else:
                selected_candidate_id = None
            reason = parsed.get("reason")
            if not isinstance(reason, str) or not reason.strip():
                raise ValueError("route selection reason is required")
            return CandidateSelectionDecision(
                route_decision_id=make_route_decision_id(candidate_set, action),  # type: ignore[arg-type]
                candidate_set_id=candidate_set.candidate_set_id,
                understanding_id=candidate_set.understanding_id,
                action=action,  # type: ignore[arg-type]
                selected_candidate_id=selected_candidate_id,
                reason=reason.strip(),
                provider_name=self.provider_name,
                model_name=self.model_name,
            )
        except (urllib.error.URLError, TimeoutError, KeyError, ValueError, json.JSONDecodeError) as exc:
            return self._fallback(understanding=understanding, candidate_set=candidate_set, error=str(exc))

    def _fallback(self, *, understanding: UnderstandingArtifact, candidate_set: CandidateSet, error: str) -> CandidateSelectionDecision:
        fallback = self.fallback.decide(understanding=understanding, candidate_set=candidate_set)
        return CandidateSelectionDecision(
            route_decision_id=make_route_decision_id(candidate_set, fallback.action),
            candidate_set_id=candidate_set.candidate_set_id,
            understanding_id=candidate_set.understanding_id,
            action=fallback.action,
            selected_candidate_id=fallback.selected_candidate_id,
            reason=fallback.reason,
            provider_name=self.provider_name,
            model_name=self.model_name,
            parse_valid=False,
            fallback_used=True,
            error=error,
        )


def build_candidate_set(
    *,
    understanding: UnderstandingArtifact,
    candidates: tuple[TaskPlan, ...],
    capability_context: set[str],
) -> CandidateSet:
    descriptors = tuple(candidate_descriptor(plan, capability_context) for plan in candidates)
    primary_understanding_id = root_understanding_id(understanding)
    return CandidateSet(
        candidate_set_id=make_candidate_set_id(primary_understanding_id, descriptors),
        understanding_id=primary_understanding_id,
        generated_by="host_candidate_generation",
        candidates=descriptors,
    )


def candidate_descriptor(plan: TaskPlan, capability_context: set[str]) -> CandidateDescriptor:
    route = plan.routes[0]
    return CandidateDescriptor(
        candidate_id=f"cand_{route.id}",
        plan_id=plan.plan_id,
        route_id=route.id,
        domain_id=route.domain_id,
        score=route.score,
        satisfiable=route_is_satisfied(plan, capability_context),
        required_capabilities=route.required_capabilities,
        default_verifier_ids=route.default_verifier_ids,
        step_ids=tuple(step.id for step in plan.steps),
    )


def resolve_selected_plan(
    *,
    decision: CandidateSelectionDecision,
    candidates: tuple[TaskPlan, ...],
    capability_context: set[str],
) -> TaskPlan | None:
    by_id = {f"cand_{plan.routes[0].id}": plan for plan in candidates}
    if decision.action != "select" or not decision.selected_candidate_id:
        return None
    plan = by_id.get(decision.selected_candidate_id)
    if plan is None:
        raise ValueError(f"unknown candidate_id returned by selection layer: {decision.selected_candidate_id}")
    if not route_is_satisfied(plan, capability_context):
        raise ValueError(f"selected candidate is outside the current capability boundary: {decision.selected_candidate_id}")
    return plan


def make_candidate_set_id(understanding_id: str, descriptors: tuple[CandidateDescriptor, ...]) -> str:
    stable = "|".join(f"{item.candidate_id}:{item.route_id}:{item.score}:{item.satisfiable}" for item in descriptors)
    digest = sha256(f"{understanding_id}:{stable}".encode("utf-8")).hexdigest()[:12]
    return f"cset_{digest}"


def make_route_decision_id(candidate_set: CandidateSet, action: CandidateSelectionAction) -> str:
    digest = sha256(f"{candidate_set.candidate_set_id}:{action}:{utc_now_iso()}".encode("utf-8")).hexdigest()[:12]
    return f"rdec_{digest}"


def build_route_selection_request_payload(*, understanding: UnderstandingArtifact, candidate_set: CandidateSet) -> dict[str, object]:
    satisfiable = [item for item in candidate_set.candidates if item.satisfiable]
    if understanding.analysis.type == "clarification":
        allowed_actions: tuple[CandidateSelectionAction, ...] = ("clarify",)
    elif not candidate_set.candidates:
        allowed_actions = ("clarify", "unsupported")
    elif not satisfiable:
        allowed_actions = ("clarify", "blocked", "unsupported")
    else:
        allowed_actions = ("select", "clarify", "blocked")
    return {
        "understanding_id": candidate_set.understanding_id,
        "active_understanding_id": understanding.understanding_id,
        "understanding_artifact_role": understanding.artifact_role,
        "analysis_type": understanding.analysis.type,
        "uncertainty_reasons": list(understanding.uncertainty_reasons),
        "candidate_set_id": candidate_set.candidate_set_id,
        "allowed_actions": list(allowed_actions),
        "candidates": [
            {
                "candidate_id": candidate.candidate_id,
                "route_id": candidate.route_id,
                "domain_id": candidate.domain_id,
                "score": candidate.score,
                "satisfiable": candidate.satisfiable,
                "required_capabilities": list(candidate.required_capabilities),
                "default_verifier_ids": list(candidate.default_verifier_ids),
                "step_ids": list(candidate.step_ids),
            }
            for candidate in candidate_set.candidates
        ],
    }


def model_guidance_enabled(env_name: str) -> bool:
    return env_flag_enabled(env_name)
