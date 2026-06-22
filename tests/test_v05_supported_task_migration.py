import pytest

from vibeos.planner import plan_payload


@pytest.mark.parametrize(
    ("utterance", "expected_domain", "expected_route"),
    [
        ("list apps", "apps", "apps_list_route"),
        ("open browser", "apps", "apps_open_route"),
        ("list windows", "window_management", "window_list_route"),
        ("focus firefox", "window_management", "window_focus_route"),
        ("minimize firefox", "window_management", "window_minimize_route"),
        ("maximize firefox", "window_management", "window_maximize_route"),
        ("close firefox", "window_management", "window_close_route"),
        ("clipboard hello", "clipboard", "clipboard_write_route"),
        ("notify hello", "notification", "notification_send_route"),
        ("system status", "system_observation", "system_status_route"),
        ("open https://example.com", "browser", "browser_open_url_route"),
        ("search web for hello", "browser", "browser_search_web_route"),
        ("search github.com for issue", "browser", "browser_site_search_route"),
        ("search chat history in WeChat for Alice", "app_interaction", "app_structured_search_route"),
        ("play baby", "browser", "browser_music_search_route"),
    ],
)
def test_supported_task_matrix_uses_explicit_domains_without_legacy_routes(
    utterance: str,
    expected_domain: str,
    expected_route: str,
) -> None:
    payload = plan_payload(utterance)

    assert payload["status"] == "validated"
    assert payload["plan"]["selected_route_id"] == expected_route
    assert payload["plan"]["routes"][0]["domain_id"] == expected_domain
    assert not payload["plan"]["selected_route_id"].startswith("legacy_")
    assert payload["analysis"]["provenance"]["parser"] != "legacy_intent_bridge"
    assert payload["plan"]["provenance"]["planner"] != "legacy_intent_normalizer"


@pytest.mark.parametrize(
    "utterance",
    [
        "search media for lofi beats",
        "pause music",
    ],
)
def test_media_surface_returns_explicit_non_legacy_unavailable_outcome_when_capability_is_not_local(utterance: str) -> None:
    payload = plan_payload(utterance)

    assert payload["status"] == "blocked"
    assert payload["plan"] is None
    assert payload["candidates"]
    assert payload["overall_status"] == "blocked"
    assert all(not item["route_id"].startswith("legacy_") for item in payload["candidates"])
