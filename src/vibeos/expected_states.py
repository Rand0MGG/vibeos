from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ExpectedStateSpec:
    kind: str
    required_fields: tuple[str, ...]


EXPECTED_STATE_REGISTRY: dict[str, ExpectedStateSpec] = {
    "app_list_requested": ExpectedStateSpec("app_list_requested", ()),
    "app_opened_or_focused": ExpectedStateSpec("app_opened_or_focused", ("app",)),
    "window_list_requested": ExpectedStateSpec("window_list_requested", ()),
    "window_state_requested": ExpectedStateSpec("window_state_requested", ("window", "requested_state")),
    "system_status_requested": ExpectedStateSpec("system_status_requested", ()),
    "clipboard_content_requested": ExpectedStateSpec("clipboard_content_requested", ("text",)),
    "uri_open_requested": ExpectedStateSpec("uri_open_requested", ("uri",)),
    "named_site_open_requested": ExpectedStateSpec("named_site_open_requested", ("name",)),
    "notification_requested": ExpectedStateSpec("notification_requested", ("title",)),
    "search_results_available": ExpectedStateSpec("search_results_available", ("query",)),
    "media_playing": ExpectedStateSpec("media_playing", ("query",)),
}


def expected_state_known(kind: str) -> bool:
    return kind in EXPECTED_STATE_REGISTRY


def validate_expected_state(kind: str, fields: dict[str, Any]) -> str | None:
    spec = EXPECTED_STATE_REGISTRY.get(kind)
    if spec is None:
        return f"unknown expected_state.kind: {kind}"
    missing = [field for field in spec.required_fields if field not in fields]
    if missing:
        return f"expected_state {kind} missing required fields: {missing}"
    return None
