from __future__ import annotations

import os
import shutil
import subprocess


class ClipboardAdapter:
    def write(self, text: str) -> dict[str, str]:
        if os.name != "posix":
            return {"status": "failed", "error": "clipboard adapters are only implemented for Linux sessions"}
        command = first_available(("wl-copy", "xclip", "xsel"))
        if not command:
            return {"status": "failed", "error": "no supported clipboard command found"}

        args = [command]
        if command == "xclip":
            args.extend(["-selection", "clipboard"])
        elif command == "xsel":
            args.append("--clipboard")

        completed = subprocess.run(
            args,
            input=text,
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
        if completed.returncode == 0:
            return {"status": "written", "adapter": command}
        return {"status": "failed", "error": completed.stderr.strip() or completed.stdout.strip()}


def first_available(commands: tuple[str, ...]) -> str | None:
    for command in commands:
        path = shutil.which(command)
        if path:
            return path
    return None
