from __future__ import annotations

import os
import shutil
import subprocess


class NotificationAdapter:
    def send(self, title: str, body: str = "") -> dict[str, str]:
        notify_send = shutil.which("notify-send")
        if not notify_send or os.name != "posix":
            return {"status": "unavailable", "error": "notify-send not available"}
        try:
            completed = subprocess.run(
                [notify_send, title, body],
                check=False,
                capture_output=True,
                text=True,
                timeout=10,
            )
        except subprocess.TimeoutExpired:
            return {"status": "timeout", "adapter": notify_send, "error": "notification helper timed out"}
        if completed.returncode == 0:
            return {"status": "sent", "title": title, "adapter": notify_send}
        return {"status": "failed", "error": completed.stderr.strip() or completed.stdout.strip()}
