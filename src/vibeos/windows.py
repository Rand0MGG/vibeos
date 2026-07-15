from __future__ import annotations

import ast
import json
import os
import shutil
import subprocess

from .models import WindowEntry


WINDOW_ALIASES = {
    "browser": ("firefox", "org.mozilla.firefox", "web browser"),
    "浏览器": ("firefox", "org.mozilla.firefox", "browser"),
    "terminal": ("terminal", "ptyxis", "gnome terminal", "console"),
    "终端": ("terminal", "ptyxis", "gnome terminal", "console"),
}


class WindowRegistry:
    """Window registry using the VibeOS GNOME extension when available."""

    def list_windows(self) -> list[WindowEntry]:
        raw = call_vibeos_shell("ListWindows")
        if raw is None:
            return []
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return []
        windows = []
        for item in data:
            windows.append(
                WindowEntry(
                    window_id=str(item.get("id", "")),
                    app_id=str(item.get("app_id", "")),
                    title=str(item.get("title", "")),
                    workspace=item.get("workspace"),
                    focused=bool(item.get("focused", False)),
                )
            )
        return windows

    def resolve(self, query: str) -> list[WindowEntry]:
        query_norm = query.strip().lower()
        if query_norm in {"", "current", "current window", "当前", "当前窗口"}:
            focused = [window for window in self.list_windows() if window.focused]
            return focused or self.list_windows()[:1]
        matches = []
        terms = [query_norm, *WINDOW_ALIASES.get(query_norm, ())]
        for window in self.list_windows():
            haystack = f"{window.app_id} {window.title}".lower()
            if any(term and term in haystack for term in terms):
                matches.append(window)
        return matches

    def focus(self, window: WindowEntry) -> dict[str, str]:
        return shell_action("FocusWindow", window.window_id, "focused")

    def minimize(self, window: WindowEntry) -> dict[str, str]:
        return shell_action("MinimizeWindow", window.window_id, "minimized")

    def maximize(self, window: WindowEntry) -> dict[str, str]:
        return shell_action("MaximizeWindow", window.window_id, "maximized")

    def close(self, window: WindowEntry) -> dict[str, str]:
        return shell_action("CloseWindow", window.window_id, "closed")


def shell_action(method: str, window_id: str, status: str) -> dict[str, str]:
    raw = call_vibeos_shell(method, window_id)
    if raw is None:
        return {"status": "failed", "error": "VibeOS GNOME Shell extension unavailable"}
    return parse_shell_action(raw, window_id, success_status=status)


def call_vibeos_shell(method: str, *args: str) -> str | None:
    gdbus = shutil.which("gdbus")
    if not gdbus or os.name != "posix":
        return None
    cmd = [
        gdbus,
        "call",
        "--session",
        "--dest",
        "org.vibeos.Shell",
        "--object-path",
        "/org/vibeos/Shell",
        "--method",
        f"org.vibeos.Shell.{method}",
        *args,
    ]
    try:
        completed = subprocess.run(cmd, check=False, capture_output=True, text=True, timeout=10)
    except subprocess.TimeoutExpired:
        return None
    if completed.returncode != 0:
        return None
    return unwrap_gdbus_string(completed.stdout.strip())


def unwrap_gdbus_string(value: str) -> str:
    try:
        parsed = ast.literal_eval(value)
    except (SyntaxError, ValueError):
        parsed = None
    if isinstance(parsed, tuple) and len(parsed) == 1 and isinstance(parsed[0], str):
        return parsed[0]
    if value.startswith("('") and value.endswith("',)"):
        return value[2:-3].replace("\\'", "'")
    if value.startswith('("') and value.endswith('",)'):
        return value[2:-3].replace('\\"', '"')
    return value


def parse_shell_action(raw: str, window_id: str, success_status: str) -> dict[str, str]:
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return {"status": "failed", "window_id": window_id, "error": f"invalid shell response: {raw}"}
    if not isinstance(payload, dict):
        return {"status": "failed", "window_id": window_id, "error": f"unexpected shell response: {raw}"}

    result = {str(key): str(value) for key, value in payload.items() if value is not None}
    result.setdefault("window_id", window_id)

    returned_status = result.get("status")
    if returned_status == success_status:
        return result
    if not returned_status:
        result["status"] = "failed"
        result["error"] = f"missing status in shell response: {raw}"
        return result
    return result
