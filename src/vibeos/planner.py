from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from hashlib import sha256
from urllib.parse import quote_plus, urlparse

from .assistant_semantics import AssistantIntent
from .candidate_selection import (
    CandidateSelectionDecision,
    CandidateSelectionProvider,
    CandidateSet,
    OpenAICompatibleCandidateSelectionProvider,
    build_candidate_set,
    resolve_selected_plan,
)
from .capabilities import CAPABILITIES
from .clarification import ClarificationProvider
from .config import search_engine_template
from .debug_trace import build_debug_trace, serialize_provider_exchange
from .domain_models import CapabilityExposure, DomainRoutingResult, ObservationReceipt, ObservationRequest
from .domain_registry import DomainRegistry, RouteDefinition, default_domain_registry
from .domain_router import route_domains
from .goal_models import GoalSpec, GoalSynthesisResult
from .goal_synthesizer import GoalSynthesisProvider, GoalSynthesizer, OpenAICompatibleGoalSynthesisProvider, goal_synthesis_payload
from .intent import IntentBroker, OpenAICompatibleIntentBroker, infer_browser_intent_from_open_request
from .models import Intent, utc_now_iso
from .nlu import domain_for_action
from .observation import build_capability_exposure, planner_context_payload, resolve_observation_request
from .routes import available_capabilities as global_available_capabilities
from .routes import score_candidates
from .run_trace import build_run_trace
from .task_trace import record_model_io, record_trace_event
from .task_models import (
    DisplayFields,
    ExpectedState,
    StepPrecondition,
    StepProvenance,
    TaskPlan,
    TaskRoute,
    TaskSpan,
    TaskStep,
    UtteranceAnalysis,
    canonicalize_target_for_action,
)
from .task_validation import validate_plan
from .understanding import (
    CapturingIntentBroker,
    UnderstandingArtifact,
    UnderstandingAnalysisProvider,
    UnderstandingRefinement,
    UnderstandingSupersession,
    create_primary_understanding,
    root_understanding_id,
)
from .verifiers import default_verifier_registry


OPEN_CN_PREFIX = "\u6253\u5f00 "
SEARCH_CN_PREFIX = "\u641c\u7d22 "
LISTEN_CN_PREFIX = "\u6211\u60f3\u542c "
UNAVAILABLE_LOCAL_CAPABILITIES = {"media.search", "media.play", "media.pause"}


@dataclass(frozen=True)
class PlanningArtifacts:
    understanding: UnderstandingArtifact
    analysis: UtteranceAnalysis
    goal_synthesis: GoalSynthesisResult | None
    plan: TaskPlan | None
    candidates: tuple[TaskPlan, ...]
    understanding_refinement: UnderstandingRefinement | None = None
    understanding_supersession: UnderstandingSupersession | None = None
    candidate_set: CandidateSet | None = None
    route_decision: CandidateSelectionDecision | None = None
    domain_routing: DomainRoutingResult | None = None
    observation_request: ObservationRequest | None = None
    observation_receipt: ObservationReceipt | None = None
    capability_exposure: CapabilityExposure | None = None
    trace: object | None = None
    debug_trace: object | None = None


def plan_utterance(
    utterance: str,
    intent_broker: IntentBroker | None = None,
    capability_context: set[str] | None = None,
) -> tuple[UtteranceAnalysis, TaskPlan | None, list[TaskPlan]]:
    artifacts = plan_turn(utterance, intent_broker=intent_broker, capability_context=capability_context)
    return artifacts.analysis, artifacts.plan, list(artifacts.candidates)


