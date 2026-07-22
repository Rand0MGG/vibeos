from vibeos.candidate_selection import CandidateSet, DeterministicCandidateSelectionProvider, OpenAICompatibleCandidateSelectionProvider
from vibeos.task_models import TaskSpan, UtteranceAnalysis
from vibeos.understanding import UnderstandingArtifact


def test_multi_domain_goal_requires_whole_goal_plan_instead_of_selecting_partial_action(monkeypatch) -> None:
    analysis = UtteranceAnalysis(
        utterance="check status; open Firefox; then notify me",
        type="task",
        confidence=0.9,
        domains=("system_observation", "apps", "notification"),
        explanation="multi-domain task",
        task_spans=(
            TaskSpan(
                id="span_1",
                text="check status; open Firefox; then notify me",
                start=0,
                end=43,
                domain="system_observation",
                confidence=0.9,
            ),
        ),
    )
    understanding = UnderstandingArtifact("understanding-1", analysis.utterance, analysis)
    candidate_set = CandidateSet("candidate-set-1", "understanding-1", "host_candidate_generation", ())

    deterministic = DeterministicCandidateSelectionProvider().decide(
        understanding=understanding,
        candidate_set=candidate_set,
    )

    assert deterministic.action == "clarify"
    assert "ordered bounded tasks" in deterministic.reason

    monkeypatch.setattr(
        "vibeos.candidate_selection.request_json_object",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("host safety boundary must not call the model")),
    )
    live = OpenAICompatibleCandidateSelectionProvider().decide(
        understanding=understanding,
        candidate_set=candidate_set,
    )

    assert live.action == "clarify"
    assert live.selected_candidate_id is None


def test_explicit_media_and_browser_goal_is_not_mistaken_for_internal_fallback() -> None:
    analysis = UtteranceAnalysis(
        utterance="pause the music, then open example.com",
        type="task",
        confidence=0.9,
        domains=("media", "browser"),
        explanation="explicit compound goal",
    )
    understanding = UnderstandingArtifact("understanding-2", analysis.utterance, analysis)
    candidate_set = CandidateSet("candidate-set-2", "understanding-2", "host_candidate_generation", ())

    decision = DeterministicCandidateSelectionProvider().decide(
        understanding=understanding,
        candidate_set=candidate_set,
    )

    assert decision.action == "clarify"
