from vibeos.capabilities import CAPABILITIES, allowed_actions, capability_payload, executable_actions
from vibeos.models import ALLOWED_ACTIONS
from vibeos.permissions import PermissionPolicy
from vibeos.models import Intent


def test_allowed_actions_match_registry() -> None:
    assert ALLOWED_ACTIONS == allowed_actions()
    assert set(executable_actions()) == set(CAPABILITIES)
    assert "unknown" in ALLOWED_ACTIONS


def test_every_registered_capability_has_permission_review() -> None:
    policy = PermissionPolicy()
    for action, spec in CAPABILITIES.items():
        review = policy.review(Intent(action=action, target=sample_target(action)))
        assert review.allowed == spec.allowed
        assert review.risk_level == spec.risk_level
        assert review.review_required == spec.review_required
        assert review.effects == spec.effects


def test_capability_payload_is_sorted_and_complete() -> None:
    payload = capability_payload()
    actions = [item["action"] for item in payload]
    assert actions == sorted(actions)
    assert set(actions) == set(CAPABILITIES)


def sample_target(action: str) -> dict[str, str]:
    if action == "app.open":
        return {"name": "Firefox"}
    if action in {"window.focus", "window.minimize", "window.maximize", "window.close"}:
        return {"name": "Firefox"}
    if action == "notification.send":
        return {"title": "VibeOS", "body": "hello"}
    if action == "portal.open_uri":
        return {"uri": "https://example.com"}
    if action == "browser.open_url":
        return {"uri": "https://example.com"}
    if action == "browser.open_named_target":
        return {"name": "example official website"}
    if action == "browser.search_web":
        return {"query": "hello"}
    if action == "browser.open_site_search":
        return {"site": "example.com", "query": "hello"}
    if action == "app.search_history":
        return {"app": "WeChat", "query": "Alice"}
    if action in {"media.search", "media.play"}:
        return {"query": "hello"}
    if action == "media.pause":
        return {"query": "hello"}
    if action == "clipboard.write":
        return {"text": "hello"}
    return {}