def plan_turn(
    utterance: str,
    intent_broker: IntentBroker | None = None,
    capability_context: set[str] | None = None,
    selection_provider: CandidateSelectionProvider | None = None,
    clarification_provider: ClarificationProvider | None = None,
    analysis_provider: UnderstandingAnalysisProvider | None = None,
    goal_synthesis_provider: GoalSynthesisProvider | None = None,
    understanding: UnderstandingArtifact | None = None,
    debug: bool = False,
    candidate_domain_ids_override: tuple[str, ...] | None = None,
    excluded_route_ids: tuple[str, ...] = (),
    excluded_capability_ids: tuple[str, ...] = (),
) -> PlanningArtifacts:
    if understanding is None:
        understanding, broker = create_primary_understanding(
            utterance,
            intent_broker or OpenAICompatibleIntentBroker(),
            clarification_provider=clarification_provider,
            analysis_provider=analysis_provider,
        )
        analysis = understanding.analysis
        record_trace_event(
            phase="analysis",
            event_type="understanding_created",
            status=analysis.type,
            actor="planner",
            data={
                "artifact_type": "understanding",
                "artifact_id": understanding.understanding_id,
                "source_artifact_ids": [],
                "understanding_id": understanding.understanding_id,
                "primary_understanding_id": understanding.primary_understanding_id,
                "artifact_role": understanding.artifact_role,
                "provider_parse_count": understanding.provider_parse_count,
                "provider_cache_hit_count": understanding.provider_cache_hit_count,
                "uncertainty_reasons": list(understanding.uncertainty_reasons),
                "analysis": asdict(analysis),
            },
        )
    else:
        broker = intent_broker if isinstance(intent_broker, CapturingIntentBroker) else CapturingIntentBroker(intent_broker or OpenAICompatibleIntentBroker())
        if understanding.provider_intent is not None:
            broker.remember(utterance, understanding.provider_intent)
        analysis = understanding.analysis
        record_trace_event(
            phase="analysis",
            event_type="understanding_reused",
            status=analysis.type,
            actor="planner",
            data={
                "artifact_type": "understanding",
                "artifact_id": understanding.understanding_id,
                "source_artifact_ids": [understanding.source_understanding_id] if understanding.source_understanding_id else [],
                "understanding_id": understanding.understanding_id,
                "primary_understanding_id": understanding.primary_understanding_id,
                "artifact_role": understanding.artifact_role,
                "analysis": asdict(analysis),
            },
        )
    synthesizer = GoalSynthesizer(
        provider=goal_synthesis_provider or OpenAICompatibleGoalSynthesisProvider(broker)
    )
    goal_synthesis = synthesizer.synthesize(
        utterance,
        analysis,
        understanding_id=understanding.primary_understanding_id or understanding.understanding_id,
    )
    record_trace_event(
        phase="goal_synthesis",
        event_type="goal_synthesized",
        status=goal_synthesis.status,
        actor="planner",
        goal_id=goal_synthesis.goal_spec.goal_id if goal_synthesis.goal_spec is not None else None,
        data=goal_synthesis_payload(goal_synthesis),
    )
    if analysis.type not in {"task", "mixed", "clarification", "rejected"}:
        return PlanningArtifacts(understanding=understanding, analysis=analysis, goal_synthesis=goal_synthesis, plan=None, candidates=())

    routing: DomainRoutingResult | None = None
    observation_request: ObservationRequest | None = None
    observation_receipt: ObservationReceipt | None = None
    capability_exposure: CapabilityExposure | None = None
    candidates: list[TaskPlan] = []
    trace = None
    debug_trace = None
    fallback_reasons: list[str] = []
    route_decision: CandidateSelectionDecision | None = None
    candidate_set: CandidateSet | None = None

    candidate_domain_ids = candidate_domain_ids_for_planning(
        analysis=analysis,
        goal_synthesis=goal_synthesis,
        candidate_domain_ids_override=candidate_domain_ids_override,
    )
    if should_use_domain_pipeline(analysis=analysis, candidate_domain_ids=candidate_domain_ids):
        verifier_registry = default_verifier_registry()
        registry = default_domain_registry(verifier_registry.ids())
        routing = route_domains(analysis, registry, candidate_domain_ids=candidate_domain_ids)
        if routing is not None and routing.observation_request is not None:
            observation_request = routing.observation_request
            observation_receipt = resolve_observation_request(observation_request, registry)
            capability_exposure = build_capability_exposure(registry, routing.active_domain_ids, observation_receipt)
            candidates = build_domain_candidate_plans(
                analysis,
                routing,
                registry,
                planner_context_payload(observation_receipt, capability_exposure),
                broker,
            )
    elif analysis.type in {"task", "mixed"}:
        fallback_reasons.append("no registered candidate domains were available from understanding or goal synthesis")

    candidates = augment_goal_directed_candidates(
        utterance=utterance,
        analysis=analysis,
        goal_synthesis=goal_synthesis,
        candidates=candidates,
    )
    candidates = apply_replanning_constraints(
        candidates,
        excluded_route_ids=excluded_route_ids,
        excluded_capability_ids=excluded_capability_ids,
    )
    record_trace_event(
        phase="planning",
        event_type="candidate_plans_built",
        status="ok",
        actor="planner",
        goal_id=goal_synthesis.goal_spec.goal_id if goal_synthesis.goal_spec is not None else None,
        data={
            "candidate_count": len(candidates),
            "route_ids": [candidate.selected_route_id for candidate in candidates],
            "excluded_route_ids": list(excluded_route_ids),
            "excluded_capability_ids": list(excluded_capability_ids),
        },
    )

    available = default_capability_context() if capability_context is None else capability_context
    scored_candidates = tuple(score_candidates(candidates, available))
    candidate_set = build_candidate_set(understanding=understanding, candidates=scored_candidates, capability_context=available)
    primary_understanding_id = root_understanding_id(understanding)
    record_trace_event(
        phase="planning",
        event_type="candidate_set_generated",
        status="ok",
        actor="planner",
        goal_id=goal_synthesis.goal_spec.goal_id if goal_synthesis.goal_spec is not None else None,
        data={
            "understanding_id": primary_understanding_id,
            "active_understanding_id": understanding.understanding_id,
            "candidate_set_id": candidate_set.candidate_set_id,
            "candidate_ids": [candidate.candidate_id for candidate in candidate_set.candidates],
        },
    )
    selector = selection_provider or OpenAICompatibleCandidateSelectionProvider()
    route_decision = selector.decide(understanding=understanding, candidate_set=candidate_set)
    record_model_io(
        phase="planning",
        provider=route_decision.provider_name,
        model=route_decision.model_name,
        request_payload={
            "understanding_id": primary_understanding_id,
            "active_understanding_id": understanding.understanding_id,
            "analysis_type": analysis.type,
            "candidate_set_id": candidate_set.candidate_set_id,
            "candidate_ids": [candidate.candidate_id for candidate in candidate_set.candidates],
        },
        response_payload=None,
        normalized_output=asdict(route_decision),
        actor="route_selector",
        call_kind="structured_followup",
        consumed_artifacts={
            "understanding_id": primary_understanding_id,
            "active_understanding_id": understanding.understanding_id,
            "candidate_set_id": candidate_set.candidate_set_id,
        },
    )
    selected = resolve_selected_plan(decision=route_decision, candidates=scored_candidates, capability_context=available)
    validation = validate_plan(selected) if selected is not None else None
    record_trace_event(
        phase="planning",
        event_type="route_selected" if selected is not None else "planning_unresolved",
        status="ok" if selected is not None and (validation is None or validation.ok) else ("invalid" if validation is not None and not validation.ok else "rejected"),
        actor="planner",
        goal_id=goal_synthesis.goal_spec.goal_id if goal_synthesis.goal_spec is not None else None,
        plan_id=selected.plan_id if selected is not None else None,
        data={
            "understanding_id": primary_understanding_id,
            "active_understanding_id": understanding.understanding_id,
            "candidate_set_id": candidate_set.candidate_set_id,
            "route_decision_id": route_decision.route_decision_id,
            "candidate_count": len(scored_candidates),
            "decision_action": route_decision.action,
            "selected_candidate_id": route_decision.selected_candidate_id,
            "selected_route_id": selected.selected_route_id if selected is not None else None,
            "validation_ok": validation.ok if validation is not None else None,
            "validation_errors": list(validation.errors) if validation is not None else [],
        },
    )
    candidate_selection = {
        "understanding_id": primary_understanding_id,
        "active_understanding_id": understanding.understanding_id,
        "candidate_set_id": candidate_set.candidate_set_id,
        "route_decision_id": route_decision.route_decision_id,
        "candidate_count": len(scored_candidates),
        "decision_action": route_decision.action,
        "selected_candidate_id": route_decision.selected_candidate_id,
        "selected_route_id": selected.selected_route_id if selected is not None else "",
        "selected_plan_id": selected.plan_id if selected is not None else "",
    }
    debug_trace = build_debug_trace(
        utterance_analysis=asdict(analysis),
        goal_synthesis=goal_synthesis_payload(goal_synthesis),
        model_exchange=(serialize_provider_exchange(asdict(goal_synthesis.exchange), include_raw=debug),),
        synthesis_constraints=goal_synthesis.goal_spec.constraints if goal_synthesis.goal_spec else (),
        route_competition=[candidate_summary(candidate) for candidate in scored_candidates],
        fallback_reasons=tuple(fallback_reasons),
    )
    trace = build_run_trace(
        utterance_analysis=analysis,
        goal_synthesis=goal_synthesis.goal_spec if goal_synthesis.goal_spec is not None else goal_synthesis_payload(goal_synthesis),
        domain_routing=routing,
        observation_request=observation_request,
        observation_receipt=observation_receipt,
        capability_exposure=capability_exposure,
        candidate_plan_selection=candidate_selection,
        selected_route=selected.routes[0] if selected and selected.routes else None,
        validation=validation,
        debug_trace_id="debug_trace_v0_5",
    )
    return PlanningArtifacts(
        understanding=understanding,
        analysis=analysis,
        goal_synthesis=goal_synthesis,
        plan=selected,
        candidates=scored_candidates,
        candidate_set=candidate_set,
        route_decision=route_decision,
        domain_routing=routing,
        observation_request=observation_request,
        observation_receipt=observation_receipt,
        capability_exposure=capability_exposure,
        trace=trace,
        debug_trace=debug_trace,
    )


def apply_replanning_constraints(
    candidates: list[TaskPlan],
    *,
    excluded_route_ids: tuple[str, ...],
    excluded_capability_ids: tuple[str, ...],
) -> list[TaskPlan]:
    blocked_routes = set(excluded_route_ids)
    blocked_capabilities = set(excluded_capability_ids)
    if not blocked_routes and not blocked_capabilities:
        return candidates
    filtered: list[TaskPlan] = []
    for candidate in candidates:
        if candidate.selected_route_id in blocked_routes:
            continue
        if any(step.capability_id in blocked_capabilities for step in candidate.steps):
            continue
        filtered.append(candidate)
    return filtered


def augment_goal_directed_candidates(
    *,
    utterance: str,
    analysis: UtteranceAnalysis,
    goal_synthesis: GoalSynthesisResult | None,
    candidates: list[TaskPlan],
) -> list[TaskPlan]:
    goal_spec = goal_synthesis.goal_spec if goal_synthesis is not None else None
    if goal_spec is None or goal_spec.assistant_intent is None:
        return candidates
    filtered = filter_goal_conflicting_candidates(goal_spec, candidates)
    filtered.extend(goal_directed_candidate_plans(utterance=utterance, analysis=analysis, goal_spec=goal_spec))
    return dedupe_candidates(filtered)


def filter_goal_conflicting_candidates(goal_spec: GoalSpec, candidates: list[TaskPlan]) -> list[TaskPlan]:
    assistant_intent = goal_spec.assistant_intent
    if assistant_intent is None:
        return candidates
    blocked_route_ids: set[str] = set()
    if assistant_intent.objective_kind == "open_named_website":
        blocked_route_ids.update({"browser_open_url_route", "browser_search_web_route", "browser_site_search_route"})
    if not blocked_route_ids:
        return candidates
    return [candidate for candidate in candidates if candidate.selected_route_id not in blocked_route_ids]


