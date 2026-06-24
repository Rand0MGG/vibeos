from __future__ import annotations

from dataclasses import dataclass, field, replace
from hashlib import sha256
from typing import Any, Literal

from .assistant_semantics import INTERACTION_SURFACES, InteractionSurface, assistant_intent_to_payload
from .goal_models import GoalSpec
from .run_ledger import AttemptLedgerEntry, RunLedger
from .strategy import RecoveryPolicy, StrategyCandidate, StrategyConstraint, StrategyDecision, make_strategy_decision_id
from .tool_protocol import ToolExecutionContext, ToolInvocationEnvelope, ToolRegistry, ToolResult


GoalLifecycleStatus = Literal["active", "completed", "blocked", "needs_user_input", "needs_review", "failed", "incomplete"]
OutcomeStatus = Literal["completed", "incomplete", "failed", "blocked", "needs_user_input", "retryable", "replannable"]


@dataclass(frozen=True)
class GoalPolicy:
    max_turns: int = 10
    max_attempts_per_turn: int = 4


@dataclass(frozen=True)
class EnvironmentProfile:
    platform: str
    transport_mode: str
    daemon_available: bool
    desktop_integration_available: bool
    connectivity_limitations: str
    deployment_profile: str
    region: str
    search_policy: str
    dry_run: bool = False
    available_interaction_surfaces: tuple[InteractionSurface, ...] = INTERACTION_SURFACES
    browser_site_catalog: dict[str, str] = field(default_factory=dict)
    browser_search_catalog: dict[str, dict[str, Any]] = field(default_factory=dict)
    app_fixture_catalog: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ObservationEvidence:
    attempt_id: str
    source_tool_id: str
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class VerificationEvidence:
    attempt_id: str
    verifier_id: str
    status: str
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class OutcomeDecision:
    status: OutcomeStatus
    reason: str
    failure_class: str = "none"
    continue_running: bool = False


@dataclass(frozen=True)
class TerminalOutcome:
    status: GoalLifecycleStatus
    reason: str
    failure_class: str = "none"
    verifier_confirmed: bool = False


@dataclass(frozen=True)
class GoalTurn:
    turn_id: str
    goal_id: str
    turn_index: int
    utterance: str
    attempt_ids: tuple[str, ...] = ()
    status: GoalLifecycleStatus = "active"


@dataclass(frozen=True)
class GoalRuntime:
    goal_id: str
    session_id: str
    goal_spec: GoalSpec
    policy: GoalPolicy
    status: GoalLifecycleStatus = "active"
    current_strategy_id: str | None = None
    turn_ids: tuple[str, ...] = ()
    attempt_ids: tuple[str, ...] = ()
    terminal_outcome: TerminalOutcome | None = None


@dataclass
class AgentSession:
    session_id: str
    goals: dict[str, GoalRuntime] = field(default_factory=dict)
    turns: dict[str, GoalTurn] = field(default_factory=dict)


@dataclass(frozen=True)
class GoalRunResult:
    session_id: str
    goal_runtime: GoalRuntime
    turn: GoalTurn
    ledger: RunLedger
    terminal_outcome: TerminalOutcome
    strategy_candidates: tuple[StrategyCandidate, ...]
    selected_strategy_id: str | None
    debug_payload: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class StepRunResult:
    envelope: ToolInvocationEnvelope
    result: ToolResult
    state: dict[str, Any]
    evidence_entry: dict[str, Any]


