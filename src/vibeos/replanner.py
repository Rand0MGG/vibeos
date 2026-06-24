from __future__ import annotations

import json
from hashlib import sha256
import urllib.error
from dataclasses import dataclass
from typing import Any

from .task_models import FailureClassification, PlanAttempt, ReplanDecision, TaskPlan
from .models import utc_now_iso
from .provider_client import env_flag_enabled, load_openai_compatible_provider_config, request_json_object

REPLANNING_SYSTEM_PROMPT = """You are VibeOS's bounded replanning selector.
Choose exactly one provided replanning option as JSON only.
You must not invent a new route, capability, or authority.
Schema:
{
  "selected_option_id": "option_id_here",
  "reason": "short explanation"
}
Return JSON only."""


@dataclass(frozen=True)
class ReplanOption:
    option_id: str
    action: str
    reason: str
    do_not_repeat_route_ids: tuple[str, ...] = ()
    do_not_repeat_capability_ids: tuple[str, ...] = ()
    candidate_domain_ids: tuple[str, ...] = ()


class ReplanDecisionProvider:
    provider_name = "provider"
    model_name = "structured"

    def select_option(
        self,
        *,
        utterance: str,
        current_plan: TaskPlan,
        attempts: tuple[PlanAttempt, ...],
        failure: FailureClassification,
        options: tuple[ReplanOption, ...],
        understanding_id: str | None,
        candidate_set_id: str | None,
        available_domain_ids: tuple[str, ...] = (),
    ) -> ReplanDecision:
        raise NotImplementedError


class Replanner:
    def decide(
        self,
        *,
        utterance: str,
        current_plan: TaskPlan,
        attempts: tuple[PlanAttempt, ...],
        failure: FailureClassification,
        understanding_id: str | None = None,
        candidate_set_id: str | None = None,
        available_domain_ids: tuple[str, ...] = (),
    ) -> ReplanDecision:
        raise NotImplementedError


class DeterministicReplanDecisionProvider(ReplanDecisionProvider):
    provider_name = "rule_replanner"
    model_name = "deterministic-local"

    def select_option(
        self,
        *,
        utterance: str,
        current_plan: TaskPlan,
        attempts: tuple[PlanAttempt, ...],
        failure: FailureClassification,
        options: tuple[ReplanOption, ...],
        understanding_id: str | None,
        candidate_set_id: str | None,
        available_domain_ids: tuple[str, ...] = (),
    ) -> ReplanDecision:
        selected = options[0]
        return make_replan_decision(
            option=selected,
            understanding_id=understanding_id,
            candidate_set_id=candidate_set_id,
            provider_name=self.provider_name,
            model_name=self.model_name,
        )


