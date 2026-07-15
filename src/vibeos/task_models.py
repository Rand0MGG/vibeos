from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from .models import RiskLevel


AnalysisType = Literal["chat", "task", "mixed", "clarification", "rejected"]
PlanStatus = Literal["planned", "needs_user_input", "rejected"]
ExecutionState = Literal["pending", "running", "succeeded", "failed", "blocked", "needs_user_input", "rejected"]
PlanReviewStatus = Literal["allowed", "review_required", "rejected"]
ExecutionStatus = Literal["not_started", "dry_run", "succeeded", "failed"]
AcceptanceStatus = Literal["passed", "failed", "indeterminate", "skipped"]
OverallStatus = Literal["failed", "needs_review", "dry_run", "completed", "incomplete", "blocked", "needs_user_input"]
FailureClass = Literal[
    "none",
    "provider_transient",
    "provider_timeout",
    "transport_timeout",
    "tool_timeout",
    "environment_unreachable",
    "permission_blocked",
    "semantic_mismatch",
    "acceptance_unverified",
    "acceptance_failed",
    "same_action_no_progress",
    "state_changed_externally",
    "unsupported_request",
]
ReplanAction = Literal["stop", "retry_same_attempt", "repair", "replan_with_constraints", "ask_user"]
RunStatus = Literal["running", "completed", "failed", "incomplete", "blocked", "needs_review", "needs_user_input", "dry_run"]
SemanticDecision = Literal["complete", "incomplete", "semantic_failure", "clarification_needed", "skipped"]


@dataclass(frozen=True)
class DisplayFields:
    goal: str = ""
    explanation: str = ""
    assumptions: tuple[str, ...] = ()


@dataclass(frozen=True)
class SourceSpan:
    start: int
    end: int
    text: str


@dataclass(frozen=True)
class ParseProvenance:
    parser: str
    parser_version: str
    source_span: SourceSpan
    confidence: float
    model: str | None = None
    schema_version: str | None = None
    repair_applied: bool = False


@dataclass(frozen=True)
class TaskSpan:
    id: str
    text: str
    start: int
    end: int
    domain: str
    confidence: float


@dataclass(frozen=True)
class UtteranceAnalysis:
    utterance: str
    type: AnalysisType
    confidence: float
    domains: tuple[str, ...] = ()
    explanation: str = ""
    task_spans: tuple[TaskSpan, ...] = ()
    provenance: ParseProvenance | None = None
    chat_response: str | None = None


@dataclass(frozen=True)
class ExpectedState:
    kind: str
    fields: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class StepPrecondition:
    kind: str
    capability_id: str = ""
    fields: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class StepProvenance:
    source_span_id: str
    planner: str


@dataclass(frozen=True)
class TaskRoute:
    id: str
    score: float
    domain_id: str = ""
    display: DisplayFields = field(default_factory=DisplayFields)
    score_inputs: dict[str, float] = field(default_factory=dict)
    required_capabilities: tuple[str, ...] = ()
    default_verifier_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class TaskStep:
    id: str
    action: str
    capability_id: str
    target: dict[str, Any] = field(default_factory=dict)
    depends_on: tuple[str, ...] = ()
    risk_level: RiskLevel = "L3"
    parallel_group: str | None = None
    expected_state: ExpectedState | None = None
    preconditions: tuple[StepPrecondition, ...] = ()
    provenance: StepProvenance | None = None


@dataclass(frozen=True)
class TaskPlan:
    schema_version: str
    plan_id: str
    utterance: str
    display: DisplayFields = field(default_factory=DisplayFields)
    status: PlanStatus = "planned"
    source_span_id: str = "span_1"
    selected_route_id: str = ""
    routes: tuple[TaskRoute, ...] = ()
    steps: tuple[TaskStep, ...] = ()
    provenance: dict[str, Any] = field(default_factory=dict)
    needs_user_input: bool = False


@dataclass(frozen=True)
class StepReviewRecord:
    step_safety_review_id: str
    step_id: str
    action: str
    risk_level: RiskLevel
    review_required: bool
    allowed: bool
    reason: str
    effects: tuple[str, ...] = ()
    reversible: bool = False


@dataclass(frozen=True)
class TaskPlanReviewResult:
    plan_id: str
    status: PlanReviewStatus
    max_risk_level: RiskLevel
    review_id: str | None = None
    step_reviews: tuple[StepReviewRecord, ...] = ()
    message: str = ""


