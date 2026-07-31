from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from hashlib import sha256
import json
import urllib.error

from .capturing_intent import CapturingIntentBroker
from .clarification import (
    ClarificationDecision,
    ClarificationProvider,
    OpenAICompatibleClarificationProvider,
)
from .intent import IntentBroker, OpenAICompatibleIntentBroker, RuleIntentBroker, explicit_contract_intent
from .models import Intent, utc_now_iso
from .nlu import analysis_from_intent, make_provenance
from .provider_client import env_flag_enabled, load_openai_compatible_provider_config, request_json_object
from .task_models import FailureClassification, ReplanDecision, TaskSpan, UtteranceAnalysis
from .task_trace import record_model_io

ALL_UNDERSTANDING_DOMAINS: tuple[str, ...] = (
    "apps",
    "app_interaction",
    "browser",
    "clipboard",
    "media",
    "notification",
    "system_observation",
    "window_management",
)
UNDERSTANDING_SYSTEM_PROMPT = """You are VibeOS's bounded primary understanding analyzer.
Classify the utterance into one structured understanding outcome.
Stay within the host-owned capability and domain boundary hints.
Do not invent a new capability, route, tool, or hidden authority.
Return exactly one JSON object with this schema:
{
  "type": "task",
  "confidence": 0.9,
  "domains": ["browser"],
  "explanation": "short explanation",
  "chat_response": null
}
Return JSON only."""

UNDERSTANDING_TRANSITION_SYSTEM_PROMPT = """You are VibeOS's bounded understanding transition analyzer.
You are given the current structured understanding and a bounded replanning signal.
Return exactly one updated understanding JSON object using the same schema as the primary understanding layer.
You may refine or supersede the understanding only within the host-owned domain and capability boundary hints.
Do not invent a new capability, route, tool, or hidden authority.
Return JSON only."""


@dataclass(frozen=True)
class UnderstandingArtifact:
    understanding_id: str
    utterance: str
    analysis: UtteranceAnalysis
    artifact_role: str = "primary"
    primary_understanding_id: str | None = None
    source_understanding_id: str | None = None
    refinement_id: str | None = None
    supersession_id: str | None = None
    provider_intent: Intent | None = None
    provider_parse_count: int = 0
    provider_cache_hit_count: int = 0
    uncertainty_reasons: tuple[str, ...] = ()
    clarification_question_id: str | None = None
    clarification_provider_name: str | None = None
    clarification_model_name: str | None = None
    clarification_parse_valid: bool = True
    clarification_fallback_used: bool = False
    clarification_error: str | None = None
    analysis_provider_name: str | None = None
    analysis_model_name: str | None = None
    analysis_parse_valid: bool = True
    analysis_fallback_used: bool = False
    analysis_error: str | None = None


@dataclass(frozen=True)
class UnderstandingRefinement:
    refinement_id: str
    primary_understanding_id: str
    previous_understanding_id: str
    refined_understanding_id: str
    reason: str
    changed_fields: tuple[str, ...] = ()


@dataclass(frozen=True)
class UnderstandingSupersession:
    supersession_id: str
    primary_understanding_id: str
    previous_understanding_id: str
    superseding_understanding_id: str
    reason: str
    changed_fields: tuple[str, ...] = ()


@dataclass(frozen=True)
class UnderstandingAnalysisDecision:
    analysis: UtteranceAnalysis
    provider_name: str
    model_name: str
    request_payload: dict[str, object] | None = None
    response_payload: object | None = None
    parse_valid: bool = True
    fallback_used: bool = False
    error: str | None = None


class UnderstandingAnalysisProvider:
    provider_name = "provider"
    model_name = "structured"

    def analyze(self, *, utterance: str, broker: CapturingIntentBroker) -> UnderstandingAnalysisDecision:
        raise NotImplementedError


class UnderstandingTransitionProvider:
    provider_name = "provider"
    model_name = "structured"

    def transition(
        self,
        *,
        understanding: UnderstandingArtifact,
        current_analysis: UtteranceAnalysis,
        decision: ReplanDecision,
        failure: FailureClassification,
    ) -> UnderstandingAnalysisDecision:
        raise NotImplementedError


