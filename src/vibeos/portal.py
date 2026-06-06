from __future__ import annotations

import os
import shutil
import subprocess
from urllib.parse import urlparse

from .browser_state import record_browser_navigation
from .config import portal_timeout_seconds


class PortalAdapter:
    def status(self) -> dict[str, bool | str]:
        gdbus = shutil.which("gdbus")
        if not gdbus or os.name != "posix":
            return {"available": False, "reason": "gdbus not available"}
        cmd = [
            gdbus,
            "introspect",
            "--session",
            "--dest",
            "org.freedesktop.portal.Desktop",
            "--object-path",
            "/org/freedesktop/portal/desktop",
        ]
        completed = subprocess.run(
            cmd,
            check=False,
            capture_output=True,
            text=True,
            timeout=portal_timeout_seconds(),
        )
        if completed.returncode != 0:
            return {"available": False, "reason": completed.stderr.strip()}
        return {
            "available": True,
            "open_uri": "org.freedesktop.portal.OpenURI" in completed.stdout,
            "screenshot": "org.freedesktop.portal.Screenshot" in completed.stdout,
            "remote_desktop": "org.freedesktop.portal.RemoteDesktop" in completed.stdout,
        }

    def open_uri(self, uri: str) -> dict[str, str]:
        parsed = urlparse(uri)
        if parsed.scheme not in {"http", "https"}:
            return {"status": "unsupported", "error": "only http and https URIs are supported in v0.1"}
        gdbus = shutil.which("gdbus")
        if gdbus and os.name == "posix":
            try:
                completed = subprocess.run(
                    [
                        gdbus,
                        "call",
                        "--session",
                        "--dest",
                        "org.freedesktop.portal.Desktop",
                        "--object-path",
                        "/org/freedesktop/portal/desktop",
                        "--method",
                        "org.freedesktop.portal.OpenURI.OpenURI",
                        "",
                        uri,
                        "{}",
                    ],
                    check=False,
                    capture_output=True,
                    text=True,
                    timeout=portal_timeout_seconds(),
                )
            except subprocess.TimeoutExpired:
                record_browser_navigation(uri=uri, adapter="xdg-desktop-portal", status="timeout")
                return {"status": "timeout", "adapter": "xdg-desktop-portal", "error": "portal open-uri helper timed out"}
            if completed.returncode == 0:
                record_browser_navigation(uri=uri, adapter="xdg-desktop-portal", status="opened")
                return {"status": "opened", "uri": uri, "adapter": "xdg-desktop-portal"}

        xdg_open = shutil.which("xdg-open")
        if not xdg_open or os.name != "posix":
            record_browser_navigation(uri=uri, status="unavailable")
            return {"status": "unavailable", "error": "no URI opener available"}
        try:
            completed = subprocess.run(
                [xdg_open, uri],
                check=False,
                capture_output=True,
                text=True,
                timeout=portal_timeout_seconds(),
            )
        except subprocess.TimeoutExpired:
            record_browser_navigation(uri=uri, adapter="xdg-open", status="timeout")
            return {"status": "timeout", "adapter": "xdg-open", "error": "URI opener timed out"}
        if completed.returncode == 0:
            record_browser_navigation(uri=uri, adapter="xdg-open", status="opened")
            return {"status": "opened", "uri": uri, "adapter": "xdg-open"}
        record_browser_navigation(uri=uri, adapter="xdg-open", status="failed")
        return {"status": "failed", "error": completed.stderr.strip() or completed.stdout.strip()}
