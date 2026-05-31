from __future__ import annotations

import os
import shutil
import subprocess
from urllib.parse import urlparse


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
        completed = subprocess.run(cmd, check=False, capture_output=True, text=True, timeout=10)
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
            return {"status": "failed", "error": "only http and https URIs are supported in v0.1"}
        gdbus = shutil.which("gdbus")
        if gdbus and os.name == "posix":
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
                timeout=10,
            )
            if completed.returncode == 0:
                return {"status": "opened", "uri": uri, "adapter": "xdg-desktop-portal"}

        xdg_open = shutil.which("xdg-open")
        if not xdg_open or os.name != "posix":
            return {"status": "failed", "error": "no URI opener available"}
        completed = subprocess.run(
            [xdg_open, uri],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
        if completed.returncode == 0:
            return {"status": "opened", "uri": uri, "adapter": "xdg-open"}
        return {"status": "failed", "error": completed.stderr.strip() or completed.stdout.strip()}
