from __future__ import annotations

import os
import shutil
import subprocess


class NotificationAdapter:
    def send(self, title: str, body: str = "") -> dict[str, str]:
        notify_send = shutil.which("notify-send")
        if not notify_send or os.name != "posix":
            return {"status": "failed", "error": "notify-send not available"}
        completed = subprocess.run(
            [notify_send, title, body],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
        if completed.returncode == 0:
            return {"status": "sent", "title": title}
        return {"status": "failed", "error": completed.stderr.strip() or completed.stdout.strip()}