def goal_directed_candidate_plans(
    *,
    utterance: str,
    analysis: UtteranceAnalysis,
    goal_spec: GoalSpec,
) -> list[TaskPlan]:
    assistant_intent = goal_spec.assistant_intent
    if assistant_intent is None:
        return []
    if assistant_intent.objective_kind == "open_named_website":
        return build_named_website_candidates(utterance=utterance, analysis=analysis, assistant_intent=assistant_intent)
    return []


def build_named_website_candidates(
    *,
    utterance: str,
    analysis: UtteranceAnalysis,
    assistant_intent: AssistantIntent,
) -> list[TaskPlan]:
    target_name = str(assistant_intent.target.display_name or assistant_intent.target.query_text or "").strip()
    if not target_name:
        return []
    span = primary_task_span(analysis)
    direct_route = TaskRoute(
        id="browser_named_direct_open_route",
        score=0.94,
        domain_id="browser",
        display=DisplayFields(explanation="Resolve and open the named website target directly through a host-owned browser route."),
        score_inputs={},
        required_capabilities=("browser.open_named_target",),
        default_verifier_ids=("browser_goal_page_identity",),
    )
    direct_step = TaskStep(
        id="browser_open_named_target",
        action="browser.open_named_target",
        capability_id="browser.open_named_target",
        target={"name": target_name, "resolution_mode": "direct"},
        depends_on=(),
        risk_level=CAPABILITIES["browser.open_named_target"].risk_level,
        expected_state=ExpectedState(kind="named_site_open_requested", fields={"name": target_name}),
        preconditions=(StepPrecondition(kind="capability_available", capability_id="browser.open_named_target"),),
        provenance=StepProvenance(source_span_id=span.id, planner="v0.8_goal_directed_planner"),
    )
    search_route = TaskRoute(
        id="browser_search_followup_route",
        score=0.89,
        domain_id="browser",
        display=DisplayFields(explanation="Search for the named website and continue to the resolved official result before accepting completion."),
        score_inputs={},
        required_capabilities=("browser.search_web",),
        default_verifier_ids=("browser_goal_page_identity",),
    )
    search_step = TaskStep(
        id="browser_search_followup",
        action="browser.search_web",
        capability_id="browser.search_web",
        target={"query": target_name, "follow_search_result": True, "named_target": target_name},
        depends_on=(),
        risk_level=CAPABILITIES["browser.search_web"].risk_level,
        expected_state=ExpectedState(kind="named_site_open_requested", fields={"name": target_name}),
        preconditions=(StepPrecondition(kind="capability_available", capability_id="browser.search_web"),),
        provenance=StepProvenance(source_span_id=span.id, planner="v0.8_goal_directed_planner"),
    )
    return [
        TaskPlan(
            schema_version="v0.5",
            plan_id=make_plan_id(utterance, direct_route.id),
            utterance=utterance,
            display=DisplayFields(
                goal=f"open {target_name}",
                explanation="Resolve the named website target directly before using weaker browser strategies.",
            ),
            selected_route_id=direct_route.id,
            routes=(direct_route,),
            steps=(direct_step,),
            provenance={
                "planner": "v0.8_goal_directed_planner",
                "planner_version": "v0.8",
                "source_span_id": span.id,
                "domain_id": "browser",
                "assistant_intent": assistant_intent.objective_kind,
            },
        ),
        TaskPlan(
            schema_version="v0.5",
            plan_id=make_plan_id(utterance, search_route.id),
            utterance=utterance,
            display=DisplayFields(
                goal=f"search and continue to {target_name}",
                explanation="Use a bounded browser search follow-up that resolves to the official result before completion.",
            ),
            selected_route_id=search_route.id,
            routes=(search_route,),
            steps=(search_step,),
            provenance={
                "planner": "v0.8_goal_directed_planner",
                "planner_version": "v0.8",
                "source_span_id": span.id,
                "domain_id": "browser",
                "assistant_intent": assistant_intent.objective_kind,
                "interaction_surface": "structured",
            },
        ),
    ]


def primary_task_span(analysis: UtteranceAnalysis) -> TaskSpan:
    if analysis.task_spans:
        return analysis.task_spans[0]
    return TaskSpan(id="span_1", text=analysis.utterance, start=0, end=len(analysis.utterance), domain=(analysis.domains[0] if analysis.domains else "browser"), confidence=analysis.confidence)


def should_use_domain_pipeline(*, analysis: UtteranceAnalysis, candidate_domain_ids: tuple[str, ...]) -> bool:
    if analysis.type not in {"task", "mixed"}:
        return False
    return bool(candidate_domain_ids)


def candidate_domain_ids_for_planning(
    *,
    analysis: UtteranceAnalysis,
    goal_synthesis: GoalSynthesisResult | None,
    candidate_domain_ids_override: tuple[str, ...] | None,
) -> tuple[str, ...]:
    if candidate_domain_ids_override:
        return tuple(dict.fromkeys(domain_id for domain_id in candidate_domain_ids_override if domain_id))
    domains: list[str] = []
    goal_spec = goal_synthesis.goal_spec if goal_synthesis is not None else None
    if goal_spec is not None:
        assistant_intent = goal_spec.assistant_intent
        if assistant_intent is not None:
            domains.extend(domain_id for domain_id in assistant_intent.preferred_domains if domain_id)
        required_domains = _single_required_domain(goal_spec.required_capability_ids)
        if required_domains is not None:
            domains.append(required_domains)
        domains.extend(domain_id for domain_id in goal_spec.candidate_domain_ids if domain_id)
    domains.extend(domain_id for domain_id in analysis.domains if domain_id)
    return tuple(dict.fromkeys(domains))


def _single_required_domain(capability_ids: tuple[str, ...]) -> str | None:
    domains = tuple(dict.fromkeys(domain_for_action(capability_id) for capability_id in capability_ids if capability_id))
    if len(domains) != 1:
        return None
    return domains[0]


def default_capability_context() -> set[str]:
    return set(global_available_capabilities()) - UNAVAILABLE_LOCAL_CAPABILITIES


def structured_capability_intent(text: str, intent_broker: IntentBroker | None = None) -> Intent:
    if intent_broker is not None:
        return intent_broker.parse(text)
    return Intent.unknown("structured route building requires an intent broker")


def build_domain_candidate_plans(
    analysis: UtteranceAnalysis,
    routing: DomainRoutingResult,
    registry: DomainRegistry,
    context_payload: dict[str, object],
    intent_broker: IntentBroker | None = None,
) -> list[TaskPlan]:
    del context_payload
    candidates: list[TaskPlan] = []
    for span in analysis.task_spans:
        for route_definition in registry.routes_for_domains(routing.active_domain_ids):
            builder = ROUTE_BUILDERS.get(route_definition.builder_name)
            if builder is None:
                continue
            candidate = builder(analysis.utterance, span, route_definition, intent_broker)
            if candidate is not None:
                candidates.append(candidate)
    return dedupe_candidates(candidates)


def dedupe_candidates(candidates: list[TaskPlan]) -> list[TaskPlan]:
    deduped: dict[str, TaskPlan] = {}
    for candidate in candidates:
        deduped[candidate.selected_route_id] = candidate
    return list(deduped.values())