class OpenAICompatibleUnderstandingAnalysisProvider(UnderstandingAnalysisProvider):
    def __init__(self) -> None:
        self.config = load_openai_compatible_provider_config()
        self.provider_name = self.config.provider_name
        self.model_name = self.config.model_name or "unknown-model"

    def analyze(self, *, utterance: str, broker: CapturingIntentBroker) -> UnderstandingAnalysisDecision:
        explicit_intent = explicit_contract_intent(utterance) if isinstance(broker.wrapped, (OpenAICompatibleIntentBroker, RuleIntentBroker)) else None
        if explicit_intent is not None:
            broker.remember(utterance, explicit_intent)
            return UnderstandingAnalysisDecision(
                analysis=analysis_from_intent(
                    utterance.strip(),
                    explicit_intent,
                    confidence=1.0,
                    provenance_parser="host_explicit_contract",
                ),
                provider_name="host_explicit_contract",
                model_name="deterministic-local",
                request_payload={"utterance": utterance},
                response_payload={
                    "action": explicit_intent.action,
                    "target": explicit_intent.target,
                },
            )
        if not self.config.configured or not understanding_model_guidance_enabled("VIBEOS_ENABLE_MODEL_UNDERSTANDING"):
            explicit_analysis = explicit_broker_understanding(utterance, broker)
            return UnderstandingAnalysisDecision(
                analysis=explicit_analysis or provider_unavailable_understanding(utterance, "model provider is unavailable"),
                provider_name=self.provider_name,
                model_name=self.model_name,
                request_payload={"utterance": utterance},
                response_payload={"analysis": asdict(explicit_analysis)} if explicit_analysis is not None else None,
                parse_valid=explicit_analysis is not None,
                fallback_used=explicit_analysis is not None,
                error="missing_api_key_or_model_or_guidance_disabled",
            )
        host_hint = default_understanding_host_hint(utterance)
        request_payload = build_understanding_request_payload(utterance=utterance, host_hint=host_hint)
        try:
            response = request_json_object(
                config=self.config,
                system_prompt=UNDERSTANDING_SYSTEM_PROMPT,
                user_content=json.dumps(request_payload, ensure_ascii=False),
                max_tokens=512,
                purpose="goal_understanding",
            )
            parsed = response.parsed_object
            analysis = validated_understanding_from_payload(utterance=utterance, payload=parsed, host_hint=host_hint)
            return UnderstandingAnalysisDecision(
                analysis=analysis,
                provider_name=self.provider_name,
                model_name=self.model_name,
                request_payload=response.request_payload,
                response_payload=response.response_payload,
            )
        except (urllib.error.URLError, TimeoutError, KeyError, ValueError, json.JSONDecodeError) as exc:
            explicit_analysis = explicit_broker_understanding(utterance, broker)
            return UnderstandingAnalysisDecision(
                analysis=explicit_analysis or provider_unavailable_understanding(utterance, "model provider is unavailable"),
                provider_name=self.provider_name,
                model_name=self.model_name,
                request_payload=request_payload,
                response_payload={"analysis": asdict(explicit_analysis)} if explicit_analysis is not None else None,
                parse_valid=explicit_analysis is not None,
                fallback_used=explicit_analysis is not None,
                error=str(exc),
            )


