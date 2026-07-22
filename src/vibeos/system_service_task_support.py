from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
from time import monotonic, sleep

from .core.adapters.task_repository import SqliteTaskRepository
from .core.domain import EffectLevel
from .core.domain.task import EvidenceBundle, GoalContract, TaskRun
from .durable_task_support import now_iso, stable_id
from .model_gateway.contracts import ServiceDiagnosis
from .observation_service import ObservationService
from .planning_models import PlanningArtifacts
from .system_service_contracts import FIXTURE_UNIT, SYSTEM_SERVICE_RECOVERY_ACTION, ServiceFactsV2
from .system_service_provider import SYNTHETIC_FAILURE_MARKER
from .task_models import DisplayFields, ExpectedState, StepProvenance, TaskPlan, TaskRoute, TaskStep, UtteranceAnalysis
from .understanding import UnderstandingArtifact


FIXED_SERVICE_GOAL = "诊断并恢复 VibeOS 测试用户服务，确认恢复完成"
FIXED_SERVICE_GOAL_EN = "Diagnose and recover the VibeOS test user service, then confirm recovery is complete"
SCENARIO_SCOPE = "scenario:goal04-system-service"


@dataclass(frozen=True)
class SystemServiceTaskResult:
    task: TaskRun
    diagnosis: str | None
    action: str | None
    current_state: str | None
    evidence_ids: tuple[str, ...]

    def payload(self) -> dict[str, object]:
        return {
            "schema_version": "v2",
            "task_id": self.task.task_id,
            "status": self.task.status.value,
            "revision": self.task.revision,
            "pending_reason": self.task.pending_reason,
            "diagnosis": self.diagnosis,
            "action": self.action,
            "current_state": self.current_state,
            "evidence_ids": list(self.evidence_ids),
            "terminal_outcome": self.task.terminal_outcome.__dict__ if self.task.terminal_outcome is not None else None,
        }


class SystemServiceEvidenceLedger:
    """Typed projection over canonical task evidence; it owns no state."""

    def __init__(self, repository: SqliteTaskRepository) -> None:
        self.repository = repository

    def latest_facts(self, task_id: str) -> ServiceFactsV2 | None:
        payload = self._latest_payload(task_id, "service_facts")
        value = payload.get("facts") if payload is not None else None
        return ServiceFactsV2.model_validate_json(json.dumps(value)) if isinstance(value, dict) else None

    def diagnosis_for_digest(self, task_id: str, digest: str) -> ServiceDiagnosis | None:
        for evidence in reversed(self.repository.evidence(task_id)):
            payload = _json_object(evidence.payload_json)
            if payload.get("kind") == "model_result" and payload.get("fact_digest") == digest:
                value = payload.get("diagnosis")
                return ServiceDiagnosis.model_validate_json(json.dumps(value)) if isinstance(value, dict) else None
        return None

    def latest_diagnosis(self, task_id: str) -> ServiceDiagnosis | None:
        payload = self._latest_payload(task_id, "model_result")
        value = payload.get("diagnosis") if payload is not None else None
        return ServiceDiagnosis.model_validate_json(json.dumps(value)) if isinstance(value, dict) else None

    def has_context_manifest(self, task_id: str, digest: str) -> bool:
        payload = self._latest_payload(task_id, "context_manifest")
        return payload is not None and payload.get("fact_digest") == digest

    def make(self, state: TaskRun, kind: str, payload: dict[str, object], summary: str) -> EvidenceBundle:
        timestamp = now_iso()
        body = {"schema_version": "v2", "kind": kind, **payload}
        return EvidenceBundle(
            stable_id("evidence_goal04_service", state.task_id, state.revision, kind, json.dumps(body, sort_keys=True, default=str)),
            state.task_id,
            state.current_step_id,
            None,
            "observed" if kind not in {"failure", "model_failure"} else "failed",
            summary,
            json.dumps(body, ensure_ascii=False, separators=(",", ":"), default=str),
            timestamp,
        )

    def ids(self, task_id: str) -> tuple[str, ...]:
        return tuple(item.evidence_id for item in self.repository.evidence(task_id))

    def _latest_payload(self, task_id: str, kind: str) -> dict[str, object] | None:
        for evidence in reversed(self.repository.evidence(task_id)):
            payload = _json_object(evidence.payload_json)
            if payload.get("kind") == kind:
                return payload
        return None