def normalize_intent_to_task_plan(
    intent: Intent,
    utterance: str,
    analysis: UtteranceAnalysis | None = None,
    span: TaskSpan | None = None,
) -> TaskPlan:
    route_id = f"legacy_{intent.action.replace('.', '_')}_route"
    step_id = f"step_{intent.action.replace('.', '_')}"
    capability = CAPABILITIES[intent.action]
    span_id = span.id if span is not None else (analysis.task_spans[0].id if analysis and analysis.task_spans else "span_1")
    display = DisplayFields(goal=display_goal_for_intent(intent), explanation=intent.reason or capability.reason, assumptions=())
    route = TaskRoute(
        id=route_id,
        score=0.0,
        domain_id=span.domain if span is not None else domain_for_action(intent.action),
        display=DisplayFields(explanation="Normalized from the legacy single-intent pipeline."),
        score_inputs={},
        required_capabilities=(intent.action,),
        default_verifier_ids=(),
    )
    canonical_target = canonicalize_target_for_action(intent.action, intent.target)
    step = TaskStep(
        id=step_id,
        action=intent.action,
        capability_id=intent.action,
        target=canonical_target,
        depends_on=(),
        risk_level=capability.risk_level,
        parallel_group="g1",
        expected_state=expected_state_for_intent(intent),
        preconditions=(StepPrecondition(kind="capability_available", capability_id=intent.action),),
        provenance=StepProvenance(source_span_id=span_id, planner="legacy_intent_normalizer"),
    )
    return TaskPlan(
        schema_version="v0.3",
        plan_id=make_plan_id(utterance, route_id),
        utterance=utterance,
        display=display,
        status="planned",
        source_span_id=span_id,
        selected_route_id=route.id,
        routes=(route,),
        steps=(step,),
        provenance={"planner": "legacy_intent_normalizer", "planner_version": "v0.3-phase2", "source_span_id": span_id},
        needs_user_input=False,
    )


def media_candidate_plans(utterance: str, span: TaskSpan) -> list[TaskPlan]:
    query = extract_media_query(span.text)
    if not query:
        return []
    return [music_app_media_plan(utterance, span, query), browser_media_plan(utterance, span, query)]


def music_app_media_plan(utterance: str, span: TaskSpan, query: str) -> TaskPlan:
    route = TaskRoute(
        id="media_play_route",
        score=0.0,
        domain_id="media",
        display=DisplayFields(explanation="A dedicated media route best matches a listening request."),
        score_inputs={},
        required_capabilities=("app.open", "media.search", "media.play"),
        default_verifier_ids=("media_playback_state_available",),
    )
    steps = (
        TaskStep(
            id="open_music_app",
            action="app.open",
            capability_id="app.open",
            target={"name": "music"},
            depends_on=(),
            risk_level="L1",
            parallel_group="g1",
            expected_state=ExpectedState(kind="app_opened_or_focused", fields={"app": "music"}),
            preconditions=(StepPrecondition(kind="capability_available", capability_id="app.open"),),
            provenance=StepProvenance(source_span_id=span.id, planner="v0.5_media_route"),
        ),
        TaskStep(
            id="search_track",
            action="media.search",
            capability_id="media.search",
            target={"query": query},
            depends_on=("open_music_app",),
            risk_level="L1",
            expected_state=ExpectedState(kind="search_results_available", fields={"query": query}),
            preconditions=(StepPrecondition(kind="capability_available", capability_id="media.search"),),
            provenance=StepProvenance(source_span_id=span.id, planner="v0.5_media_route"),
        ),
        TaskStep(
            id="play_track",
            action="media.play",
            capability_id="media.play",
            target={"query": query, "selection": "best_match"},
            depends_on=("search_track",),
            risk_level="L1",
            expected_state=ExpectedState(kind="media_playing", fields={"query": query}),
            preconditions=(StepPrecondition(kind="capability_available", capability_id="media.play"),),
            provenance=StepProvenance(source_span_id=span.id, planner="v0.5_media_route"),
        ),
    )
    return TaskPlan(
        schema_version="v0.5",
        plan_id=make_plan_id(utterance, route.id),
        utterance=utterance,
        display=DisplayFields(
            goal=f"play media matching {query}",
            explanation="Use a dedicated media route when local media execution capabilities are available.",
            assumptions=(f"{query} is treated as a media search query",),
        ),
        status="planned",
        source_span_id=span.id,
        selected_route_id=route.id,
        routes=(route,),
        steps=steps,
        provenance={
            "planner": "v0.5_domain_planner",
            "planner_version": "v0.5",
            "source_span_id": span.id,
            "domain_id": "media",
            "fallback_route_id": "browser_music_search_route",
            "dedicated_execution_status": "unavailable_on_local_host",
        },
        needs_user_input=False,
    )


def browser_media_plan(utterance: str, span: TaskSpan, query: str) -> TaskPlan:
    route = TaskRoute(
        id="browser_music_search_route",
        score=0.0,
        domain_id="browser",
        display=DisplayFields(explanation="Browser search is an explicit fallback when dedicated media execution is unavailable."),
        score_inputs={},
        required_capabilities=("browser.open_site_search",),
        default_verifier_ids=("browser_search_route_completed",),
    )
    steps = (
        TaskStep(
            id="open_media_search_uri",
            action="browser.open_site_search",
            capability_id="browser.open_site_search",
            target={"site": "youtube.com", "query": query},
            depends_on=(),
            risk_level="L1",
            expected_state=ExpectedState(kind="search_results_available", fields={"query": query}),
            preconditions=(StepPrecondition(kind="capability_available", capability_id="browser.open_site_search"),),
            provenance=StepProvenance(source_span_id=span.id, planner="v0.5_browser_fallback"),
        ),
    )
    return TaskPlan(
        schema_version="v0.5",
        plan_id=make_plan_id(utterance, route.id),
        utterance=utterance,
        display=DisplayFields(
            goal=f"find media matching {query} in a browser",
            explanation="Use the browser fallback after the media domain records that dedicated playback execution is unavailable.",
            assumptions=(f"{query} is treated as a media search query",),
        ),
        status="planned",
        source_span_id=span.id,
        selected_route_id=route.id,
        routes=(route,),
        steps=steps,
        provenance={
            "planner": "v0.5_domain_planner",
            "planner_version": "v0.5",
            "source_span_id": span.id,
            "domain_id": "browser",
            "fallback_from_domain": "media",
            "fallback_reason": "dedicated media execution unavailable on local host",
        },
        needs_user_input=False,
    )


def build_browser_open_url_plan(
    utterance: str,
    span: TaskSpan,
    route_definition: RouteDefinition,
    intent_broker: IntentBroker | None = None,
) -> TaskPlan | None:
    intent = structured_capability_intent(span.text, intent_broker)
    uri = ""
    if intent.action == "browser.open_url":
        uri = str(canonicalize_target_for_action(intent.action, intent.target).get("uri") or "")
    if not uri:
        uri = extract_browser_url(span.text)
    if not uri:
        return None
    route = task_route_from_definition(route_definition, "Open a URL in the browser.")
    step = TaskStep(
        id="browser_open_url",
        action="browser.open_url",
        capability_id="browser.open_url",
        target={"uri": uri},
        depends_on=(),
        risk_level=CAPABILITIES["browser.open_url"].risk_level,
        expected_state=ExpectedState(kind="uri_open_requested", fields={"uri": uri}),
        preconditions=(StepPrecondition(kind="capability_available", capability_id="browser.open_url"),),
        provenance=StepProvenance(source_span_id=span.id, planner="v0.5_browser_route"),
    )
    return TaskPlan(
        schema_version="v0.5",
        plan_id=make_plan_id(utterance, route.id),
        utterance=utterance,
        display=DisplayFields(goal="open a URL", explanation="Use browser-domain URL semantics instead of the generic portal primitive."),
        selected_route_id=route.id,
        routes=(route,),
        steps=(step,),
        provenance={"planner": "v0.5_domain_planner", "planner_version": "v0.5", "source_span_id": span.id, "domain_id": "browser"},
    )