class OpenAICompatibleReplanDecisionProvider(ReplanDecisionProvider):
    def __init__(self, fallback: ReplanDecisionProvider | None = None) -> None:
        self.config = load_openai_compatible_provider_config()
        self.provider_name = self.config.provider_name
        self.model_name = self.config.model_name or "unknown-model"
        self.fallback = fallback or DeterministicReplanDecisionProvider()

    def select_option(
        self,
        *,
        utterance: str,
        current_plan: TaskPlan,
        attempts: tuple[PlanAttempt, ...],
        failure: FailureClassification,
        options: tuple[ReplanOption, ...],
        understanding_id: str | None,
        candidate_set_id: str | None,
        available_domain_ids: tuple[str, ...] = (),
    ) -> ReplanDecision:
        if not self.config.configured or not model_guidance_enabled("VIBEOS_ENABLE_MODEL_REPLANNING"):
            return self._fallback(
                utterance=utterance,
                current_plan=current_plan,
                attempts=attempts,
                failure=failure,
                options=options,
                understanding_id=understanding_id,
                candidate_set_id=candidate_set_id,
                available_domain_ids=available_domain_ids,
                error="missing_api_key_or_model_or_guidance_disabled",
            )

        request_payload = build_replan_request_payload(
            utterance=utterance,
            current_plan=current_plan,
            attempts=attempts,
            failure=failure,
            options=options,
            understanding_id=understanding_id,
            candidate_set_id=candidate_set_id,
            available_domain_ids=available_domain_ids,
        )
        try:
            response = request_json_object(
                config=self.config,
                system_prompt=REPLANNING_SYSTEM_PROMPT,
                user_content=json.dumps(request_payload, ensure_ascii=False),
                max_tokens=384,
            )
            parsed = response.parsed_object
            selected_option_id = str(parsed.get("selected_option_id") or "").strip()
            selected = next((item for item in options if item.option_id == selected_option_id), None)
            if selected is None:
                raise ValueError("selected_option_id must match a provided option")
            reason = parsed.get("reason")
            if not isinstance(reason, str) or not reason.strip():
                raise ValueError("replanning reason is required")
            return make_replan_decision(
                option=ReplanOption(
                    option_id=selected.option_id,
                    action=selected.action,
                    reason=reason.strip(),
                    do_not_repeat_route_ids=selected.do_not_repeat_route_ids,
                    do_not_repeat_capability_ids=selected.do_not_repeat_capability_ids,
                    candidate_domain_ids=selected.candidate_domain_ids,
                ),
                understanding_id=understanding_id,
                candidate_set_id=candidate_set_id,
                provider_name=self.provider_name,
                model_name=self.model_name,
            )
        except (urllib.error.URLError, TimeoutError, KeyError, ValueError, json.JSONDecodeError) as exc:
            return self._fallback(
                utterance=utterance,
                current_plan=current_plan,
                attempts=attempts,
                failure=failure,
                options=options,
                understanding_id=understanding_id,
                candidate_set_id=candidate_set_id,
                available_domain_ids=available_domain_ids,
                error=str(exc),
            )

    def _fallback(
        self,
        *,
        utterance: str,
        current_plan: TaskPlan,
        attempts: tuple[PlanAttempt, ...],
        failure: FailureClassification,
        options: tuple[ReplanOption, ...],
        understanding_id: str | None,
        candidate_set_id: str | None,
        available_domain_ids: tuple[str, ...],
        error: str,
    ) -> ReplanDecision:
        fallback = self.fallback.select_option(
            utterance=utterance,
            current_plan=current_plan,
            attempts=attempts,
            failure=failure,
            options=options,
            understanding_id=understanding_id,
            candidate_set_id=candidate_set_id,
            available_domain_ids=available_domain_ids,
        )
        return ReplanDecision(
            action=fallback.action,
            reason=fallback.reason,
            replan_decision_id=make_replan_decision_id(fallback.action, fallback.reason, understanding_id, candidate_set_id),
            understanding_id=understanding_id,
            candidate_set_id=candidate_set_id,
            provider_name=self.provider_name,
            model_name=self.model_name,
            parse_valid=False,
            fallback_used=True,
            error=error,
            do_not_repeat_route_ids=fallback.do_not_repeat_route_ids,
            do_not_repeat_capability_ids=fallback.do_not_repeat_capability_ids,
            candidate_domain_ids=fallback.candidate_domain_ids,
        )


class EvidenceDrivenReplanner(Replanner):
    def __init__(self, max_attempts: int = 3, provider: ReplanDecisionProvider | None = None) -> None:
        self.max_attempts = max_attempts
        if provider is not None:
            self.provider = provider
        else:
            self.provider = OpenAICompatibleReplanDecisionProvider()

    def decide(
        self,
        *,
        utterance: str,
        current_plan: TaskPlan,
        attempts: tuple[PlanAttempt, ...],
        failure: FailureClassification,
        understanding_id: str | None = None,
        candidate_set_id: str | None = None,
        available_domain_ids: tuple[str, ...] = (),
    ) -> ReplanDecision:
        options = build_replan_options(
            utterance=utterance,
            current_plan=current_plan,
            attempts=attempts,
            failure=failure,
            max_attempts=self.max_attempts,
            available_domain_ids=available_domain_ids,
        )
        return self.provider.select_option(
            utterance=utterance,
            current_plan=current_plan,
            attempts=attempts,
            failure=failure,
            options=options,
            understanding_id=understanding_id,
            candidate_set_id=candidate_set_id,
            available_domain_ids=available_domain_ids,
        )