class AgentRuntime:
    def __init__(self, tool_registry: ToolRegistry, recovery_policy: RecoveryPolicy | None = None) -> None:
        self.tool_registry = tool_registry
        self.recovery_policy = recovery_policy or RecoveryPolicy()
        self._sessions: dict[str, AgentSession] = {}
        self._ledgers: dict[tuple[str, str], RunLedger] = {}

    def create_session(self, session_id: str | None = None) -> AgentSession:
        resolved = session_id or self._make_id("session", "runtime")
        session = AgentSession(session_id=resolved)
        self._sessions[resolved] = session
        return session

    def start_goal(self, session_id: str, goal_spec: GoalSpec, policy: GoalPolicy | None = None) -> GoalRuntime:
        session = self._sessions[session_id]
        runtime = GoalRuntime(
            goal_id=goal_spec.goal_id,
            session_id=session_id,
            goal_spec=goal_spec,
            policy=policy or GoalPolicy(),
        )
        session.goals[goal_spec.goal_id] = runtime
        self._ledgers[(session_id, goal_spec.goal_id)] = RunLedger(session_id=session_id, goal_id=goal_spec.goal_id)
        return runtime

    def inspect_goal(self, session_id: str, goal_id: str) -> GoalRuntime:
        return self._sessions[session_id].goals[goal_id]

    def inspect_ledger(self, session_id: str, goal_id: str) -> RunLedger:
        return self._ledgers[(session_id, goal_id)]

    def continue_goal(
        self,
        *,
        session_id: str,
        goal_id: str,
        utterance: str,
        strategies: tuple[StrategyCandidate, ...],
        environment: EnvironmentProfile,
    ) -> GoalRunResult:
        session = self._sessions[session_id]
        goal_runtime = session.goals[goal_id]
        ledger = self._ledgers[(session_id, goal_id)]
        turn_index = len(goal_runtime.turn_ids) + 1
        turn_id = self._make_id("turn", f"{goal_id}:{turn_index}")
        turn = GoalTurn(turn_id=turn_id, goal_id=goal_id, turn_index=turn_index, utterance=utterance)
        session.turns[turn_id] = turn
        goal_runtime = replace(goal_runtime, turn_ids=(*goal_runtime.turn_ids, turn_id))
        session.goals[goal_id] = goal_runtime

        constraints = StrategyConstraint()
        selected_strategy_id: str | None = None
        for attempt_index in range(1, goal_runtime.policy.max_attempts_per_turn + 1):
            prior_attempts = tuple(item for item in ledger.attempts if item.turn_id == turn_id)
            previous_failure = prior_attempts[-1].failure_class if prior_attempts else "none"
            decision = self.recovery_policy.select_strategy(
                utterance=utterance,
                strategies=strategies,
                constraints=constraints,
                environment=environment,
                attempts=prior_attempts,
                last_failure_class=previous_failure,
            )
            ledger = ledger.append_strategy_decision(decision, turn_id=turn_id)
            if decision.action != "select" or decision.selected_strategy_id is None:
                terminal_status: GoalLifecycleStatus = "failed"
                if decision.failure_class == "environment_unreachable":
                    terminal_status = "blocked"
                elif decision.failure_class == "acceptance_unverified":
                    terminal_status = "incomplete"
                terminal = TerminalOutcome(status=terminal_status, reason=decision.reason, failure_class=decision.failure_class, verifier_confirmed=False)
                turn = replace(turn, status=terminal.status)
                goal_runtime = replace(goal_runtime, status=terminal.status, terminal_outcome=terminal)
                session.turns[turn_id] = turn
                session.goals[goal_id] = goal_runtime
                ledger = ledger.with_terminal_outcome(_terminal_payload(terminal))
                self._ledgers[(session_id, goal_id)] = ledger
                return GoalRunResult(
                    session_id=session_id,
                    goal_runtime=goal_runtime,
                    turn=turn,
                    ledger=ledger,
                    terminal_outcome=terminal,
                    strategy_candidates=strategies,
                    selected_strategy_id=selected_strategy_id,
                    debug_payload=self._build_debug_payload(goal_runtime, turn, strategies, ledger, selected_strategy_id),
                )

            selected_strategy_id = decision.selected_strategy_id
            strategy = next(candidate for candidate in strategies if candidate.strategy_id == decision.selected_strategy_id)
            attempt_id = self._make_id("attempt", f"{turn_id}:{attempt_index}:{strategy.strategy_id}")
            attempt_entry, outcome, state = self._execute_strategy(
                session_id=session_id,
                goal_id=goal_id,
                turn_id=turn_id,
                attempt_id=attempt_id,
                strategy=strategy,
                environment=environment,
            )
            ledger = ledger.append_attempt(attempt_entry)
            turn = replace(turn, attempt_ids=(*turn.attempt_ids, attempt_id))
            goal_runtime = replace(
                goal_runtime,
                current_strategy_id=strategy.strategy_id,
                attempt_ids=(*goal_runtime.attempt_ids, attempt_id),
            )
            session.turns[turn_id] = turn
            session.goals[goal_id] = goal_runtime
            if outcome.status == "completed":
                terminal = TerminalOutcome(status="completed", reason=outcome.reason, verifier_confirmed=True)
                turn = replace(turn, status="completed")
                goal_runtime = replace(goal_runtime, status="completed", terminal_outcome=terminal)
                session.turns[turn_id] = turn
                session.goals[goal_id] = goal_runtime
                ledger = ledger.with_terminal_outcome(_terminal_payload(terminal))
                self._ledgers[(session_id, goal_id)] = ledger
                return GoalRunResult(
                    session_id=session_id,
                    goal_runtime=goal_runtime,
                    turn=turn,
                    ledger=ledger,
                    terminal_outcome=terminal,
                    strategy_candidates=strategies,
                    selected_strategy_id=selected_strategy_id,
                    debug_payload=self._build_debug_payload(goal_runtime, turn, strategies, ledger, selected_strategy_id),
                )
            if outcome.status in {"failed", "blocked", "needs_user_input"}:
                terminal = TerminalOutcome(
                    status="blocked" if outcome.status == "blocked" else ("needs_user_input" if outcome.status == "needs_user_input" else "failed"),
                    reason=outcome.reason,
                    failure_class=outcome.failure_class,
                    verifier_confirmed=False,
                )
                turn = replace(turn, status=terminal.status)
                goal_runtime = replace(goal_runtime, status=terminal.status, terminal_outcome=terminal)
                session.turns[turn_id] = turn
                session.goals[goal_id] = goal_runtime
                ledger = ledger.with_terminal_outcome(_terminal_payload(terminal))
                self._ledgers[(session_id, goal_id)] = ledger
                return GoalRunResult(
                    session_id=session_id,
                    goal_runtime=goal_runtime,
                    turn=turn,
                    ledger=ledger,
                    terminal_outcome=terminal,
                    strategy_candidates=strategies,
                    selected_strategy_id=selected_strategy_id,
                    debug_payload=self._build_debug_payload(goal_runtime, turn, strategies, ledger, selected_strategy_id),
                )
            constraints = self.recovery_policy.next_constraints(strategy, outcome.failure_class)

        terminal = TerminalOutcome(status="blocked", reason="attempt budget exhausted", failure_class="attempt_budget_exhausted", verifier_confirmed=False)
        turn = replace(turn, status="blocked")
        goal_runtime = replace(goal_runtime, status="blocked", terminal_outcome=terminal)
        session.turns[turn_id] = turn
        session.goals[goal_id] = goal_runtime
        ledger = ledger.with_terminal_outcome(_terminal_payload(terminal))
        self._ledgers[(session_id, goal_id)] = ledger
        return GoalRunResult(
            session_id=session_id,
            goal_runtime=goal_runtime,
            turn=turn,
            ledger=ledger,
            terminal_outcome=terminal,
            strategy_candidates=strategies,
            selected_strategy_id=selected_strategy_id,
            debug_payload=self._build_debug_payload(goal_runtime, turn, strategies, ledger, selected_strategy_id),
        )

    def gate_goal(
        self,
        *,
        session_id: str,
        goal_id: str,
        utterance: str,
        strategies: tuple[StrategyCandidate, ...],
        selected_strategy_id: str,
        reason: str,
        terminal_status: GoalLifecycleStatus,
    ) -> GoalRunResult:
        session = self._sessions[session_id]
        goal_runtime = session.goals[goal_id]
        ledger = self._ledgers[(session_id, goal_id)]
        turn_index = len(goal_runtime.turn_ids) + 1
        turn_id = self._make_id("turn", f"{goal_id}:{turn_index}")
        turn = GoalTurn(turn_id=turn_id, goal_id=goal_id, turn_index=turn_index, utterance=utterance, status=terminal_status)
        session.turns[turn_id] = turn
        goal_runtime = replace(goal_runtime, turn_ids=(*goal_runtime.turn_ids, turn_id), current_strategy_id=selected_strategy_id)
        session.goals[goal_id] = goal_runtime
        selected_decision = self.recovery_policy.select_strategy(
            utterance=utterance,
            strategies=strategies,
            constraints=StrategyConstraint(),
            environment=EnvironmentProfile(
                platform="unknown",
                transport_mode="unknown",
                daemon_available=False,
                desktop_integration_available=False,
                connectivity_limitations="offline",
                deployment_profile="gate",
                region="local",
                search_policy="balanced",
            ),
            attempts=(),
            last_failure_class="none",
        )
        if selected_decision.selected_strategy_id != selected_strategy_id:
            selected_decision = replace(
                selected_decision,
                selected_strategy_id=selected_strategy_id,
                reason=reason,
                strategy_decision_id=make_strategy_decision_id(
                    selected_decision.action,
                    selected_strategy_id,
                    reason,
                    provider_name=selected_decision.provider_name,
                    model_name=selected_decision.model_name,
                ),
            )
        ledger = ledger.append_strategy_decision(selected_decision, turn_id=turn_id)
        terminal = TerminalOutcome(status=terminal_status, reason=reason, failure_class="permission_blocked" if terminal_status == "needs_review" else "unsupported_request", verifier_confirmed=False)
        goal_runtime = replace(goal_runtime, status=terminal.status, terminal_outcome=terminal)
        session.goals[goal_id] = goal_runtime
        ledger = ledger.with_terminal_outcome(_terminal_payload(terminal))
        self._ledgers[(session_id, goal_id)] = ledger
        return GoalRunResult(
            session_id=session_id,
            goal_runtime=goal_runtime,
            turn=turn,
            ledger=ledger,
            terminal_outcome=terminal,
            strategy_candidates=strategies,
            selected_strategy_id=selected_strategy_id,
            debug_payload=self._build_debug_payload(goal_runtime, turn, strategies, ledger, selected_strategy_id),
        )

    def record_external_turn(
        self,
        *,
        session_id: str,
        goal_spec: GoalSpec,
        utterance: str,
        strategies: tuple[StrategyCandidate, ...],
        selected_strategy_id: str | None,
        strategy_history: tuple[tuple[StrategyDecision, str | None], ...],
        attempts: tuple[AttemptLedgerEntry, ...],
        terminal: TerminalOutcome,
    ) -> GoalRunResult:
        session = self._sessions[session_id]
        if goal_spec.goal_id not in session.goals:
            self.start_goal(session_id, goal_spec)
        goal_runtime = session.goals[goal_spec.goal_id]
        ledger = self._ledgers[(session_id, goal_spec.goal_id)]
        turn_index = len(goal_runtime.turn_ids) + 1
        turn_id = self._make_id("turn", f"{goal_spec.goal_id}:{turn_index}")
        turn = GoalTurn(
            turn_id=turn_id,
            goal_id=goal_spec.goal_id,
            turn_index=turn_index,
            utterance=utterance,
            attempt_ids=tuple(item.attempt_id for item in attempts),
            status=terminal.status,
        )
        session.turns[turn_id] = turn
        goal_runtime = replace(
            goal_runtime,
            turn_ids=(*goal_runtime.turn_ids, turn_id),
            attempt_ids=(*goal_runtime.attempt_ids, *(item.attempt_id for item in attempts)),
            current_strategy_id=selected_strategy_id,
            status=terminal.status,
            terminal_outcome=terminal,
        )
        session.goals[goal_spec.goal_id] = goal_runtime
        for decision, attempt_id in strategy_history:
            ledger = ledger.append_strategy_decision(decision, turn_id=turn_id, attempt_id=attempt_id)
        for attempt in attempts:
            ledger = ledger.append_attempt(
                replace(
                    attempt,
                    turn_id=turn_id,
                    goal_id=goal_spec.goal_id,
                )
            )
        ledger = ledger.with_terminal_outcome(_terminal_payload(terminal))
        self._ledgers[(session_id, goal_spec.goal_id)] = ledger
        return GoalRunResult(
            session_id=session_id,
            goal_runtime=goal_runtime,
            turn=turn,
            ledger=ledger,
            terminal_outcome=terminal,
            strategy_candidates=strategies,
            selected_strategy_id=selected_strategy_id,
            debug_payload=self._build_debug_payload(goal_runtime, turn, strategies, ledger, selected_strategy_id),
        )

    def _execute_strategy(
        self,
        *,
        session_id: str,
        goal_id: str,
        turn_id: str,
        attempt_id: str,
        strategy: StrategyCandidate,
        environment: EnvironmentProfile,
    ) -> tuple[AttemptLedgerEntry, OutcomeDecision, dict[str, Any]]:
        state: dict[str, Any] = {"task_plan_id": strategy.task_plan.plan_id}
        envelopes: list[ToolInvocationEnvelope] = []
        evidence: list[dict[str, Any]] = []
        verification_records: list[VerificationEvidence] = []
        saw_observer = False
        saw_verifier = False
        semantic_refs = {
            "understanding_id": _metadata_string(strategy.metadata, "understanding_id"),
            "candidate_set_id": _metadata_string(strategy.metadata, "candidate_set_id"),
            "route_decision_id": _metadata_string(strategy.metadata, "route_decision_id"),
        }
        step_safety_review_ids = _metadata_tuple_strings(strategy.metadata, "step_safety_review_ids")

        for step in strategy.steps:
            step_run = self.execute_strategy_step(
                session_id=session_id,
                goal_id=goal_id,
                turn_id=turn_id,
                attempt_id=attempt_id,
                strategy_id=strategy.strategy_id,
                step=step,
                environment=environment,
                state=state,
            )
            envelope = step_run.envelope
            result = step_run.result
            state = dict(step_run.state)
            envelopes.append(envelope)
            evidence.append(step_run.evidence_entry)
            if envelope.family == "observer":
                saw_observer = True
            elif envelope.family == "verifier":
                saw_verifier = True
                verification_records.append(
                    VerificationEvidence(
                        attempt_id=attempt_id,
                        verifier_id=envelope.tool_id,
                        status="passed" if result.accepted else "failed",
                        details=result.evidence or result.output,
                    )
                )
            if result.status in {"failed", "blocked", "unavailable"}:
                outcome_status = "replannable" if result.failure_class in {"semantic_mismatch", "environment_unreachable"} else ("blocked" if result.status == "blocked" else "failed")
                message = result.message or f"{envelope.tool_id} did not complete successfully"
                attempt = AttemptLedgerEntry(
                    attempt_id=attempt_id,
                    turn_id=turn_id,
                    goal_id=goal_id,
                    strategy_id=strategy.strategy_id,
                    route_id=strategy.route_id,
                    trigger="execute_strategy",
                    task_plan_id=strategy.task_plan.plan_id,
                    capability_surface=strategy.capability_surface,
                    interaction_surface=strategy.interaction_surface,
                    understanding_id=semantic_refs["understanding_id"],
                    candidate_set_id=semantic_refs["candidate_set_id"],
                    route_decision_id=semantic_refs["route_decision_id"],
                    step_safety_review_ids=step_safety_review_ids,
                    tool_invocations=tuple(envelopes),
                    evidence=tuple(evidence),
                    outcome_status=outcome_status,
                    failure_class=result.failure_class,
                    message=message,
                )
                return attempt, OutcomeDecision(status=outcome_status, reason=message, failure_class=result.failure_class, continue_running=outcome_status == "replannable"), state

        if saw_verifier and all(item.status == "passed" for item in verification_records):
            attempt = AttemptLedgerEntry(
                attempt_id=attempt_id,
                turn_id=turn_id,
                goal_id=goal_id,
                strategy_id=strategy.strategy_id,
                route_id=strategy.route_id,
                trigger="execute_strategy",
                task_plan_id=strategy.task_plan.plan_id,
                capability_surface=strategy.capability_surface,
                interaction_surface=strategy.interaction_surface,
                understanding_id=semantic_refs["understanding_id"],
                candidate_set_id=semantic_refs["candidate_set_id"],
                route_decision_id=semantic_refs["route_decision_id"],
                step_safety_review_ids=step_safety_review_ids,
                tool_invocations=tuple(envelopes),
                evidence=tuple(evidence),
                outcome_status="completed",
                failure_class="none",
                message="verifier evidence accepted the goal outcome",
            )
            return attempt, OutcomeDecision(status="completed", reason=attempt.message, continue_running=False), state
        if not saw_observer and not saw_verifier and str(strategy.metadata.get("acceptance_mode") or "") == "action_only":
            attempt = AttemptLedgerEntry(
                attempt_id=attempt_id,
                turn_id=turn_id,
                goal_id=goal_id,
                strategy_id=strategy.strategy_id,
                route_id=strategy.route_id,
                trigger="execute_strategy",
                task_plan_id=strategy.task_plan.plan_id,
                capability_surface=strategy.capability_surface,
                interaction_surface=strategy.interaction_surface,
                understanding_id=semantic_refs["understanding_id"],
                candidate_set_id=semantic_refs["candidate_set_id"],
                route_decision_id=semantic_refs["route_decision_id"],
                step_safety_review_ids=step_safety_review_ids,
                tool_invocations=tuple(envelopes),
                evidence=tuple(evidence),
                outcome_status="completed",
                failure_class="none",
                message="action tool completed the goal without post-action verification requirements",
            )
            return attempt, OutcomeDecision(status="completed", reason=attempt.message, continue_running=False), state
        if saw_observer and not saw_verifier:
            attempt = AttemptLedgerEntry(
                attempt_id=attempt_id,
                turn_id=turn_id,
                goal_id=goal_id,
                strategy_id=strategy.strategy_id,
                route_id=strategy.route_id,
                trigger="execute_strategy",
                task_plan_id=strategy.task_plan.plan_id,
                capability_surface=strategy.capability_surface,
                interaction_surface=strategy.interaction_surface,
                understanding_id=semantic_refs["understanding_id"],
                candidate_set_id=semantic_refs["candidate_set_id"],
                route_decision_id=semantic_refs["route_decision_id"],
                step_safety_review_ids=step_safety_review_ids,
                tool_invocations=tuple(envelopes),
                evidence=tuple(evidence),
                outcome_status="incomplete",
                failure_class="acceptance_unverified",
                message="observation completed but verification evidence is missing",
            )
            return attempt, OutcomeDecision(status="incomplete", reason=attempt.message, failure_class="acceptance_unverified", continue_running=False), state
        attempt = AttemptLedgerEntry(
            attempt_id=attempt_id,
            turn_id=turn_id,
            goal_id=goal_id,
            strategy_id=strategy.strategy_id,
            route_id=strategy.route_id,
            trigger="execute_strategy",
            task_plan_id=strategy.task_plan.plan_id,
            capability_surface=strategy.capability_surface,
            interaction_surface=strategy.interaction_surface,
            understanding_id=semantic_refs["understanding_id"],
            candidate_set_id=semantic_refs["candidate_set_id"],
            route_decision_id=semantic_refs["route_decision_id"],
            step_safety_review_ids=step_safety_review_ids,
            tool_invocations=tuple(envelopes),
            evidence=tuple(evidence),
            outcome_status="failed",
            failure_class="acceptance_failed",
            message="strategy completed without acceptable outcome evidence",
        )
        return attempt, OutcomeDecision(status="failed", reason=attempt.message, failure_class="acceptance_failed", continue_running=False), state

    def execute_strategy_step(
        self,
        *,
        session_id: str,
        goal_id: str,
        turn_id: str,
        attempt_id: str,
        strategy_id: str,
        step,
        environment: EnvironmentProfile,
        state: dict[str, Any],
    ) -> StepRunResult:
        context = ToolExecutionContext(
            session_id=session_id,
            goal_id=goal_id,
            turn_id=turn_id,
            attempt_id=attempt_id,
            strategy_id=strategy_id,
            environment=environment,
            state=dict(state),
        )
        envelope, result = self.tool_registry.invoke(step.tool_id, dict(step.input_payload), context)
        next_state = dict(state)
        next_state.update(result.output)
        next_state.update(result.state_updates)
        if envelope.family == "observer":
            evidence_entry = {
                "kind": "observation",
                "payload": ObservationEvidence(
                    attempt_id=attempt_id,
                    source_tool_id=envelope.tool_id,
                    details=result.evidence or result.output,
                ).__dict__,
            }
        elif envelope.family == "verifier":
            evidence_entry = {
                "kind": "verification",
                "payload": VerificationEvidence(
                    attempt_id=attempt_id,
                    verifier_id=envelope.tool_id,
                    status="passed" if result.accepted else "failed",
                    details=result.evidence or result.output,
                ).__dict__,
            }
        else:
            evidence_entry = {
                "kind": envelope.family,
                "payload": {
                    "tool_id": envelope.tool_id,
                    "status": envelope.status,
                    "details": result.evidence or result.output,
                },
            }
        return StepRunResult(
            envelope=envelope,
            result=result,
            state=next_state,
            evidence_entry=evidence_entry,
        )

    def _build_debug_payload(
        self,
        goal_runtime: GoalRuntime,
        turn: GoalTurn,
        strategies: tuple[StrategyCandidate, ...],
        ledger: RunLedger,
        selected_strategy_id: str | None,
    ) -> dict[str, Any]:
        current_attempts = [attempt for attempt in ledger.attempts if attempt.turn_id == turn.turn_id]
        return {
            "goal_runtime": {
                "goal_id": goal_runtime.goal_id,
                "status": goal_runtime.status,
                "current_strategy_id": goal_runtime.current_strategy_id,
                "turn_ids": list(goal_runtime.turn_ids),
                "attempt_ids": list(goal_runtime.attempt_ids),
            },
            "strategy_candidates": [
                {
                    "strategy_id": strategy.strategy_id,
                    "route_id": strategy.route_id,
                    "capability_surface": strategy.capability_surface,
                    "interaction_surface": strategy.interaction_surface,
                    "task_plan_id": strategy.task_plan.plan_id,
                }
                for strategy in strategies
            ],
            "selected_strategy_id": selected_strategy_id,
            "action_plan_provenance": [
                {
                    "strategy_id": attempt.strategy_id,
                    "task_plan_id": attempt.task_plan_id,
                    "route_id": attempt.route_id,
                }
                for attempt in current_attempts
            ],
            "recovery_decisions": [
                {
                    "strategy_decision_id": entry.strategy_decision_id,
                    "action": entry.action,
                    "strategy_id": entry.strategy_id,
                    "reason": entry.reason,
                    "failure_class": entry.failure_class,
                    "provider_name": entry.provider_name,
                    "model_name": entry.model_name,
                    "fallback_used": entry.fallback_used,
                    "error": entry.error,
                }
                for entry in ledger.strategy_history
                if entry.turn_id == turn.turn_id
            ],
            "provider_artifacts": [
                {
                    "strategy_decision_id": entry.strategy_decision_id,
                    "provider_name": entry.provider_name,
                    "model_name": entry.model_name,
                    "parse_valid": entry.parse_valid,
                    "fallback_used": entry.fallback_used,
                    "error": entry.error,
                }
                for entry in ledger.strategy_history
                if entry.turn_id == turn.turn_id
            ],
            "assistant_intent": assistant_intent_to_payload(goal_runtime.goal_spec.assistant_intent),
        }

    @staticmethod
    def _make_id(prefix: str, seed: str) -> str:
        digest = sha256(f"{prefix}:{seed}".encode("utf-8")).hexdigest()[:12]
        return f"{prefix}_{digest}"


def _terminal_payload(outcome: TerminalOutcome) -> dict[str, Any]:
    return {
        "status": outcome.status,
        "reason": outcome.reason,
        "failure_class": outcome.failure_class,
        "verifier_confirmed": outcome.verifier_confirmed,
    }


def _metadata_string(metadata: dict[str, object], key: str) -> str | None:
    value = metadata.get(key)
    if value is None:
        return None
    return str(value)


def _metadata_tuple_strings(metadata: dict[str, object], key: str) -> tuple[str, ...]:
    value = metadata.get(key)
    if not isinstance(value, (list, tuple)):
        return ()
    return tuple(str(item) for item in value if item is not None)