def build_browser_search_web_plan(
    utterance: str,
    span: TaskSpan,
    route_definition: RouteDefinition,
    intent_broker: IntentBroker | None = None,
) -> TaskPlan | None:
    intent = structured_capability_intent(span.text, intent_broker)
    query = ""
    if intent.action == "browser.search_web":
        query = str(canonicalize_target_for_action(intent.action, intent.target).get("query") or "")
    if not query:
        query = extract_browser_search_query(span.text)
    if not query:
        return None
    route = task_route_from_definition(route_definition, "Search the web in the browser.")
    step = TaskStep(
        id="browser_search_web",
        action="browser.search_web",
        capability_id="browser.search_web",
        target={"query": query},
        depends_on=(),
        risk_level=CAPABILITIES["browser.search_web"].risk_level,
        expected_state=ExpectedState(kind="search_results_available", fields={"query": query}),
        preconditions=(StepPrecondition(kind="capability_available", capability_id="browser.search_web"),),
        provenance=StepProvenance(source_span_id=span.id, planner="v0.5_browser_route"),
    )
    return TaskPlan(
        schema_version="v0.5",
        plan_id=make_plan_id(utterance, route.id),
        utterance=utterance,
        display=DisplayFields(goal="search the web", explanation="Use browser-domain search semantics with narrowed route exposure."),
        selected_route_id=route.id,
        routes=(route,),
        steps=(step,),
        provenance={"planner": "v0.5_domain_planner", "planner_version": "v0.5", "source_span_id": span.id, "domain_id": "browser"},
    )


def build_browser_site_search_plan(
    utterance: str,
    span: TaskSpan,
    route_definition: RouteDefinition,
    intent_broker: IntentBroker | None = None,
) -> TaskPlan | None:
    intent = structured_capability_intent(span.text, intent_broker)
    site = ""
    query = ""
    if intent.action == "browser.open_site_search":
        target = canonicalize_target_for_action(intent.action, intent.target)
        site = str(target.get("site") or "")
        query = str(target.get("query") or "")
    if not site or not query:
        site_search = extract_site_search(span.text)
        if site_search is None:
            return None
        site, query = site_search
    route = task_route_from_definition(route_definition, "Search a site in the browser.")
    step = TaskStep(
        id="browser_site_search",
        action="browser.open_site_search",
        capability_id="browser.open_site_search",
        target={"site": site, "query": query},
        depends_on=(),
        risk_level=CAPABILITIES["browser.open_site_search"].risk_level,
        expected_state=ExpectedState(kind="search_results_available", fields={"query": query}),
        preconditions=(StepPrecondition(kind="capability_available", capability_id="browser.open_site_search"),),
        provenance=StepProvenance(source_span_id=span.id, planner="v0.5_browser_route"),
    )
    return TaskPlan(
        schema_version="v0.5",
        plan_id=make_plan_id(utterance, route.id),
        utterance=utterance,
        display=DisplayFields(goal=f"search {site}", explanation="Use site-scoped browser search semantics."),
        selected_route_id=route.id,
        routes=(route,),
        steps=(step,),
        provenance={"planner": "v0.5_domain_planner", "planner_version": "v0.5", "source_span_id": span.id, "domain_id": "browser"},
    )


def build_media_play_plan(
    utterance: str,
    span: TaskSpan,
    route_definition: RouteDefinition,
    intent_broker: IntentBroker | None = None,
) -> TaskPlan | None:
    del intent_broker
    query = extract_media_query(span.text)
    if not query:
        return None
    del route_definition
    return music_app_media_plan(utterance, span, query)


def build_media_search_plan(
    utterance: str,
    span: TaskSpan,
    route_definition: RouteDefinition,
    intent_broker: IntentBroker | None = None,
) -> TaskPlan | None:
    del intent_broker
    query = extract_media_search_query(span.text)
    if not query:
        return None
    route = task_route_from_definition(route_definition, "Search media through the explicit media domain.")
    step = TaskStep(
        id="media_search",
        action="media.search",
        capability_id="media.search",
        target={"query": query},
        depends_on=(),
        risk_level=CAPABILITIES["media.search"].risk_level,
        expected_state=ExpectedState(kind="search_results_available", fields={"query": query}),
        preconditions=(StepPrecondition(kind="capability_available", capability_id="media.search"),),
        provenance=StepProvenance(source_span_id=span.id, planner="v0.5_media_route"),
    )
    return make_explicit_plan(utterance, span, route, (step,), goal="search media", explanation="Use the explicit media domain even when dedicated execution is unavailable locally.")


def build_media_pause_plan(
    utterance: str,
    span: TaskSpan,
    route_definition: RouteDefinition,
    intent_broker: IntentBroker | None = None,
) -> TaskPlan | None:
    del intent_broker
    if not is_media_pause_request(span.text):
        return None
    route = task_route_from_definition(route_definition, "Pause media through the explicit media domain.")
    step = TaskStep(
        id="media_pause",
        action="media.pause",
        capability_id="media.pause",
        target={},
        depends_on=(),
        risk_level=CAPABILITIES["media.pause"].risk_level,
        expected_state=ExpectedState(kind="media_playing", fields={"query": "pause"}),
        preconditions=(StepPrecondition(kind="capability_available", capability_id="media.pause"),),
        provenance=StepProvenance(source_span_id=span.id, planner="v0.5_media_route"),
    )
    return make_explicit_plan(utterance, span, route, (step,), goal="pause media", explanation="Use the explicit media domain for pause requests, even when the local host cannot execute the adapter.")


def build_browser_media_fallback_plan(
    utterance: str,
    span: TaskSpan,
    route_definition: RouteDefinition,
    intent_broker: IntentBroker | None = None,
) -> TaskPlan | None:
    del intent_broker
    query = extract_media_query(span.text)
    if not query:
        return None
    del route_definition
    return browser_media_plan(utterance, span, query)


def build_apps_list_plan(
    utterance: str,
    span: TaskSpan,
    route_definition: RouteDefinition,
    intent_broker: IntentBroker | None = None,
) -> TaskPlan | None:
    intent = structured_capability_intent(span.text, intent_broker)
    if intent.action != "app.list":
        return None
    route = task_route_from_definition(route_definition, "List installed applications through the apps domain.")
    step = TaskStep(
        id="apps_list",
        action="app.list",
        capability_id="app.list",
        target={},
        depends_on=(),
        risk_level=CAPABILITIES["app.list"].risk_level,
        expected_state=ExpectedState(kind="app_list_requested"),
        preconditions=(StepPrecondition(kind="capability_available", capability_id="app.list"),),
        provenance=StepProvenance(source_span_id=span.id, planner="v0.5_apps_route"),
    )
    return make_explicit_plan(utterance, span, route, (step,), goal="list applications", explanation="Use the explicit apps domain for application inventory.")


def build_apps_open_plan(
    utterance: str,
    span: TaskSpan,
    route_definition: RouteDefinition,
    intent_broker: IntentBroker | None = None,
) -> TaskPlan | None:
    intent = structured_capability_intent(span.text, intent_broker)
    if intent.action != "app.open":
        return None
    target = canonicalize_target_for_action(intent.action, intent.target)
    if "://" in str(target.get("name") or ""):
        return None
    route = task_route_from_definition(route_definition, "Open an application through the apps domain.")
    step = TaskStep(
        id="apps_open",
        action="app.open",
        capability_id="app.open",
        target=target,
        depends_on=(),
        risk_level=CAPABILITIES["app.open"].risk_level,
        expected_state=ExpectedState(kind="app_opened_or_focused", fields={"app": str(target.get("name") or "")}),
        preconditions=(StepPrecondition(kind="capability_available", capability_id="app.open"),),
        provenance=StepProvenance(source_span_id=span.id, planner="v0.5_apps_route"),
    )
    return make_explicit_plan(utterance, span, route, (step,), goal="open an application", explanation="Use the explicit apps domain for application launch or focus.")


