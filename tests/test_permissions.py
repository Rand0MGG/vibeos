from vibeos.models import Intent
from vibeos.permissions import PermissionPolicy


def test_l0_observe_is_automatic() -> None:
    review = PermissionPolicy().review(Intent(action="window.list"))
    assert review.risk_level == "L0"
    assert review.allowed
    assert not review.review_required


def test_l1_session_action_is_automatic() -> None:
    review = PermissionPolicy().review(Intent(action="window.maximize", target={"name": "current"}))
    assert review.risk_level == "L1"
    assert review.allowed
    assert not review.review_required


def test_l2_side_effect_requires_review() -> None:
    review = PermissionPolicy().review(Intent(action="clipboard.write", target={"text": "hello"}))
    assert review.risk_level == "L2"
    assert review.allowed
    assert review.review_required


def test_unknown_is_rejected_high_risk() -> None:
    review = PermissionPolicy().review(Intent.unknown("unsupported"))
    assert review.risk_level == "L3"
    assert not review.allowed
    assert not review.review_required


def test_open_uri_rejects_unsupported_scheme() -> None:
    review = PermissionPolicy().review(Intent(action="portal.open_uri", target={"uri": "file:///etc/passwd"}))
    assert review.risk_level == "L3"
    assert not review.allowed
    assert "http or https" in review.reason


def test_clipboard_write_requires_non_empty_text() -> None:
    review = PermissionPolicy().review(Intent(action="clipboard.write", target={"text": ""}))
    assert review.risk_level == "L3"
    assert not review.allowed
    assert "non-empty" in review.reason