def build_replan_options(
    *,
    utterance: str,
    current_plan: TaskPlan,
    attempts: tuple[PlanAttempt, ...],
    failure: FailureClassification,
    max_attempts: int,
    available_domain_ids: tuple[str, ...] = (),
) -> tuple[ReplanOption, ...]:
    del utterance
    if failure.failure_class == "none":
        return (ReplanOption(option_id="stop_completed", action="stop", reason="execution completed"),)

    if len(attempts) >= max_attempts:
        return (ReplanOption(option_id="stop_budget", action="stop", reason="attempt budget exhausted"),)

    if failure.failure_class == "permission_blocked":
        return (ReplanOption(option_id="ask_user_permission", action="ask_user", reason=failure.message or "user approval or clarification is required"),)

    if failure.failure_class == "environment_unreachable":
        return (
            ReplanOption(option_id="stop_environment", action="stop", reason=failure.message or "environment does not expose the required capability"),
            ReplanOption(option_id="ask_user_environment", action="ask_user", reason="ask the user to change environment or confirm an alternative"),
        )

    if failure.failure_class in {"transport_timeout", "tool_timeout", "provider_timeout", "provider_transient"}:
        same_route_attempts = [item for item in attempts if item.selected_route_id == current_plan.selected_route_id]
        if len(same_route_attempts) < 2:
            return (
                ReplanOption(
                    option_id="retry_same_attempt",
                    action="retry_same_attempt",
                    reason=failure.message or "transient failure may succeed on retry",
                ),
                ReplanOption(option_id="stop_transient", action="stop", reason="stop instead of retrying the transient failure"),
            )
        return (ReplanOption(option_id="stop_transient_budget", action="stop", reason="transient retry budget exhausted"),)

    if failure.failure_class in {"semantic_mismatch", "acceptance_unverified", "acceptance_failed", "same_action_no_progress"}:
        return semantic_recovery_options(
            current_plan=current_plan,
            attempts=attempts,
            failure=failure,
            available_domain_ids=available_domain_ids,
        )

    return (
        ReplanOption(option_id="stop_default", action="stop", reason=failure.message or "no safe replanning path was identified"),
        ReplanOption(option_id="ask_user_default", action="ask_user", reason="ask the user for clarification instead of retrying blindly"),
    )


def semantic_recovery_options(
    *,
    current_plan: TaskPlan,
    attempts: tuple[PlanAttempt, ...],
    failure: FailureClassification,
    available_domain_ids: tuple[str, ...],
) -> tuple[ReplanOption, ...]:
    do_not_repeat_capability_ids = tuple(step.capability_id for step in current_plan.steps)
    selected_route = current_plan.routes[0] if current_plan.routes else None
    if (
        selected_route is not None
        and selected_route.domain_id == "app_interaction"
        and do_not_repeat_capability_ids == ("app.search_history",)
    ):
        # App search recovery often needs to keep the same capability while switching
        # to a weaker interaction surface such as a shortcut-driven route.
        do_not_repeat_capability_ids = ()
    shared_constraints = {
        "do_not_repeat_route_ids": (current_plan.selected_route_id,),
        "do_not_repeat_capability_ids": do_not_repeat_capability_ids,
    }
    alternative_domains = replan_candidate_domains(
        current_plan=current_plan,
        attempts=attempts,
        available_domain_ids=available_domain_ids,
    )
    options: list[ReplanOption] = []
    if failure.replannable:
        if alternative_domains:
            options.append(
                ReplanOption(
                    option_id="replan_alternative_domain",
                    action="replan_with_constraints",
                    reason=failure.message or "semantic evidence suggests a different host-generated domain",
                    candidate_domain_ids=alternative_domains,
                    **shared_constraints,
                )
            )
        else:
            options.append(
                ReplanOption(
                    option_id="replan_alternative",
                    action="replan_with_constraints",
                    reason=failure.message or "semantic evidence suggests a different host-generated route",
                    **shared_constraints,
                )
            )
    if failure.failure_class == "semantic_mismatch":
        options.append(
            ReplanOption(
                option_id="ask_user",
                action="ask_user",
                reason="clarify the requested target before more retries",
            )
        )
        options.append(
            ReplanOption(
                option_id="stop_mismatch",
                action="stop",
                reason="stop after semantic mismatch instead of trying another route",
            )
        )
        return tuple(options)
    options.append(
        ReplanOption(
            option_id="ask_user",
            action="ask_user",
            reason="ask the user for clarification instead of more retries",
        )
    )
    options.append(
        ReplanOption(
            option_id="stop_acceptance",
            action="stop",
            reason=failure.message or "acceptance did not produce a safe terminal result",
        )
    )
    return tuple(options)


