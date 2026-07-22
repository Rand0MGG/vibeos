from __future__ import annotations

from dataclasses import asdict, replace
from hashlib import sha256
from typing import Callable

from .assistant_semantics import assistant_intent_to_payload
from .candidate_selection import CandidateSelectionProvider, candidate_selection_decision_from_payload, candidate_set_from_payload
from .clarification import ClarificationProvider
from .goal_synthesizer import GoalSynthesisProvider
from .intent import IntentBroker
from .models import CommandRequest, ReviewRequest
from .planner import plan_turn
from .planning_models import PlanningArtifacts
from .task_models import FailureClassification, ReplanDecision, task_plan_from_payload
from .task_trace import record_model_io, record_trace_event
from .understanding import (
    UnderstandingAnalysisDecision,
    UnderstandingAnalysisProvider,
    UnderstandingArtifact,
    UnderstandingRefinement,
    UnderstandingSupersession,
    UnderstandingTransitionProvider,
    create_primary_understanding,
    default_understanding_host_hint,
    reconcile_reinterpreted_understanding,
    reconcile_understanding_transition,
    root_understanding_id,
    validated_understanding_from_payload,
)


PLANNING_SNAPSHOT_VERSION = 1


class PlanningSnapshotError(ValueError):
    """A persisted planning snapshot cannot safely re-enter orchestration."""


PlanTurner = Callable[..., PlanningArtifacts]


def _object_mapping(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        return {}
    return {str(key): item for key, item in value.items()}


def _integer(value: object, *, default: int = 0) -> int:
    if isinstance(value, (int, float, str)):
        try:
            return int(value)
        except ValueError:
            pass
    return default


def _string_items(value: object) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple, set)):
        return ()
    return tuple(str(item) for item in value)