@dataclass(frozen=True)
class PlanValidationResult:
    ok: bool
    plan_id: str
    errors: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class StepExecutionResult:
    step_id: str
    layer: str
    status: ExecutionState
    step_safety_review_id: str | None = None
    adapter: str | None = None
    capability_id: str | None = None
    attempt: int = 1
    attempt_id: str | None = None
    duration_ms: int | None = None
    adapter_status: str | None = None
    diagnostics: dict[str, Any] = field(default_factory=dict)
    error_code: str | None = None
    result: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    audit_id: str | None = None


@dataclass(frozen=True)
class PlanExecutionResult:
    plan_id: str
    status: ExecutionState
    step_results: tuple[StepExecutionResult, ...] = ()
    verification_results: tuple[dict[str, Any], ...] = ()
    verification_status: str | None = None
    execution_status: ExecutionStatus = "not_started"
    acceptance_status: AcceptanceStatus = "skipped"
    overall_status: OverallStatus = "failed"
    acceptance_result: dict[str, Any] | None = None
    error: str | None = None


@dataclass(frozen=True)
class FailureClassification:
    failure_class: FailureClass
    message: str = ""
    retryable: bool = False
    replannable: bool = False
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ReplanDecision:
    action: ReplanAction
    reason: str = ""
    replan_decision_id: str | None = None
    understanding_id: str | None = None
    candidate_set_id: str | None = None
    provider_name: str | None = None
    model_name: str | None = None
    parse_valid: bool = True
    fallback_used: bool = False
    error: str | None = None
    do_not_repeat_route_ids: tuple[str, ...] = ()
    do_not_repeat_capability_ids: tuple[str, ...] = ()
    candidate_domain_ids: tuple[str, ...] = ()
    max_next_attempts: int | None = None


@dataclass(frozen=True)
class PlanAttempt:
    attempt_id: str
    run_id: str
    attempt_index: int
    trigger: str
    understanding_id: str | None = None
    candidate_set_id: str | None = None
    route_decision_id: str | None = None
    replan_decision_id: str | None = None
    semantic_summary_id: str | None = None
    semantic_acceptance_decision_id: str | None = None
    step_safety_review_ids: tuple[str, ...] = ()
    selected_route_id: str = ""
    task_plan: TaskPlan | None = None
    execution_result: PlanExecutionResult | None = None
    observation_receipt: dict[str, Any] | None = None
    acceptance_result: dict[str, Any] | None = None
    failure: FailureClassification | None = None
    replan_decision: ReplanDecision | None = None


@dataclass(frozen=True)
class AgentRun:
    run_id: str
    goal_id: str
    utterance: str
    status: RunStatus
    selected_transport: str | None = None
    attempt_ids: tuple[str, ...] = ()
    final_outcome: str = ""