def build_app_structured_search_plan(
    utterance: str,
    span: TaskSpan,
    route_definition: RouteDefinition,
    intent_broker: IntentBroker | None = None,
) -> TaskPlan | None:
    return build_app_search_plan(
        utterance,
        span,
        route_definition,
        intent_broker=intent_broker,
        interaction_surface="structured",
    )


def build_app_shortcut_search_plan(
    utterance: str,
    span: TaskSpan,
    route_definition: RouteDefinition,
    intent_broker: IntentBroker | None = None,
) -> TaskPlan | None:
    return build_app_search_plan(
        utterance,
        span,
        route_definition,
        intent_broker=intent_broker,
        interaction_surface="shortcut",
    )


def build_app_search_plan(
    utterance: str,
    span: TaskSpan,
    route_definition: RouteDefinition,
    *,
    intent_broker: IntentBroker | None = None,
    interaction_surface: str,
) -> TaskPlan | None:
    intent = structured_capability_intent(span.text, intent_broker)
    if intent.action != "app.search_history":
        return None
    target = canonicalize_target_for_action(intent.action, intent.target)
    app_name = str(target.get("app") or "")
    query = str(target.get("query") or "")
    if not app_name or not query:
        return None
    route = task_route_from_definition(
        route_definition,
        "Search inside an application through the explicit app-interaction domain.",
    )
    step = TaskStep(
        id=route_definition.route_id.replace("_route", ""),
        action="app.search_history",
        capability_id="app.search_history",
        target={"app": app_name, "query": query, "interaction_surface": interaction_surface},
        depends_on=(),
        risk_level=CAPABILITIES["app.search_history"].risk_level,
        expected_state=ExpectedState(kind="search_results_available", fields={"query": query}),
        preconditions=(StepPrecondition(kind="capability_available", capability_id="app.search_history"),),
        provenance=StepProvenance(source_span_id=span.id, planner="v0.8_app_interaction_route"),
    )
    display = DisplayFields(
        goal=f"search {app_name} for {query}",
        explanation=(
            "Prefer structured UI search controls inside the application."
            if interaction_surface == "structured"
            else "Use a bounded shortcut-driven in-app search fallback."
        ),
    )
    return TaskPlan(
        schema_version="v0.5",
        plan_id=make_plan_id(utterance, route.id),
        utterance=utterance,
        display=display,
        selected_route_id=route.id,
        routes=(route,),
        steps=(step,),
        provenance={
            "planner": "v0.8_domain_planner",
            "planner_version": "v0.8",
            "source_span_id": span.id,
            "domain_id": "app_interaction",
            "interaction_surface": interaction_surface,
        },
    )


def build_window_list_plan(
    utterance: str,
    span: TaskSpan,
    route_definition: RouteDefinition,
    intent_broker: IntentBroker | None = None,
) -> TaskPlan | None:
    intent = structured_capability_intent(span.text, intent_broker)
    if intent.action != "window.list":
        return None
    route = task_route_from_definition(route_definition, "List current windows through the window-management domain.")
    step = TaskStep(
        id="window_list",
        action="window.list",
        capability_id="window.list",
        target={},
        depends_on=(),
        risk_level=CAPABILITIES["window.list"].risk_level,
        expected_state=ExpectedState(kind="window_list_requested"),
        preconditions=(StepPrecondition(kind="capability_available", capability_id="window.list"),),
        provenance=StepProvenance(source_span_id=span.id, planner="v0.5_window_route"),
    )
    return make_explicit_plan(utterance, span, route, (step,), goal="list windows", explanation="Use the explicit window-management domain for window inventory.")


def build_window_focus_plan(
    utterance: str,
    span: TaskSpan,
    route_definition: RouteDefinition,
    intent_broker: IntentBroker | None = None,
) -> TaskPlan | None:
    return build_single_window_action_plan(utterance, span, route_definition, "window.focus", "focused", intent_broker)


def build_window_state_plan(
    utterance: str,
    span: TaskSpan,
    route_definition: RouteDefinition,
    intent_broker: IntentBroker | None = None,
) -> TaskPlan | None:
    intent = structured_capability_intent(span.text, intent_broker)
    if intent.action not in {"window.minimize", "window.maximize"}:
        return None
    if intent.action not in route_definition.required_capability_ids:
        return None
    requested_state = "minimized" if intent.action == "window.minimize" else "maximized"
    return build_single_window_action_plan(utterance, span, route_definition, intent.action, requested_state, intent_broker)


def build_window_close_plan(
    utterance: str,
    span: TaskSpan,
    route_definition: RouteDefinition,
    intent_broker: IntentBroker | None = None,
) -> TaskPlan | None:
    return build_single_window_action_plan(utterance, span, route_definition, "window.close", "closed", intent_broker)


def build_clipboard_write_plan(
    utterance: str,
    span: TaskSpan,
    route_definition: RouteDefinition,
    intent_broker: IntentBroker | None = None,
) -> TaskPlan | None:
    intent = structured_capability_intent(span.text, intent_broker)
    if intent.action != "clipboard.write":
        return None
    target = canonicalize_target_for_action(intent.action, intent.target)
    route = task_route_from_definition(route_definition, "Write to the clipboard through the explicit clipboard domain.")
    step = TaskStep(
        id="clipboard_write",
        action="clipboard.write",
        capability_id="clipboard.write",
        target=target,
        depends_on=(),
        risk_level=CAPABILITIES["clipboard.write"].risk_level,
        expected_state=ExpectedState(kind="clipboard_content_requested", fields={"text": str(target.get("text") or "")}),
        preconditions=(StepPrecondition(kind="capability_available", capability_id="clipboard.write"),),
        provenance=StepProvenance(source_span_id=span.id, planner="v0.5_clipboard_route"),
    )
    return make_explicit_plan(utterance, span, route, (step,), goal="write the clipboard", explanation="Use the explicit clipboard domain instead of the compatibility intent bridge.")


def build_notification_send_plan(
    utterance: str,
    span: TaskSpan,
    route_definition: RouteDefinition,
    intent_broker: IntentBroker | None = None,
) -> TaskPlan | None:
    intent = structured_capability_intent(span.text, intent_broker)
    if intent.action != "notification.send":
        return None
    target = canonicalize_target_for_action(intent.action, intent.target)
    route = task_route_from_definition(route_definition, "Send a desktop notification through the notification domain.")
    step = TaskStep(
        id="notification_send",
        action="notification.send",
        capability_id="notification.send",
        target=target,
        depends_on=(),
        risk_level=CAPABILITIES["notification.send"].risk_level,
        expected_state=ExpectedState(kind="notification_requested", fields={"title": str(target.get("title") or "VibeOS")}),
        preconditions=(StepPrecondition(kind="capability_available", capability_id="notification.send"),),
        provenance=StepProvenance(source_span_id=span.id, planner="v0.5_notification_route"),
    )
    return make_explicit_plan(utterance, span, route, (step,), goal="send a notification", explanation="Use the explicit notification domain for desktop notifications.")


def build_system_status_plan(
    utterance: str,
    span: TaskSpan,
    route_definition: RouteDefinition,
    intent_broker: IntentBroker | None = None,
) -> TaskPlan | None:
    intent = structured_capability_intent(span.text, intent_broker)
    if intent.action != "system.status":
        return None
    route = task_route_from_definition(route_definition, "Inspect runtime status through the system-observation domain.")
    step = TaskStep(
        id="system_status",
        action="system.status",
        capability_id="system.status",
        target={},
        depends_on=(),
        risk_level=CAPABILITIES["system.status"].risk_level,
        expected_state=ExpectedState(kind="system_status_requested"),
        preconditions=(StepPrecondition(kind="capability_available", capability_id="system.status"),),
        provenance=StepProvenance(source_span_id=span.id, planner="v0.5_system_route"),
    )
    return make_explicit_plan(utterance, span, route, (step,), goal="read system status", explanation="Use the explicit system-observation domain for runtime status inspection.")