class OpenAICompatibleUnderstandingTransitionProvider(UnderstandingTransitionProvider):
    def __init__(self) -> None:
        self.config = load_openai_compatible_provider_config()
        self.provider_name = self.config.provider_name
        self.model_name = self.config.model_name or "unknown-model"

    def transition(
        self,
        *,
        understanding: UnderstandingArtifact,
        current_analysis: UtteranceAnalysis,
        decision: ReplanDecision,
        failure: FailureClassification,
    ) -> UnderstandingAnalysisDecision:
        if not self.config.configured or not understanding_model_guidance_enabled("VIBEOS_ENABLE_MODEL_UNDERSTANDING_TRANSITION"):
            return self._fallback_transition(
                understanding=understanding,
                current_analysis=current_analysis,
                decision=decision,
                failure=failure,
                error="missing_api_key_or_model_or_guidance_disabled",
            )
        host_hint = understanding_transition_host_hint(current_analysis, decision=decision, failure=failure)
        request_payload = build_understanding_transition_request_payload(
            understanding=understanding,
            current_analysis=current_analysis,
            decision=decision,
            failure=failure,
            host_hint=host_hint,
        )
        try:
            response = request_json_object(
                config=self.config,
                system_prompt=UNDERSTANDING_TRANSITION_SYSTEM_PROMPT,
                user_content=json.dumps(request_payload, ensure_ascii=False),
                max_tokens=512,
                purpose="understanding_transition",
            )
            parsed = response.parsed_object
            analysis = validated_understanding_from_payload(
                utterance=understanding.utterance,
                payload=parsed,
                host_hint=host_hint,
                prior_analysis=current_analysis,
            )
            return UnderstandingAnalysisDecision(
                analysis=analysis,
                provider_name=self.provider_name,
                model_name=self.model_name,
                request_payload=response.request_payload,
                response_payload=response.response_payload,
            )
        except (urllib.error.URLError, TimeoutError, KeyError, ValueError, json.JSONDecodeError) as exc:
            return self._fallback_transition(
                understanding=understanding,
                current_analysis=current_analysis,
                decision=decision,
                failure=failure,
                error=str(exc),
            )

    def _fallback_transition(
        self,
        *,
        understanding: UnderstandingArtifact,
        current_analysis: UtteranceAnalysis,
        decision: ReplanDecision,
        failure: FailureClassification,
        error: str,
    ) -> UnderstandingAnalysisDecision:
        analysis = analysis_from_replan_signals(current_analysis, decision=decision, failure=failure) or current_analysis
        host_hint = understanding_transition_host_hint(current_analysis, decision=decision, failure=failure)
        return UnderstandingAnalysisDecision(
            analysis=analysis,
            provider_name=self.provider_name,
            model_name=self.model_name,
            request_payload=build_understanding_transition_request_payload(
                understanding=understanding,
                current_analysis=current_analysis,
                decision=decision,
                failure=failure,
                host_hint=host_hint,
            ),
            response_payload={"analysis": asdict(analysis)},
            parse_valid=False,
            fallback_used=True,
            error=error,
        )