def replan_candidate_domains(
    *,
    current_plan: TaskPlan,
    attempts: tuple[PlanAttempt, ...],
    available_domain_ids: tuple[str, ...],
) -> tuple[str, ...]:
    current_domains = tuple(dict.fromkeys(route.domain_id for route in current_plan.routes if route.domain_id))
    attempted_domains = {
        route.domain_id
        for attempt in attempts
        for route in attempt.task_plan.routes
        if route.domain_id
    }
    attempted_domains.update(current_domains)
    return tuple(
        domain_id
        for domain_id in dict.fromkeys(available_domain_ids)
        if domain_id and domain_id not in attempted_domains
    )


def build_replan_request_payload(
    *,
    utterance: str,
    current_plan: TaskPlan,
    attempts: tuple[PlanAttempt, ...],
    failure: FailureClassification,
    options: tuple[ReplanOption, ...],
    understanding_id: str | None,
    candidate_set_id: str | None,
    available_domain_ids: tuple[str, ...] = (),
) -> dict[str, Any]:
    return {
        "utterance": utterance,
        "understanding_id": understanding_id,
        "candidate_set_id": candidate_set_id,
        "current_plan": {
            "plan_id": current_plan.plan_id,
            "selected_route_id": current_plan.selected_route_id,
            "route_domain_ids": [route.domain_id for route in current_plan.routes],
            "capability_ids": [step.capability_id for step in current_plan.steps],
        },
        "failure": {
            "failure_class": failure.failure_class,
            "message": failure.message,
            "retryable": failure.retryable,
            "replannable": failure.replannable,
            "details": dict(failure.details),
        },
        "prior_attempts": [
            {
                "attempt_id": attempt.attempt_id,
                "attempt_index": attempt.attempt_index,
                "selected_route_id": attempt.selected_route_id,
                "failure_class": attempt.failure.failure_class if attempt.failure else "none",
                "failure_message": attempt.failure.message if attempt.failure else "",
            }
            for attempt in attempts
        ],
        "allowed_options": [
            {
                "option_id": option.option_id,
                "action": option.action,
                "reason": option.reason,
                "do_not_repeat_route_ids": list(option.do_not_repeat_route_ids),
                "do_not_repeat_capability_ids": list(option.do_not_repeat_capability_ids),
                "candidate_domain_ids": list(option.candidate_domain_ids),
            }
            for option in options
        ],
        "available_domain_ids": list(available_domain_ids),
    }


def make_replan_decision(
    *,
    option: ReplanOption,
    understanding_id: str | None,
    candidate_set_id: str | None,
    provider_name: str,
    model_name: str,
    parse_valid: bool = True,
    fallback_used: bool = False,
    error: str | None = None,
) -> ReplanDecision:
    return ReplanDecision(
        action=option.action,  # type: ignore[arg-type]
        reason=option.reason,
        replan_decision_id=make_replan_decision_id(option.action, option.reason, understanding_id, candidate_set_id),
        understanding_id=understanding_id,
        candidate_set_id=candidate_set_id,
        provider_name=provider_name,
        model_name=model_name,
        parse_valid=parse_valid,
        fallback_used=fallback_used,
        error=error,
        do_not_repeat_route_ids=option.do_not_repeat_route_ids,
        do_not_repeat_capability_ids=option.do_not_repeat_capability_ids,
        candidate_domain_ids=option.candidate_domain_ids,
    )


def make_replan_decision_id(action: str, reason: str, understanding_id: str | None, candidate_set_id: str | None) -> str:
    digest = sha256(f"{action}:{reason}:{understanding_id}:{candidate_set_id}:{utc_now_iso()}".encode("utf-8")).hexdigest()[:12]
    return f"rpdec_{digest}"


def model_guidance_enabled(env_name: str) -> bool:
    return env_flag_enabled(env_name)