def build_single_window_action_plan(
    utterance: str,
    span: TaskSpan,
    route_definition: RouteDefinition,
    action: str,
    requested_state: str,
    intent_broker: IntentBroker | None = None,
) -> TaskPlan | None:
    intent = structured_capability_intent(span.text, intent_broker)
    if intent.action != action:
        return None
    target = canonicalize_target_for_action(intent.action, intent.target)
    route = task_route_from_definition(route_definition, "Manage window state through the explicit window-management domain.")
    step = TaskStep(
        id=action.replace(".", "_"),
        action=action,
        capability_id=action,
        target=target,
        depends_on=(),
        risk_level=CAPABILITIES[action].risk_level,
        expected_state=ExpectedState(kind="window_state_requested", fields={"window": str(target.get("name") or "current"), "requested_state": requested_state}),
        preconditions=(StepPrecondition(kind="capability_available", capability_id=action),),
        provenance=StepProvenance(source_span_id=span.id, planner="v0.5_window_route"),
    )
    return make_explicit_plan(utterance, span, route, (step,), goal=f"{requested_state} a window", explanation="Use the explicit window-management domain instead of the compatibility intent bridge.")


def make_explicit_plan(
    utterance: str,
    span: TaskSpan,
    route: TaskRoute,
    steps: tuple[TaskStep, ...],
    *,
    goal: str,
    explanation: str,
) -> TaskPlan:
    return TaskPlan(
        schema_version="v0.5",
        plan_id=make_plan_id(utterance, route.id),
        utterance=utterance,
        display=DisplayFields(goal=goal, explanation=explanation),
        selected_route_id=route.id,
        routes=(route,),
        steps=steps,
        provenance={"planner": "v0.5_domain_planner", "planner_version": "v0.5", "source_span_id": span.id, "domain_id": route.domain_id},
    )


ROUTE_BUILDERS = {
    "build_apps_list_plan": build_apps_list_plan,
    "build_apps_open_plan": build_apps_open_plan,
    "build_app_structured_search_plan": build_app_structured_search_plan,
    "build_app_shortcut_search_plan": build_app_shortcut_search_plan,
    "build_window_list_plan": build_window_list_plan,
    "build_window_focus_plan": build_window_focus_plan,
    "build_window_state_plan": build_window_state_plan,
    "build_window_close_plan": build_window_close_plan,
    "build_clipboard_write_plan": build_clipboard_write_plan,
    "build_notification_send_plan": build_notification_send_plan,
    "build_system_status_plan": build_system_status_plan,
    "build_browser_open_url_plan": build_browser_open_url_plan,
    "build_browser_search_web_plan": build_browser_search_web_plan,
    "build_browser_site_search_plan": build_browser_site_search_plan,
    "build_media_search_plan": build_media_search_plan,
    "build_media_play_plan": build_media_play_plan,
    "build_media_pause_plan": build_media_pause_plan,
    "build_browser_media_fallback_plan": build_browser_media_fallback_plan,
}


def task_route_from_definition(route_definition: RouteDefinition, explanation: str) -> TaskRoute:
    return TaskRoute(
        id=route_definition.route_id,
        score=0.0,
        domain_id=route_definition.domain_id,
        display=DisplayFields(explanation=explanation),
        score_inputs={},
        required_capabilities=route_definition.required_capability_ids,
        default_verifier_ids=route_definition.default_verifier_ids,
    )


def plan_payload(
    utterance: str,
    intent_broker: IntentBroker | None = None,
    capability_context: set[str] | None = None,
    debug: bool = False,
) -> dict[str, object]:
    artifacts = plan_turn(utterance, intent_broker=intent_broker, capability_context=capability_context, debug=debug)
    goal_payload = goal_synthesis_payload(artifacts.goal_synthesis) if artifacts.goal_synthesis is not None else None
    if artifacts.plan is None:
        route_action = artifacts.route_decision.action if artifacts.route_decision is not None else None
        if route_action == "clarify" or artifacts.analysis.type == "clarification":
            status = "clarification"
            overall_status = "needs_user_input"
            message = (
                artifacts.route_decision.reason
                if artifacts.route_decision is not None and artifacts.route_decision.reason
                else (artifacts.goal_synthesis.message if artifacts.goal_synthesis is not None else artifacts.analysis.explanation)
            )
        elif route_action == "blocked":
            status = "blocked"
            overall_status = "blocked"
            message = artifacts.route_decision.reason if artifacts.route_decision is not None else "no route satisfies required capabilities"
        elif route_action == "unsupported":
            status = "rejected"
            overall_status = "failed"
            message = artifacts.route_decision.reason if artifacts.route_decision is not None else "no executable task plan was produced"
        else:
            status = "rejected"
            overall_status = "failed"
            message = artifacts.goal_synthesis.message if artifacts.goal_synthesis is not None else artifacts.analysis.explanation
        if artifacts.candidates:
            return {
                "status": status,
                "understanding": asdict(artifacts.understanding),
                "analysis": asdict(artifacts.analysis),
                "goal_synthesis": goal_payload,
                "plan": None,
                "validation": None,
                "candidate_set": asdict(artifacts.candidate_set) if artifacts.candidate_set is not None else None,
                "route_decision": asdict(artifacts.route_decision) if artifacts.route_decision is not None else None,
                "candidates": [candidate_summary(candidate) for candidate in sorted(artifacts.candidates, key=lambda item: item.routes[0].score, reverse=True)],
                "domain_routing": asdict(artifacts.domain_routing) if artifacts.domain_routing else None,
                "observation_request": asdict(artifacts.observation_request) if artifacts.observation_request else None,
                "observation_receipt": asdict(artifacts.observation_receipt) if artifacts.observation_receipt else None,
                "capability_exposure": asdict(artifacts.capability_exposure) if artifacts.capability_exposure else None,
                "trace": asdict(artifacts.trace) if artifacts.trace is not None else {},
                "debug_trace": asdict(artifacts.debug_trace) if artifacts.debug_trace is not None else {},
                "execution_status": "not_started",
                "acceptance_status": "skipped",
                "overall_status": overall_status,
                "message": message or "no route satisfies required capabilities",
            }
        return {
            "status": status,
            "understanding": asdict(artifacts.understanding),
            "analysis": asdict(artifacts.analysis),
            "goal_synthesis": goal_payload,
            "plan": None,
            "validation": None,
            "candidate_set": asdict(artifacts.candidate_set) if artifacts.candidate_set is not None else None,
            "route_decision": asdict(artifacts.route_decision) if artifacts.route_decision is not None else None,
            "candidates": [],
            "domain_routing": asdict(artifacts.domain_routing) if artifacts.domain_routing else None,
            "observation_request": asdict(artifacts.observation_request) if artifacts.observation_request else None,
            "observation_receipt": asdict(artifacts.observation_receipt) if artifacts.observation_receipt else None,
            "capability_exposure": asdict(artifacts.capability_exposure) if artifacts.capability_exposure else None,
            "trace": asdict(artifacts.trace) if artifacts.trace is not None else {},
            "debug_trace": asdict(artifacts.debug_trace) if artifacts.debug_trace is not None else {},
            "execution_status": "not_started",
            "acceptance_status": "skipped",
            "overall_status": overall_status,
            "message": message,
        }
    validation = validate_plan(artifacts.plan)
    status = "validated" if validation.ok else "invalid"
    return {
        "status": status,
        "understanding": asdict(artifacts.understanding),
        "analysis": asdict(artifacts.analysis),
        "goal_synthesis": goal_payload,
        "plan": asdict(artifacts.plan),
        "validation": asdict(validation),
        "candidate_set": asdict(artifacts.candidate_set) if artifacts.candidate_set is not None else None,
        "route_decision": asdict(artifacts.route_decision) if artifacts.route_decision is not None else None,
        "candidates": [candidate_summary(candidate) for candidate in sorted(artifacts.candidates, key=lambda item: item.routes[0].score, reverse=True)],
        "domain_routing": asdict(artifacts.domain_routing) if artifacts.domain_routing else None,
        "observation_request": asdict(artifacts.observation_request) if artifacts.observation_request else None,
        "observation_receipt": asdict(artifacts.observation_receipt) if artifacts.observation_receipt else None,
        "capability_exposure": asdict(artifacts.capability_exposure) if artifacts.capability_exposure else None,
        "trace": asdict(artifacts.trace) if artifacts.trace is not None else {},
        "debug_trace": asdict(artifacts.debug_trace) if artifacts.debug_trace is not None else {},
        "execution_status": "not_started",
        "acceptance_status": "skipped",
        "overall_status": "validated",
    }