def create_primary_understanding(
    utterance: str,
    intent_broker: IntentBroker | None = None,
    clarification_provider: ClarificationProvider | None = None,
    analysis_provider: UnderstandingAnalysisProvider | None = None,
) -> tuple[UnderstandingArtifact, CapturingIntentBroker]:
    broker = intent_broker if isinstance(intent_broker, CapturingIntentBroker) else CapturingIntentBroker(intent_broker or OpenAICompatibleIntentBroker())
    understanding_provider = analysis_provider or OpenAICompatibleUnderstandingAnalysisProvider()
    analysis_decision = understanding_provider.analyze(utterance=utterance, broker=broker)
    analysis = analysis_decision.analysis
    provider_intent = _provider_intent_for_analysis(utterance, analysis, broker)
    artifact = UnderstandingArtifact(
        understanding_id=make_understanding_id(utterance, analysis),
        utterance=utterance,
        analysis=analysis,
        primary_understanding_id=None,
        provider_intent=provider_intent,
        provider_parse_count=broker.provider_parse_count,
        provider_cache_hit_count=broker.provider_cache_hit_count,
        uncertainty_reasons=infer_uncertainty_reasons(analysis),
        analysis_provider_name=analysis_decision.provider_name,
        analysis_model_name=analysis_decision.model_name,
        analysis_parse_valid=analysis_decision.parse_valid,
        analysis_fallback_used=analysis_decision.fallback_used,
        analysis_error=analysis_decision.error,
    )
    artifact = replace(artifact, primary_understanding_id=artifact.understanding_id)
    record_model_io(
        phase="analysis",
        provider=analysis_decision.provider_name,
        model=analysis_decision.model_name,
        request_payload=analysis_decision.request_payload,
        response_payload=analysis_decision.response_payload,
        normalized_output=asdict(analysis),
        parse_valid=analysis_decision.parse_valid,
        fallback_used=analysis_decision.fallback_used,
        error=analysis_decision.error,
        actor="understanding_classifier",
        call_kind="full_context_understanding",
    )
    clarification_decision: ClarificationDecision | None = None
    if analysis.type == "clarification":
        provider = clarification_provider or OpenAICompatibleClarificationProvider()
        clarification_decision = provider.generate(utterance=utterance, analysis=analysis)
        record_model_io(
            phase="clarification",
            provider=clarification_decision.provider_name,
            model=clarification_decision.model_name,
            request_payload={"utterance": utterance, "analysis": asdict(analysis)},
            response_payload=None,
            normalized_output={
                "clarification_question_id": clarification_decision.clarification_question_id,
                "question": clarification_decision.question,
                "reason": clarification_decision.reason,
            },
            parse_valid=clarification_decision.parse_valid,
            fallback_used=clarification_decision.fallback_used,
            error=clarification_decision.error,
            actor="clarification_generator",
            call_kind="structured_followup",
            consumed_artifacts={
                "understanding_id": artifact.understanding_id,
                "primary_understanding_id": artifact.primary_understanding_id,
                "analysis_type": analysis.type,
                "domains": list(analysis.domains),
            },
        )
        analysis = replace(analysis, chat_response=clarification_decision.question)
    artifact = replace(
        artifact,
        analysis=analysis,
        uncertainty_reasons=infer_uncertainty_reasons(analysis),
        clarification_question_id=clarification_decision.clarification_question_id if clarification_decision else None,
        clarification_provider_name=clarification_decision.provider_name if clarification_decision else None,
        clarification_model_name=clarification_decision.model_name if clarification_decision else None,
        clarification_parse_valid=clarification_decision.parse_valid if clarification_decision else True,
        clarification_fallback_used=clarification_decision.fallback_used if clarification_decision else False,
        clarification_error=clarification_decision.error if clarification_decision else None,
        provider_intent=_provider_intent_for_analysis(utterance, analysis, broker),
        provider_parse_count=broker.provider_parse_count,
        provider_cache_hit_count=broker.provider_cache_hit_count,
    )
    return (
        artifact,
        broker,
    )


def _provider_intent_for_analysis(
    utterance: str,
    analysis: UtteranceAnalysis,
    broker: CapturingIntentBroker,
) -> Intent | None:
    cached = broker.cached_intent(utterance)
    if cached is not None:
        return cached
    if analysis.type not in {"task", "mixed"}:
        return None
    parsed = broker.parse(utterance)
    if parsed.action == "unknown":
        return None
    return parsed


def explicit_broker_understanding(utterance: str, broker: CapturingIntentBroker) -> UtteranceAnalysis | None:
    wrapped = getattr(broker, "wrapped", None)
    if wrapped is None or isinstance(wrapped, OpenAICompatibleIntentBroker):
        return None
    parsed = broker.parse(utterance)
    if parsed.action == "unknown":
        return None
    return analysis_from_intent(utterance.strip(), parsed, confidence=0.88, provenance_parser="explicit_intent_broker")


def provider_unavailable_understanding(utterance: str, message: str) -> UtteranceAnalysis:
    stripped = utterance.strip()
    if not stripped:
        return UtteranceAnalysis(
            utterance=utterance,
            type="clarification",
            confidence=1.0,
            domains=(),
            explanation="The request is empty.",
            task_spans=(),
            provenance=None,
            chat_response="Please provide a task.",
        )
    return UtteranceAnalysis(
        utterance=utterance,
        type="rejected",
        confidence=0.0,
        domains=(),
        explanation=message,
        task_spans=(),
        provenance=None,
        chat_response=None,
    )


def make_understanding_id(utterance: str, analysis: UtteranceAnalysis) -> str:
    digest = sha256(f"{analysis.type}:{analysis.confidence}:{analysis.domains}:{utterance.strip()}:{utc_now_iso()}".encode("utf-8")).hexdigest()[:12]
    return f"und_{digest}"


