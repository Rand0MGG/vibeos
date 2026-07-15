import json

from vibeos.models import WindowEntry
from vibeos.windows import WindowRegistry, parse_shell_action, unwrap_gdbus_string


class StaticWindows(WindowRegistry):
    def __init__(self, windows):
        self._windows = windows

    def list_windows(self):
        return self._windows


def test_resolve_browser_alias_matches_firefox_app_id() -> None:
    registry = StaticWindows(
        [
            WindowEntry(window_id="1", app_id="org.mozilla.firefox.desktop", title="New Tab", focused=False),
            WindowEntry(window_id="2", app_id="org.gnome.Ptyxis.desktop", title="Terminal", focused=True),
        ]
    )

    assert [window.window_id for window in registry.resolve("browser")] == ["1"]


def test_resolve_terminal_alias_matches_ptyxis_app_id() -> None:
    registry = StaticWindows(
        [
            WindowEntry(window_id="1", app_id="org.mozilla.firefox.desktop", title="New Tab", focused=False),
            WindowEntry(window_id="2", app_id="org.gnome.Ptyxis.desktop", title="Terminal", focused=True),
        ]
    )

    assert [window.window_id for window in registry.resolve("terminal")] == ["2"]


def test_resolve_current_prefers_focused_window() -> None:
    registry = StaticWindows(
        [
            WindowEntry(window_id="1", app_id="org.mozilla.firefox.desktop", title="New Tab", focused=False),
            WindowEntry(window_id="2", app_id="org.gnome.Ptyxis.desktop", title="Terminal", focused=True),
        ]
    )

    assert [window.window_id for window in registry.resolve("current")] == ["2"]


def test_parse_shell_action_preserves_not_found_status() -> None:
    result = parse_shell_action('{"status":"not_found","window_id":"7"}', "7", success_status="closed")

    assert result == {"status": "not_found", "window_id": "7"}


def test_parse_shell_action_rejects_invalid_json() -> None:
    result = parse_shell_action("not-json", "7", success_status="closed")

    assert result["status"] == "failed"
    assert result["window_id"] == "7"
    assert "invalid shell response" in result["error"]


def test_unwrap_gdbus_string_preserves_nested_json_escaping() -> None:
    payload = json.dumps({"raw_output": json.dumps({"status": "unsupported"}), "message": "provider isn't available"})

    assert unwrap_gdbus_string(repr((payload,))) == payload