class PlanningService:
    """Owns plan creation, planning transitions, and snapshot reconstruction."""

    def __init__(
        self,
        *,
        intent_broker: IntentBroker,
        route_selection_provider: CandidateSelectionProvider | None,
        clarification_provider: ClarificationProvider | None,
        analysis_provider: UnderstandingAnalysisProvider | None,
        goal_synthesis_provider: GoalSynthesisProvider | None,
        understanding_transition_provider: UnderstandingTransitionProvider,
        plan_turner: PlanTurner = plan_turn,
    ) -> None:
        self._intent_broker = intent_broker
        self._route_selection_provider = route_selection_provider
        self._clarification_provider = clarification_provider
        self._analysis_provider = analysis_provider
        self._goal_synthesis_provider = goal_synthesis_provider
        self._understanding_transition_provider = understanding_transition_provider
        self._plan_turner = plan_turner

    def plan(self, request: CommandRequest) -> PlanningArtifacts:
        return self._plan_turner(
            request.utterance,
            self._intent_broker,
            selection_provider=self._route_selection_provider,
            clarification_provider=self._clarification_provider,
            analysis_provider=self._analysis_provider,
            goal_synthesis_provider=self._goal_synthesis_provider,
            debug=request.debug,
        )

    def replan(
        self,
        planning: PlanningArtifacts,
        request: CommandRequest,
        excluded_route_ids: tuple[str, ...],
        excluded_capability_ids: tuple[str, ...],
        candidate_domain_ids_override: tuple[str, ...],
    ) -> PlanningArtifacts:
        return self._plan_turner(
            request.utterance,
            self._intent_broker,
            selection_provider=self._route_selection_provider,
            clarification_provider=self._clarification_provider,
            analysis_provider=self._analysis_provider,
            goal_synthesis_provider=self._goal_synthesis_provider,
            understanding=planning.understanding,
            debug=request.debug,
            candidate_domain_ids_override=candidate_domain_ids_override or None,
            excluded_route_ids=excluded_route_ids,
            excluded_capability_ids=excluded_capability_ids,
        )

    def resolve_understanding_transition(self, planning: PlanningArtifacts, *, trigger: str) -> PlanningArtifacts:
        understanding = planning.understanding
        analysis = planning.analysis
        if understanding is None or analysis is None:
            return planning
        decision = UnderstandingAnalysisDecision(
            analysis=analysis,
            provider_name="planning_understanding_sync",
            model_name="deterministic-local",
            request_payload={
                "trigger": trigger,
                "understanding_id": root_understanding_id(understanding),
                "active_understanding_id": understanding.understanding_id,
            },
        )
        return self._apply_understanding_transition(
            planning,
            analysis_decision=decision,
            reason=f"planning trigger {trigger} changed the active understanding basis",
        )

    def apply_replan_transition(
        self,
        planning: PlanningArtifacts,
        *,
        decision: ReplanDecision,
        failure: FailureClassification,
    ) -> PlanningArtifacts:
        understanding = planning.understanding
        analysis = planning.analysis
        if understanding is None or analysis is None:
            return planning
        transition = self._understanding_transition_provider.transition(
            understanding=understanding,
            current_analysis=analysis,
            decision=decision,
            failure=failure,
        )
        return self._apply_understanding_transition(
            planning,
            analysis_decision=transition,
            reason=f"replanning action {decision.action} updated the understanding basis",
        )

    def payload(self, planning: PlanningArtifacts) -> dict[str, object]:
        return {
            "snapshot_version": PLANNING_SNAPSHOT_VERSION,
            "understanding": asdict(planning.understanding),
            "analysis": asdict(planning.analysis),
            "understanding_refinement": asdict(planning.understanding_refinement) if planning.understanding_refinement else None,
            "understanding_supersession": asdict(planning.understanding_supersession) if planning.understanding_supersession else None,
            "goal_synthesis": asdict(planning.goal_synthesis) if planning.goal_synthesis else None,
            "assistant_intent": assistant_intent_to_payload(planning.goal_synthesis.goal_spec.assistant_intent)
            if planning.goal_synthesis and planning.goal_synthesis.goal_spec
            else None,
            "plan": asdict(planning.plan) if planning.plan else None,
            "candidate_set": asdict(planning.candidate_set) if planning.candidate_set else None,
            "route_decision": asdict(planning.route_decision) if planning.route_decision else None,
            "candidates": [asdict(candidate) for candidate in planning.candidates],
            "domain_routing": asdict(planning.domain_routing) if planning.domain_routing else None,
            "observation_request": asdict(planning.observation_request) if planning.observation_request else None,
            "observation_receipt": asdict(planning.observation_receipt) if planning.observation_receipt else None,
            "capability_exposure": asdict(planning.capability_exposure) if planning.capability_exposure else None,
            "trace": asdict(planning.trace) if planning.trace is not None else {},
            "debug_trace": asdict(planning.debug_trace) if planning.debug_trace is not None else {},
        }

    def from_snapshot(self, *, utterance: str, payload: dict[str, object]) -> PlanningArtifacts:
        version = payload.get("snapshot_version", 0)
        if version not in {0, PLANNING_SNAPSHOT_VERSION}:
            raise PlanningSnapshotError(f"unsupported planning snapshot version: {version!r}")
        plan_payload = payload.get("plan")
        if plan_payload is not None and not isinstance(plan_payload, dict):
            raise PlanningSnapshotError("planning snapshot plan must be an object")
        candidate_payloads = payload.get("candidates", ())
        if not isinstance(candidate_payloads, (list, tuple)):
            raise PlanningSnapshotError("planning snapshot candidates must be a list")
        try:
            plan = task_plan_from_payload(plan_payload) if plan_payload is not None else None
            candidates = tuple(task_plan_from_payload(item) for item in candidate_payloads if isinstance(item, dict))
            if len(candidates) != len(candidate_payloads):
                raise PlanningSnapshotError("planning snapshot contains a non-object candidate")
            candidate_set_payload = payload.get("candidate_set")
            route_decision_payload = payload.get("route_decision")
            if candidate_set_payload is not None and not isinstance(candidate_set_payload, dict):
                raise PlanningSnapshotError("planning snapshot candidate_set must be an object")
            if route_decision_payload is not None and not isinstance(route_decision_payload, dict):
                raise PlanningSnapshotError("planning snapshot route_decision must be an object")
            understanding = self._understanding_from_snapshot(utterance=utterance, payload=payload)
        except (KeyError, TypeError, ValueError) as exc:
            raise PlanningSnapshotError("planning snapshot is malformed") from exc
        return PlanningArtifacts(
            understanding=understanding,
            analysis=understanding.analysis,
            goal_synthesis=None,
            plan=plan,
            candidates=candidates,
            understanding_refinement=None,
            understanding_supersession=None,
            candidate_set=candidate_set_from_payload(candidate_set_payload) if isinstance(candidate_set_payload, dict) else None,
            route_decision=candidate_selection_decision_from_payload(route_decision_payload) if isinstance(route_decision_payload, dict) else None,
            domain_routing=None,
            observation_request=None,
            observation_receipt=None,
            capability_exposure=None,
            trace=None,
            debug_trace=None,
        )

    def plan_from_user_input(
        self,
        review_request: ReviewRequest,
        supplemental_input: str,
        *,
        primary_understanding_id: str | None,
    ) -> tuple[str, PlanningArtifacts]:
        restored = self.from_snapshot(utterance=review_request.utterance, payload=review_request.plan_payload or {})
        if primary_understanding_id and not isinstance((review_request.plan_payload or {}).get("understanding"), dict):
            restored = replace(
                restored,
                understanding=replace(
                    restored.understanding,
                    understanding_id=primary_understanding_id,
                    primary_understanding_id=primary_understanding_id,
                    source_understanding_id=restored.understanding.source_understanding_id,
                ),
            )
        resumed_utterance = self.merge_user_input_utterance(review_request.utterance, supplemental_input)
        reinterpreted_understanding, _ = create_primary_understanding(
            resumed_utterance,
            self._intent_broker,
            clarification_provider=self._clarification_provider,
            analysis_provider=self._analysis_provider,
        )
        updated_understanding, refinement, supersession = reconcile_reinterpreted_understanding(
            restored.understanding,
            reinterpreted_understanding,
            reason="supplemental user input refined the understanding basis",
        )
        transition_decision = UnderstandingAnalysisDecision(
            analysis=updated_understanding.analysis,
            provider_name=updated_understanding.analysis_provider_name or "resume_understanding_refiner",
            model_name=updated_understanding.analysis_model_name or "deterministic-local",
            request_payload={"utterance": resumed_utterance, "supplemental_input": supplemental_input, "resume_kind": "user_input"},
            response_payload={"analysis": asdict(updated_understanding.analysis)},
            parse_valid=updated_understanding.analysis_parse_valid,
            fallback_used=updated_understanding.analysis_fallback_used,
            error=updated_understanding.analysis_error,
        )
        transitioned = self._apply_understanding_transition(
            restored,
            analysis_decision=transition_decision,
            reason="supplemental user input refined the understanding basis",
            previous_understanding=restored.understanding,
            updated_understanding=updated_understanding,
            refinement=refinement,
            supersession=supersession,
        )
        planned = self._plan_turner(
            resumed_utterance,
            self._intent_broker,
            selection_provider=self._route_selection_provider,
            clarification_provider=self._clarification_provider,
            analysis_provider=self._analysis_provider,
            goal_synthesis_provider=self._goal_synthesis_provider,
            understanding=transitioned.understanding,
        )
        return resumed_utterance, replace(
            planned,
            understanding_refinement=transitioned.understanding_refinement,
            understanding_supersession=transitioned.understanding_supersession,
        )

    @staticmethod
    def merge_user_input_utterance(utterance: str, supplemental_input: str) -> str:
        base = utterance.strip()
        detail = supplemental_input.strip()
        return f"{base}\n\nAdditional user detail: {detail}" if base else detail

    def _apply_understanding_transition(
        self,
        planning: PlanningArtifacts,
        *,
        analysis_decision: UnderstandingAnalysisDecision,
        reason: str,
        previous_understanding: UnderstandingArtifact | None = None,
        updated_understanding: UnderstandingArtifact | None = None,
        refinement: UnderstandingRefinement | None = None,
        supersession: UnderstandingSupersession | None = None,
    ) -> PlanningArtifacts:
        previous = previous_understanding or planning.understanding
        updated = updated_understanding
        if updated is None:
            updated, refinement, supersession = reconcile_understanding_transition(previous, analysis_decision.analysis, reason=reason)
        if refinement is None and supersession is None:
            return planning
        if refinement is not None:
            artifact_id = refinement.refinement_id
            artifact_role = "refinement"
            changed_fields = refinement.changed_fields
            transition_reason = refinement.reason
        else:
            assert supersession is not None
            artifact_id = supersession.supersession_id
            artifact_role = "supersession"
            changed_fields = supersession.changed_fields
            transition_reason = supersession.reason
        primary_understanding_id = root_understanding_id(updated)
        source_artifact_ids = [previous.understanding_id]
        if previous.source_understanding_id is not None:
            source_artifact_ids.append(previous.source_understanding_id)
        record_model_io(
            phase="analysis",
            provider=analysis_decision.provider_name,
            model=analysis_decision.model_name,
            request_payload=analysis_decision.request_payload,
            response_payload=analysis_decision.response_payload,
            normalized_output=asdict(updated),
            parse_valid=analysis_decision.parse_valid,
            fallback_used=analysis_decision.fallback_used,
            error=analysis_decision.error,
            actor="planning_service",
            call_kind="structured_followup",
            consumed_artifacts={
                "understanding_id": primary_understanding_id,
                "active_understanding_id": previous.understanding_id,
                "candidate_set_id": planning.candidate_set.candidate_set_id if planning.candidate_set else None,
                "route_decision_id": planning.route_decision.route_decision_id if planning.route_decision else None,
            },
        )
        record_trace_event(
            phase="analysis",
            event_type="understanding_refined" if refinement is not None else "understanding_superseded",
            status=updated.analysis.type,
            actor="planning_service",
            data={
                "artifact_type": "understanding_transition",
                "artifact_id": artifact_id,
                "source_artifact_ids": source_artifact_ids,
                "artifact_role": artifact_role,
                "primary_understanding_id": updated.primary_understanding_id,
                "previous_understanding_id": previous.understanding_id,
                "active_understanding_id": updated.understanding_id,
                "changed_fields": list(changed_fields),
                "reason": transition_reason,
            },
        )
        return replace(
            planning,
            understanding=updated,
            analysis=updated.analysis,
            understanding_refinement=refinement,
            understanding_supersession=supersession,
        )

    def _understanding_from_snapshot(self, *, utterance: str, payload: dict[str, object]) -> UnderstandingArtifact:
        understanding_payload = _object_mapping(payload.get("understanding"))
        analysis_payload = _object_mapping(payload.get("analysis")) or _object_mapping(understanding_payload.get("analysis"))
        if not analysis_payload:
            plan = task_plan_from_payload(payload["plan"]) if isinstance(payload.get("plan"), dict) else None
            analysis_payload = {
                "type": "task" if plan is not None else "clarification",
                "confidence": 0.5,
                "domains": [route.domain_id for route in plan.routes if route.domain_id] if plan is not None else ["browser"],
                "explanation": "restored from stored goal loop payload",
                "chat_response": None,
            }
        analysis = validated_understanding_from_payload(
            utterance=utterance,
            payload=analysis_payload,
            host_hint=default_understanding_host_hint(utterance),
        )
        understanding_id = str(understanding_payload.get("understanding_id") or f"und_resume_{sha256(utterance.encode('utf-8')).hexdigest()[:10]}")
        primary_understanding_id = understanding_payload.get("primary_understanding_id")
        source_understanding_id = understanding_payload.get("source_understanding_id")
        return UnderstandingArtifact(
            understanding_id=understanding_id,
            utterance=utterance,
            analysis=analysis,
            artifact_role=str(understanding_payload.get("artifact_role", "primary")),
            primary_understanding_id=str(primary_understanding_id) if primary_understanding_id is not None else understanding_id,
            source_understanding_id=str(source_understanding_id) if source_understanding_id is not None else None,
            refinement_id=str(understanding_payload["refinement_id"]) if understanding_payload.get("refinement_id") is not None else None,
            supersession_id=str(understanding_payload["supersession_id"]) if understanding_payload.get("supersession_id") is not None else None,
            provider_intent=None,
            provider_parse_count=_integer(understanding_payload.get("provider_parse_count", 0)),
            provider_cache_hit_count=_integer(understanding_payload.get("provider_cache_hit_count", 0)),
            uncertainty_reasons=_string_items(understanding_payload.get("uncertainty_reasons", ())),
            clarification_question_id=str(understanding_payload["clarification_question_id"])
            if understanding_payload.get("clarification_question_id") is not None
            else None,
            clarification_provider_name=str(understanding_payload["clarification_provider_name"])
            if understanding_payload.get("clarification_provider_name") is not None
            else None,
            clarification_model_name=str(understanding_payload["clarification_model_name"])
            if understanding_payload.get("clarification_model_name") is not None
            else None,
            clarification_parse_valid=bool(understanding_payload.get("clarification_parse_valid", True)),
            clarification_fallback_used=bool(understanding_payload.get("clarification_fallback_used", False)),
            clarification_error=str(understanding_payload["clarification_error"]) if understanding_payload.get("clarification_error") is not None else None,
            analysis_provider_name=str(understanding_payload["analysis_provider_name"])
            if understanding_payload.get("analysis_provider_name") is not None
            else None,
            analysis_model_name=str(understanding_payload["analysis_model_name"]) if understanding_payload.get("analysis_model_name") is not None else None,
            analysis_parse_valid=bool(understanding_payload.get("analysis_parse_valid", True)),
            analysis_fallback_used=bool(understanding_payload.get("analysis_fallback_used", False)),
            analysis_error=str(understanding_payload["analysis_error"]) if understanding_payload.get("analysis_error") is not None else None,
        )