def infer_uncertainty_reasons(analysis: UtteranceAnalysis) -> tuple[str, ...]:
    reasons: list[str] = []
    if analysis.type == "clarification":
        reasons.append("missing_required_detail")
    if analysis.type == "rejected":
        reasons.append("unsupported_or_unresolved")
    if analysis.confidence < 0.9:
        reasons.append("low_confidence")
    if not analysis.domains and analysis.type not in {"chat", "clarification"}:
        reasons.append("no_domain_selected")
    return tuple(reasons)


def root_understanding_id(understanding: UnderstandingArtifact) -> str:
    return understanding.primary_understanding_id or understanding.understanding_id


def reconcile_understanding_transition(
    understanding: UnderstandingArtifact,
    analysis: UtteranceAnalysis,
    *,
    reason: str,
) -> tuple[UnderstandingArtifact, UnderstandingRefinement | None, UnderstandingSupersession | None]:
    if analysis_semantically_equivalent(understanding.analysis, analysis):
        return understanding, None, None

    primary_understanding_id = root_understanding_id(understanding)
    changed_fields = understanding_changed_fields(understanding.analysis, analysis)
    updated = replace(
        understanding,
        understanding_id=make_understanding_id(understanding.utterance, analysis),
        analysis=analysis,
        primary_understanding_id=primary_understanding_id,
        source_understanding_id=understanding.understanding_id,
    )
    if understanding.analysis.type != analysis.type:
        supersession_id = make_supersession_id(understanding.understanding_id, updated.understanding_id)
        return (
            replace(updated, artifact_role="supersession", refinement_id=None, supersession_id=supersession_id),
            None,
            UnderstandingSupersession(
                supersession_id=supersession_id,
                primary_understanding_id=primary_understanding_id,
                previous_understanding_id=understanding.understanding_id,
                superseding_understanding_id=updated.understanding_id,
                reason=reason,
                changed_fields=changed_fields,
            ),
        )
    refinement_id = make_refinement_id(understanding.understanding_id, updated.understanding_id)
    return (
        replace(updated, artifact_role="refinement", refinement_id=refinement_id, supersession_id=None),
        UnderstandingRefinement(
            refinement_id=refinement_id,
            primary_understanding_id=primary_understanding_id,
            previous_understanding_id=understanding.understanding_id,
            refined_understanding_id=updated.understanding_id,
            reason=reason,
            changed_fields=changed_fields,
        ),
        None,
    )


def reconcile_reinterpreted_understanding(
    previous: UnderstandingArtifact,
    reinterpreted: UnderstandingArtifact,
    *,
    reason: str,
) -> tuple[UnderstandingArtifact, UnderstandingRefinement | None, UnderstandingSupersession | None]:
    primary_understanding_id = root_understanding_id(previous)
    changed_fields = list(understanding_changed_fields(previous.analysis, reinterpreted.analysis))
    if previous.utterance.strip() != reinterpreted.utterance.strip():
        changed_fields.append("utterance")
    if not changed_fields:
        return previous, None, None

    updated = replace(
        reinterpreted,
        primary_understanding_id=primary_understanding_id,
        source_understanding_id=previous.understanding_id,
    )
    if previous.analysis.type != reinterpreted.analysis.type:
        supersession_id = make_supersession_id(previous.understanding_id, updated.understanding_id)
        return (
            replace(updated, artifact_role="supersession", refinement_id=None, supersession_id=supersession_id),
            None,
            UnderstandingSupersession(
                supersession_id=supersession_id,
                primary_understanding_id=primary_understanding_id,
                previous_understanding_id=previous.understanding_id,
                superseding_understanding_id=updated.understanding_id,
                reason=reason,
                changed_fields=tuple(changed_fields),
            ),
        )
    refinement_id = make_refinement_id(previous.understanding_id, updated.understanding_id)
    return (
        replace(updated, artifact_role="refinement", refinement_id=refinement_id, supersession_id=None),
        UnderstandingRefinement(
            refinement_id=refinement_id,
            primary_understanding_id=primary_understanding_id,
            previous_understanding_id=previous.understanding_id,
            refined_understanding_id=updated.understanding_id,
            reason=reason,
            changed_fields=tuple(changed_fields),
        ),
        None,
    )