def candidate_summary(plan: TaskPlan) -> dict[str, object]:
    route = plan.routes[0]
    return {
        "candidate_id": f"cand_{route.id}",
        "plan_id": plan.plan_id,
        "route_id": route.id,
        "domain_id": route.domain_id,
        "score": route.score,
        "required_capabilities": list(route.required_capabilities),
        "default_verifier_ids": list(route.default_verifier_ids),
        "step_ids": [step.id for step in plan.steps],
    }


def make_plan_id(utterance: str, route_id: str) -> str:
    digest = sha256(f"{route_id}:{utterance}:{utc_now_iso()}".encode("utf-8")).hexdigest()[:12]
    return f"plan_{digest}"


def display_goal_for_intent(intent: Intent) -> str:
    if intent.action == "clipboard.write":
        return "write text to the clipboard"
    if intent.action == "browser.open_named_target":
        return "open a named website target"
    if intent.action in {"portal.open_uri", "browser.open_url"}:
        return "open a URL"
    if intent.action in {"browser.search_web", "browser.open_site_search"}:
        return "search in the browser"
    if intent.action == "notification.send":
        return "show a notification"
    if intent.action == "app.open":
        return "open an application"
    if intent.action == "app.search_history":
        return "search within an application"
    if intent.action == "window.list":
        return "list current windows"
    if intent.action == "window.focus":
        return "focus a window"
    if intent.action == "window.minimize":
        return "minimize a window"
    if intent.action == "window.maximize":
        return "maximize a window"
    if intent.action == "window.close":
        return "close a window"
    if intent.action == "app.list":
        return "list installed applications"
    if intent.action in {"media.search", "media.play", "media.pause"}:
        return "control media playback"
    return "inspect VibeOS session status"


def expected_state_for_intent(intent: Intent) -> ExpectedState:
    target = canonicalize_target_for_action(intent.action, intent.target)
    if intent.action == "app.list":
        return ExpectedState(kind="app_list_requested")
    if intent.action == "window.list":
        return ExpectedState(kind="window_list_requested")
    if intent.action == "system.status":
        return ExpectedState(kind="system_status_requested")
    if intent.action == "app.open":
        return ExpectedState(kind="app_opened_or_focused", fields={"app": str(target.get("name") or "")})
    if intent.action in {"window.focus", "window.minimize", "window.maximize", "window.close"}:
        requested_state = {
            "window.focus": "focused",
            "window.minimize": "minimized",
            "window.maximize": "maximized",
            "window.close": "closed",
        }[intent.action]
        return ExpectedState(kind="window_state_requested", fields={"window": str(target.get("name") or "current"), "requested_state": requested_state})
    if intent.action == "clipboard.write":
        return ExpectedState(kind="clipboard_content_requested", fields={"text": str(target.get("text") or "")})
    if intent.action == "browser.open_named_target":
        return ExpectedState(kind="named_site_open_requested", fields={"name": str(target.get("name") or "")})
    if intent.action in {"portal.open_uri", "browser.open_url"}:
        return ExpectedState(kind="uri_open_requested", fields={"uri": str(target.get("uri") or "")})
    if intent.action == "app.search_history":
        return ExpectedState(kind="search_results_available", fields={"query": str(intent.target.get("query") or "")})
    if intent.action in {"browser.search_web", "browser.open_site_search", "media.search"}:
        return ExpectedState(kind="search_results_available", fields={"query": str(intent.target.get("query") or "")})
    if intent.action == "notification.send":
        return ExpectedState(kind="notification_requested", fields={"title": str(intent.target.get("title") or "VibeOS")})
    if intent.action == "media.play":
        return ExpectedState(kind="media_playing", fields={"query": str(intent.target.get("query") or "")})
    return ExpectedState(kind="system_status_requested")


def extract_media_query(text: str) -> str:
    lowered = text.lower().strip()
    prefixes = ("play ", "listen to ", LISTEN_CN_PREFIX, "\u64ad\u653e ", "\u653e\u4e00\u9996 ")
    for prefix in prefixes:
        if lowered.startswith(prefix.lower()) or text.startswith(prefix):
            return text[len(prefix) :].strip()
    return ""


def extract_media_search_query(text: str) -> str:
    lowered = text.lower().strip()
    prefixes = ("search media for ", "search music for ", "find media ", "find music ")
    for prefix in prefixes:
        if lowered.startswith(prefix):
            return text[len(prefix) :].strip()
    return ""


def is_media_pause_request(text: str) -> bool:
    lowered = text.lower().strip()
    return lowered in {"pause", "pause music", "pause playback"} or lowered.startswith("pause ")


def extract_browser_url(text: str) -> str:
    stripped = text.strip()
    lowered = stripped.lower()
    if lowered.startswith("open https://"):
        return stripped.split(maxsplit=1)[1].strip()
    if stripped.startswith(OPEN_CN_PREFIX + "https://"):
        return stripped[len(OPEN_CN_PREFIX) :].strip()
    browser_intent = infer_browser_intent_from_open_request(stripped)
    if browser_intent is not None and browser_intent.action == "browser.open_url":
        return str(browser_intent.target.get("uri") or "")
    return ""


def extract_browser_search_query(text: str) -> str:
    stripped = text.strip()
    lowered = stripped.lower()
    if lowered.startswith("search web for "):
        return stripped[len("search web for ") :].strip()
    if stripped.startswith(SEARCH_CN_PREFIX):
        return stripped[len(SEARCH_CN_PREFIX) :].strip()
    browser_intent = infer_browser_intent_from_open_request(stripped)
    if browser_intent is not None and browser_intent.action == "browser.search_web":
        return str(browser_intent.target.get("query") or "")
    return ""


def extract_site_search(text: str) -> tuple[str, str] | None:
    stripped = text.strip()
    match = re.match(r"^search\s+([A-Za-z0-9.-]+\.[A-Za-z]{2,})\s+for\s+(.+)$", stripped, re.IGNORECASE)
    if not match:
        return None
    return match.group(1), match.group(2).strip()


def infer_browser_query_uri(query: str) -> str:
    return search_engine_template().format(query=quote_plus(query))


def infer_site_search_uri(site: str, query: str) -> str:
    return infer_browser_query_uri(f"site:{site} {query}")


def browser_semantic_uri(intent: Intent) -> str:
    if intent.action == "browser.open_url":
        return str(intent.target.get("uri") or "")
    if intent.action == "browser.search_web":
        return infer_browser_query_uri(str(intent.target.get("query") or ""))
    if intent.action == "browser.open_site_search":
        return infer_site_search_uri(str(intent.target.get("site") or ""), str(intent.target.get("query") or ""))
    return str(intent.target.get("uri") or "")