def build_system_service_planning(state: TaskRun, diagnosis: ServiceDiagnosis, goal: str) -> PlanningArtifacts:
    action = diagnosis.proposal.action
    plan_id = stable_id("plan_goal04_service", state.task_id, diagnosis.proposal.fact_digest, action)
    plan_revision_id = stable_id("planrev", state.task_id, state.revision + 1, plan_id)
    idempotency_key = stable_id("idem", state.task_id, plan_revision_id, "recover_fixture", SYSTEM_SERVICE_RECOVERY_ACTION, length=32)
    step = TaskStep(
        id="recover_fixture",
        action=SYSTEM_SERVICE_RECOVERY_ACTION,
        capability_id=SYSTEM_SERVICE_RECOVERY_ACTION,
        target={
            "unit": FIXTURE_UNIT,
            "operation": action,
            "idempotency_key": idempotency_key,
            "diagnosis": diagnosis.diagnosis,
            "fact_digest": diagnosis.proposal.fact_digest,
        },
        effect_level=EffectLevel.E1,
        expected_state=ExpectedState("system_service_healthy", {"unit": FIXTURE_UNIT, "active_state": "active", "sub_state": "running"}),
        provenance=StepProvenance("span_goal04_service", "goal04_system_service_planner_v1"),
    )
    plan = TaskPlan(
        schema_version="v2",
        plan_id=plan_id,
        utterance=goal,
        display=DisplayFields(goal=goal, explanation="Recover only the fixed Goal04 systemd user fixture."),
        source_span_id="span_goal04_service",
        selected_route_id="goal04_system_service_route",
        routes=(
            TaskRoute(
                "goal04_system_service_route",
                1.0,
                domain_id="system_observation",
                required_capabilities=(SYSTEM_SERVICE_RECOVERY_ACTION,),
                default_verifier_ids=("goal04_system_service_healthy",),
            ),
        ),
        steps=(step,),
        provenance={"model_gateway_schema": "v1", "fact_digest": diagnosis.proposal.fact_digest},
    )
    analysis = UtteranceAnalysis(goal, "task", 1.0, domains=("system_observation",), explanation="fixed Goal04 service task")
    understanding_id = stable_id("understanding_goal04_service", state.task_id)
    understanding = UnderstandingArtifact(
        understanding_id,
        goal,
        analysis,
        primary_understanding_id=understanding_id,
        analysis_provider_name="goal04_system_service_contract",
        analysis_model_name="deterministic-local",
    )
    return PlanningArtifacts(understanding, analysis, None, plan, (plan,))


def facts_fresh(facts: ServiceFactsV2) -> bool:
    captured = datetime.fromisoformat(facts.captured_at.replace("Z", "+00:00"))
    if captured.tzinfo is None:
        captured = captured.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - captured).total_seconds() <= facts.ttl_seconds


def is_goal04_contract(contract: GoalContract) -> bool:
    capability_marker = f"capability:{SYSTEM_SERVICE_RECOVERY_ACTION}"
    return contract.goal in {FIXED_SERVICE_GOAL, FIXED_SERVICE_GOAL_EN} and (SCENARIO_SCOPE in contract.scope or capability_marker in contract.scope)


def validate_initial_facts(facts: ServiceFactsV2) -> tuple[str, str] | None:
    if not facts_fresh(facts):
        return "stale_fact", "service facts expired before diagnosis"
    if facts.load_state != "loaded":
        return "unit_not_found", "fixed Goal04 fixture is not loaded"
    if facts.active_state not in {"failed", "inactive"}:
        return "precondition_failed", f"fixture must begin failed or inactive, found {facts.active_state}"
    if facts.journal is None:
        return "journal_unavailable", "bounded fixture journal is unavailable"
    if not any(SYNTHETIC_FAILURE_MARKER in line for line in facts.journal.lines):
        return "fixture_not_prepared", "fixture journal must contain the controller's synthetic failure marker"
    return None


def observe_for_verification(observation: ObservationService) -> tuple[ServiceFactsV2, bool]:
    deadline = monotonic() + 5.0
    stable_unhealthy = 0
    while True:
        facts = observation.observe_service_fixture(include_journal=True)
        healthy_log = facts.journal is not None and any("VIBEOS_GOAL04_HEALTHY_V1" in line for line in facts.journal.lines)
        healthy = facts.load_state == "loaded" and facts.active_state == "active" and facts.sub_state == "running" and facts.process.running and healthy_log
        if healthy:
            return facts, True
        stable_unhealthy = stable_unhealthy + 1 if facts.active_state in {"failed", "inactive"} else 0
        if stable_unhealthy >= 3 or monotonic() >= deadline:
            return facts, False
        sleep(0.1)


def _json_object(raw: str) -> dict[str, object]:
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}
