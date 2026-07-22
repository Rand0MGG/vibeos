from vibeos.models import Intent
from vibeos.permissions import EffectPolicy


def test_e0_observe_is_automatic() -> None:
    review = EffectPolicy().assess(Intent(action="window.list"))
    assert review.effect_level == "E0"
    assert review.allowed
    assert not review.review_required


def test_e1_session_action_is_automatic() -> None:
    review = EffectPolicy().assess(Intent(action="window.maximize", target={"name": "current"}))
    assert review.effect_level == "E1"
    assert review.allowed
    assert not review.review_required


def test_e1_bounded_clipboard_effect_is_automatic() -> None:
    review = EffectPolicy().assess(Intent(action="clipboard.write", target={"text": "hello"}))
    assert review.effect_level == "E1"
    assert review.allowed
    assert not review.review_required


def test_unknown_is_rejected_high_risk() -> None:
    review = EffectPolicy().assess(Intent.unknown("unsupported"))
    assert review.effect_level == "E4"
    assert not review.allowed
    assert not review.review_required


def test_open_uri_rejects_unsupported_scheme() -> None:
    review = EffectPolicy().assess(Intent(action="portal.open_uri", target={"uri": "file:///etc/passwd"}))
    assert review.effect_level == "E4"
    assert not review.allowed
    assert "http or https" in review.reason


def test_clipboard_write_requires_non_empty_text() -> None:
    review = EffectPolicy().assess(Intent(action="clipboard.write", target={"text": ""}))
    assert review.effect_level == "E4"
    assert not review.allowed
    assert "non-empty" in review.reason


def test_clipboard_write_accepts_content_alias() -> None:
    review = EffectPolicy().assess(Intent(action="clipboard.write", target={"content": "hello"}))
    assert review.effect_level == "E1"
    assert review.allowed
    assert not review.review_required
