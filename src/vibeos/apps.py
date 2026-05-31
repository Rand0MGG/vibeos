from __future__ import annotations

import configparser
import os
import shutil
import subprocess
from pathlib import Path

from .models import AppEntry


APP_DIRS = (
    Path("/usr/share/applications"),
    Path.home() / ".local" / "share" / "applications",
)

ALIASES = {
    "browser": ("firefox", "google chrome", "chromium", "web"),
    "浏览器": ("firefox", "google chrome", "chromium", "web", "browser"),
    "terminal": ("terminal", "console", "kgx", "gnome terminal", "konsole"),
    "终端": ("terminal", "console", "kgx", "gnome terminal", "konsole"),
}


class AppRegistry:
    def __init__(self, app_dirs: tuple[Path, ...] = APP_DIRS) -> None:
        self.app_dirs = app_dirs

    def list_apps(self) -> list[AppEntry]:
        entries: list[AppEntry] = []
        for app_dir in self.app_dirs:
            if not app_dir.exists():
                continue
            for desktop_file in sorted(app_dir.glob("*.desktop")):
                entry = self._parse_desktop_file(desktop_file)
                if entry:
                    entries.append(entry)
        return entries

    def resolve(self, query: str) -> list[AppEntry]:
        query_norm = normalize(query)
        candidates = self.list_apps()
        scored: list[tuple[int, AppEntry]] = []
        expanded_queries = [query_norm, *ALIASES.get(query_norm, ())]
        for app in candidates:
            haystack = " ".join(
                [
                    app.desktop_id,
                    app.name,
                    " ".join(app.keywords),
                    " ".join(app.categories),
                ]
            ).lower()
            score = 0
            for term in expanded_queries:
                term_norm = normalize(term)
                if not term_norm:
                    continue
                if normalize(app.name) == term_norm or normalize(app.desktop_id) == term_norm:
                    score += 100
                elif term_norm in haystack:
                    score += 20
            if score:
                scored.append((score, app))
        scored.sort(key=lambda item: (-item[0], item[1].name.lower()))
        return [app for _, app in scored[:10]]

    def open_app(self, app: AppEntry) -> dict[str, str]:
        gtk_launch = shutil.which("gtk-launch")
        if not gtk_launch:
            return {"status": "failed", "error": "gtk-launch not found"}
        try:
            completed = subprocess.run(
                [gtk_launch, app.desktop_id],
                check=False,
                capture_output=True,
                text=True,
                timeout=20,
            )
        except subprocess.TimeoutExpired:
            return {"status": "failed", "error": "gtk-launch timed out"}
        if completed.returncode == 0:
            return {"status": "opened", "desktop_id": app.desktop_id}
        return {"status": "failed", "error": completed.stderr.strip() or completed.stdout.strip()}

    def _parse_desktop_file(self, path: Path) -> AppEntry | None:
        parser = configparser.ConfigParser(interpolation=None, strict=False)
        parser.optionxform = str
        try:
            parser.read(path, encoding="utf-8")
        except configparser.Error:
            return None
        if "Desktop Entry" not in parser:
            return None
        section = parser["Desktop Entry"]
        if section.get("NoDisplay", "").lower() == "true":
            return None
        if section.get("Hidden", "").lower() == "true":
            return None
        if section.get("Type", "Application") != "Application":
            return None
        name = section.get("Name")
        if not name:
            return None
        keywords = split_desktop_list(section.get("Keywords", ""))
        categories = split_desktop_list(section.get("Categories", ""))
        return AppEntry(
            desktop_id=path.name,
            name=name,
            exec_line=section.get("Exec"),
            keywords=tuple(keywords),
            categories=tuple(categories),
        )


def split_desktop_list(value: str) -> list[str]:
    return [part for part in value.split(";") if part]


def normalize(value: str) -> str:
    return os.path.basename(value.strip().lower()).replace(".desktop", "")