def analysis_semantically_equivalent(left: UtteranceAnalysis, right: UtteranceAnalysis) -> bool:
    return (
        left.type == right.type
        and tuple(left.domains) == tuple(right.domains)
        and normalized_task_spans(left.task_spans) == normalized_task_spans(right.task_spans)
        and (left.chat_response or "") == (right.chat_response or "")
    )


def understanding_changed_fields(left: UtteranceAnalysis, right: UtteranceAnalysis) -> tuple[str, ...]:
    changed: list[str] = []
    if left.type != right.type:
        changed.append("type")
    if tuple(left.domains) != tuple(right.domains):
        changed.append("domains")
    if normalized_task_spans(left.task_spans) != normalized_task_spans(right.task_spans):
        changed.append("task_spans")
    if (left.chat_response or "") != (right.chat_response or ""):
        changed.append("chat_response")
    if (left.explanation or "").strip() != (right.explanation or "").strip():
        changed.append("explanation")
    return tuple(changed)


def normalized_task_spans(task_spans: tuple[TaskSpan, ...]) -> tuple[tuple[str, int, int, str, float], ...]:
    return tuple((span.text, span.start, span.end, span.domain, span.confidence) for span in task_spans)


def make_refinement_id(previous_understanding_id: str, refined_understanding_id: str) -> str:
    digest = sha256(f"refinement:{previous_understanding_id}:{refined_understanding_id}:{utc_now_iso()}".encode("utf-8")).hexdigest()[:12]
    return f"ref_{digest}"


def make_supersession_id(previous_understanding_id: str, superseding_understanding_id: str) -> str:
    digest = sha256(f"supersession:{previous_understanding_id}:{superseding_understanding_id}:{utc_now_iso()}".encode("utf-8")).hexdigest()[:12]
    return f"sup_{digest}"


def build_understanding_request_payload(*, utterance: str, host_hint: dict[str, object]) -> dict[str, object]:
    return {
        "utterance": utterance,
        "host_hint": host_hint,
        "allowed_types": ["chat", "task", "mixed", "clarification", "rejected"],
        "allowed_domains": list(ALL_UNDERSTANDING_DOMAINS),
    }


def build_understanding_transition_request_payload(
    *,
    understanding: UnderstandingArtifact,
    current_analysis: UtteranceAnalysis,
    decision: ReplanDecision,
    failure: FailureClassification,
    host_hint: dict[str, object],
) -> dict[str, object]:
    return {
        "utterance": understanding.utterance,
        "primary_understanding_id": root_understanding_id(understanding),
        "active_understanding_id": understanding.understanding_id,
        "current_analysis": asdict(current_analysis),
        "host_hint": host_hint,
        "replanning_signal": {
            "action": decision.action,
            "reason": decision.reason,
            "candidate_domain_ids": list(decision.candidate_domain_ids),
            "failure_class": failure.failure_class,
            "failure_message": failure.message,
        },
        "allowed_types": ["chat", "task", "mixed", "clarification", "rejected"],
        "allowed_domains": list(ALL_UNDERSTANDING_DOMAINS),
    }


def analysis_from_replan_signals(
    current_analysis: UtteranceAnalysis,
    *,
    decision: ReplanDecision,
    failure: FailureClassification,
) -> UtteranceAnalysis | None:
    if decision.action == "replan_with_constraints" and decision.candidate_domain_ids:
        next_domain = decision.candidate_domain_ids[0]
        updated_spans = tuple(replace(span, domain=next_domain) for span in current_analysis.task_spans)
        return replace(
            current_analysis,
            domains=tuple(decision.candidate_domain_ids),
            task_spans=updated_spans,
            explanation=decision.reason or failure.message or current_analysis.explanation,
        )
    if decision.action == "ask_user" and failure.failure_class in {"semantic_mismatch", "acceptance_failed", "acceptance_unverified"}:
        return replace(
            current_analysis,
            type="clarification",
            domains=current_analysis.domains,
            explanation=decision.reason or failure.message or current_analysis.explanation,
            task_spans=(),
            chat_response=decision.reason or failure.message or "What detail should I use to continue?",
        )
    return None


