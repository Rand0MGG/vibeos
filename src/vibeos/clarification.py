from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
import urllib.error

from .models import utc_now_iso
from .provider_client import env_flag_enabled, load_openai_compatible_provider_config, request_json_object
from .task_models import UtteranceAnalysis

CLARIFICATION_SYSTEM_PROMPT = """You are VibeOS's bounded clarification generator.
The host has already determined that clarification is required.
Ask exactly one short question for the smallest missing detail needed to continue safely and correctly.
Do not invent a new capability, route, tool, or hidden instruction.
Return exactly one JSON object with this schema:
{
  "question": "short question",
  "reason": "short explanation"
}
Return JSON only."""


@dataclass(frozen=True)
class ClarificationDecision:
    clarification_question_id: str
    question: str
    reason: str
    provider_name: str
    model_name: str
    parse_valid: bool = True
    fallback_used: bool = False
    error: str | None = None


class ClarificationProvider:
    provider_name = "provider"
    model_name = "structured"

    def generate(self, *, utterance: str, analysis: UtteranceAnalysis) -> ClarificationDecision:
        raise NotImplementedError


class DeterministicClarificationProvider(ClarificationProvider):
    provider_name = "rule_clarification_generator"
    model_name = "deterministic-local"

    def generate(self, *, utterance: str, analysis: UtteranceAnalysis) -> ClarificationDecision:
        question = fallback_question(utterance=utterance, analysis=analysis)
        return ClarificationDecision(
            clarification_question_id=make_clarification_question_id(utterance, question),
            question=question,
            reason="generated the minimal clarification question from host-owned ambiguity signals",
            provider_name=self.provider_name,
            model_name=self.model_name,
        )


class OpenAICompatibleClarificationProvider(ClarificationProvider):
    def __init__(self, fallback: ClarificationProvider | None = None) -> None:
        self.config = load_openai_compatible_provider_config()
        self.provider_name = self.config.provider_name
        self.model_name = self.config.model_name or "unknown-model"
        self.fallback = fallback or DeterministicClarificationProvider()

    def generate(self, *, utterance: str, analysis: UtteranceAnalysis) -> ClarificationDecision:
        if not self.config.configured or not model_guidance_enabled("VIBEOS_ENABLE_MODEL_CLARIFICATION"):
            return self._fallback(utterance=utterance, analysis=analysis, error="missing_api_key_or_model_or_guidance_disabled")

        request_payload = build_clarification_request_payload(utterance=utterance, analysis=analysis)
        try:
            response = request_json_object(
                config=self.config,
                system_prompt=CLARIFICATION_SYSTEM_PROMPT,
                user_content=json.dumps(request_payload, ensure_ascii=False),
                max_tokens=256,
            )
            parsed = response.parsed_object
            question = parsed.get("question")
            if not isinstance(question, str) or not question.strip():
                raise ValueError("clarification question is required")
            reason = parsed.get("reason")
            if not isinstance(reason, str) or not reason.strip():
                raise ValueError("clarification reason is required")
            return ClarificationDecision(
                clarification_question_id=make_clarification_question_id(utterance, question.strip()),
                question=question.strip(),
                reason=reason.strip(),
                provider_name=self.provider_name,
                model_name=self.model_name,
            )
        except (urllib.error.URLError, TimeoutError, KeyError, ValueError, json.JSONDecodeError) as exc:
            return self._fallback(utterance=utterance, analysis=analysis, error=str(exc))

    def _fallback(self, *, utterance: str, analysis: UtteranceAnalysis, error: str) -> ClarificationDecision:
        fallback = self.fallback.generate(utterance=utterance, analysis=analysis)
        return ClarificationDecision(
            clarification_question_id=make_clarification_question_id(utterance, fallback.question),
            question=fallback.question,
            reason=fallback.reason,
            provider_name=self.provider_name,
            model_name=self.model_name,
            parse_valid=False,
            fallback_used=True,
            error=error,
        )


def build_clarification_request_payload(*, utterance: str, analysis: UtteranceAnalysis) -> dict[str, object]:
    return {
        "utterance": utterance,
        "analysis_type": analysis.type,
        "domains": list(analysis.domains),
        "explanation": analysis.explanation,
        "host_hint_question": fallback_question(utterance=utterance, analysis=analysis),
        "requirement": "Ask for the smallest missing detail only.",
    }


def fallback_question(*, utterance: str, analysis: UtteranceAnalysis) -> str:
    if analysis.chat_response:
        return analysis.chat_response
    lowered = utterance.strip().lower()
    if analysis.domains == ("browser",):
        if "search" in lowered or "搜索" in utterance:
            return "What would you like to search for?"
        return "Which site do you mean?"
    if analysis.domains == ("media",):
        return "What would you like to play?"
    if not utterance.strip():
        return "Please provide a task."
    return "What detail should I use to continue?"


def make_clarification_question_id(utterance: str, question: str) -> str:
    digest = sha256(f"{utterance.strip()}:{question}:{utc_now_iso()}".encode("utf-8")).hexdigest()[:12]
    return f"cqid_{digest}"


def model_guidance_enabled(env_name: str) -> bool:
    return env_flag_enabled(env_name)