def canonicalize_target_for_action(action: str, target: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(target, dict):
        return {}

    if action == "clipboard.write":
        text = _first_text(target, "text", "content")
        return {"text": text} if text else {}

    if action == "portal.open_uri":
        uri = _first_text(target, "uri", "url", "name")
        if not uri:
            return {}
        canonical = {"uri": uri}
        kind = target.get("kind")
        if isinstance(kind, str) and kind.strip():
            canonical["kind"] = kind.strip()
        return canonical

    if action == "browser.open_url":
        uri = _first_text(target, "uri", "url", "name")
        return {"uri": uri} if uri else {}

    if action == "browser.open_named_target":
        name = _first_text(target, "name", "target_name")
        canonical: dict[str, Any] = {}
        if name:
            canonical["name"] = name
        resolution_mode = target.get("resolution_mode")
        if isinstance(resolution_mode, str) and resolution_mode.strip():
            canonical["resolution_mode"] = resolution_mode.strip()
        return canonical

    if action == "browser.search_web":
        query = _first_text(target, "query", "text")
        canonical: dict[str, Any] = {}
        if query:
            canonical["query"] = query
        named_target = _first_text(target, "named_target", "name")
        if named_target:
            canonical["named_target"] = named_target
        follow_search_result = target.get("follow_search_result")
        if isinstance(follow_search_result, bool):
            canonical["follow_search_result"] = follow_search_result
        return canonical

    if action == "browser.open_site_search":
        query = _first_text(target, "query", "text")
        site = _first_text(target, "site", "domain")
        canonical: dict[str, Any] = {}
        if site:
            canonical["site"] = site
        if query:
            canonical["query"] = query
        return canonical

    if action in {"media.search", "media.play", "media.pause"}:
        query = _first_text(target, "query", "text")
        canonical: dict[str, Any] = {}
        if query:
            canonical["query"] = query
        selection = target.get("selection")
        if isinstance(selection, str) and selection.strip():
            canonical["selection"] = selection.strip()
        return canonical or dict(target)

    if action == "app.search_history":
        app = _first_text(target, "app", "name")
        query = _first_text(target, "query", "text")
        canonical: dict[str, Any] = {}
        if app:
            canonical["app"] = app
        if query:
            canonical["query"] = query
        interaction_surface = target.get("interaction_surface")
        if isinstance(interaction_surface, str) and interaction_surface.strip():
            canonical["interaction_surface"] = interaction_surface.strip()
        return canonical

    if action == "app.open":
        name = _first_text(target, "name", "app")
        return {"name": name} if name else {}

    if action in {"window.focus", "window.minimize", "window.maximize", "window.close"}:
        name = _first_text(target, "name", "window")
        return {"name": name} if name else dict(target)

    if action == "notification.send":
        canonical: dict[str, Any] = {}
        title = _first_text(target, "title")
        body = _first_text(target, "body", "message", "name")
        if title:
            canonical["title"] = title
        if body:
            canonical["body"] = body
        return canonical

    return dict(target)


def _first_text(target: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = target.get(key)
        if isinstance(value, str):
            stripped = value.strip()
            if stripped:
                return stripped
    return ""


def task_plan_from_payload(payload: dict[str, Any]) -> TaskPlan:
    return TaskPlan(
        schema_version=str(payload["schema_version"]),
        plan_id=str(payload["plan_id"]),
        utterance=str(payload["utterance"]),
        display=DisplayFields(
            goal=str(payload.get("display", {}).get("goal", "")),
            explanation=str(payload.get("display", {}).get("explanation", "")),
            assumptions=tuple(str(item) for item in payload.get("display", {}).get("assumptions", ())),
        ),
        status=str(payload.get("status", "planned")),
        source_span_id=str(payload.get("source_span_id", "span_1")),
        selected_route_id=str(payload.get("selected_route_id", "")),
        routes=tuple(task_route_from_payload(item) for item in payload.get("routes", ())),
        steps=tuple(task_step_from_payload(item) for item in payload.get("steps", ())),
        provenance=payload.get("provenance", {}) if isinstance(payload.get("provenance"), dict) else {},
        needs_user_input=bool(payload.get("needs_user_input", False)),
    )


def task_route_from_payload(payload: dict[str, Any]) -> TaskRoute:
    return TaskRoute(
        id=str(payload["id"]),
        score=float(payload.get("score", 0.0)),
        domain_id=str(payload.get("domain_id", "")),
        display=DisplayFields(
            goal=str(payload.get("display", {}).get("goal", "")),
            explanation=str(payload.get("display", {}).get("explanation", "")),
            assumptions=tuple(str(item) for item in payload.get("display", {}).get("assumptions", ())),
        ),
        score_inputs={str(key): float(value) for key, value in (payload.get("score_inputs", {}) or {}).items()},
        required_capabilities=tuple(str(item) for item in payload.get("required_capabilities", ())),
        default_verifier_ids=tuple(str(item) for item in payload.get("default_verifier_ids", ())),
    )


def task_step_from_payload(payload: dict[str, Any]) -> TaskStep:
    expected_state_payload = payload.get("expected_state")
    provenance_payload = payload.get("provenance")
    action = str(payload["action"])
    return TaskStep(
        id=str(payload["id"]),
        action=action,
        capability_id=str(payload.get("capability_id", payload["action"])),
        target=canonicalize_target_for_action(action, payload.get("target", {}) if isinstance(payload.get("target"), dict) else {}),
        depends_on=tuple(str(item) for item in payload.get("depends_on", ())),
        risk_level=str(payload.get("risk_level", "L3")),
        parallel_group=str(payload["parallel_group"]) if payload.get("parallel_group") is not None else None,
        expected_state=ExpectedState(
            kind=str(expected_state_payload.get("kind", "")),
            fields=expected_state_payload.get("fields", {}) if isinstance(expected_state_payload.get("fields"), dict) else {},
        )
        if isinstance(expected_state_payload, dict)
        else None,
        preconditions=tuple(step_precondition_from_payload(item) for item in payload.get("preconditions", ())),
        provenance=StepProvenance(
            source_span_id=str(provenance_payload.get("source_span_id", "span_1")),
            planner=str(provenance_payload.get("planner", "")),
        )
        if isinstance(provenance_payload, dict)
        else None,
    )


def step_precondition_from_payload(payload: dict[str, Any]) -> StepPrecondition:
    return StepPrecondition(
        kind=str(payload.get("kind", "")),
        capability_id=str(payload.get("capability_id", "")),
        fields=payload.get("fields", {}) if isinstance(payload.get("fields"), dict) else {},
    )