def validated_understanding_from_payload(
    *,
    utterance: str,
    payload: dict[str, object],
    host_hint: dict[str, object],
    prior_analysis: UtteranceAnalysis | None = None,
) -> UtteranceAnalysis:
    analysis_type = str(payload.get("type") or "").strip()
    if analysis_type not in {"chat", "task", "mixed", "clarification", "rejected"}:
        raise ValueError("understanding type is invalid")
    default_confidence = float(host_hint.get("default_confidence", prior_analysis.confidence if prior_analysis is not None else 0.5))
    confidence = float(payload.get("confidence", default_confidence))
    if confidence < 0.0 or confidence > 1.0:
        raise ValueError("understanding confidence must be between 0 and 1")
    suggested_domains = list(host_hint.get("suggested_domains", prior_analysis.domains if prior_analysis is not None else ()))
    raw_domains = payload.get("domains", suggested_domains)
    if not isinstance(raw_domains, list):
        raise ValueError("understanding domains must be a list")
    domains = tuple(str(item) for item in raw_domains if str(item) in ALL_UNDERSTANDING_DOMAINS)
    if analysis_type in {"task", "mixed"} and not domains and prior_analysis is not None:
        domains = tuple(str(item) for item in prior_analysis.domains if str(item) in ALL_UNDERSTANDING_DOMAINS)
    explanation = payload.get("explanation")
    if not isinstance(explanation, str) or not explanation.strip():
        raise ValueError("understanding explanation is required")
    chat_response_value = payload.get("chat_response")
    chat_response = None if chat_response_value is None else str(chat_response_value).strip() or None

    task_spans = prior_analysis.task_spans if prior_analysis is not None else ()
    if analysis_type in {"task", "mixed"} and not domains and prior_analysis is None:
        raise ValueError("task or mixed understanding requires at least one allowed domain")
    if analysis_type in {"task", "mixed"} and not task_spans:
        if not domains:
            domains = tuple(str(item) for item in (prior_analysis.domains if prior_analysis is not None else ()) if str(item) in ALL_UNDERSTANDING_DOMAINS)
        if not domains:
            raise ValueError("task or mixed understanding requires a reusable domain before host normalization")
        resolved_domain = domains[0]
        task_spans = (
            TaskSpan(
                id="span_1",
                text=utterance.strip(),
                start=0,
                end=len(utterance.strip()),
                domain=resolved_domain,
                confidence=confidence,
            ),
        )
    elif analysis_type in {"chat", "clarification", "rejected"}:
        task_spans = ()

    if analysis_type == "clarification":
        if not chat_response:
            chat_response = str(host_hint.get("default_clarification_question") or "What detail should I use to continue?")
    else:
        if analysis_type == "mixed" and not chat_response and prior_analysis is not None:
            chat_response = prior_analysis.chat_response
        if analysis_type != "mixed":
            chat_response = None

    return UtteranceAnalysis(
        utterance=utterance,
        type=analysis_type,  # type: ignore[arg-type]
        confidence=confidence,
        domains=domains,
        explanation=explanation.strip(),
        task_spans=task_spans,
        provenance=replace(
            make_provenance(utterance, "provider_understanding_analysis", confidence),
            parser_version="v0.8",
            model="structured-provider",
            schema_version="v0.8",
        ),
        chat_response=chat_response,
    )


def understanding_model_guidance_enabled(env_name: str) -> bool:
    return env_flag_enabled(env_name)


def default_understanding_host_hint(utterance: str) -> dict[str, object]:
    del utterance
    return {
        "suggested_domains": [],
        "default_confidence": 0.5,
        "default_clarification_question": "What detail should I use to continue?",
    }


def understanding_transition_host_hint(
    current_analysis: UtteranceAnalysis,
    *,
    decision: ReplanDecision,
    failure: FailureClassification,
) -> dict[str, object]:
    suggested_domains = list(decision.candidate_domain_ids or current_analysis.domains)
    default_question = decision.reason or failure.message or current_analysis.chat_response or "What detail should I use to continue?"
    return {
        "suggested_domains": [item for item in suggested_domains if item in ALL_UNDERSTANDING_DOMAINS],
        "default_confidence": current_analysis.confidence,
        "default_clarification_question": default_question,
    }
