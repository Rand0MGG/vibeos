from vibeos.goal_synthesizer import GoalSynthesizer
from vibeos.nlu import analyze_utterance


def test_goal_synthesizer_returns_typed_ready_goal_for_supported_request() -> None:
    utterance = "open browser"
    analysis = analyze_utterance(utterance)

    result = GoalSynthesizer().synthesize(utterance, analysis)

    assert result.status == "ready"
    assert result.goal_spec is not None
    assert result.goal_spec.goal_type == "app_open"
    assert result.goal_spec.candidate_domain_ids == ("apps",)
    assert result.goal_spec.required_capability_ids == ("app.open",)


def test_goal_synthesizer_returns_clarification_for_incomplete_media_request() -> None:
    utterance = "play"
    analysis = analyze_utterance(utterance)

    result = GoalSynthesizer().synthesize(utterance, analysis)

    assert result.status == "clarification_needed"
    assert result.goal_spec is not None
    assert result.goal_spec.clarification_questions


def test_goal_synthesizer_returns_missing_capability_for_unsupported_request() -> None:
    utterance = "email Alice about the report"
    analysis = analyze_utterance(utterance)

    result = GoalSynthesizer().synthesize(utterance, analysis)

    assert result.status == "missing_capability"
    assert result.goal_spec is not None
    assert result.goal_spec.missing_capability_ids == ("email.send",)
