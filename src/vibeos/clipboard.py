from __future__ import annotations

import os
import shutil
import subprocess


class ClipboardAdapter:
    def write(self, text: str) -> dict[str, str]:
        if os.name != "posix":
            return {"status": "unsupported", "error": "clipboard adapters are only implemented for Linux sessions"}
        command = first_available(("wl-copy", "xclip", "xsel"))
        if not command:
            return {"status": "unavailable", "error": "no supported clipboard command found"}

        if os.path.basename(command) == "wl-copy":
            return self._write_wl_copy(command, text)

        args = [command]
        if command == "xclip":
            args.extend(["-selection", "clipboard"])
        elif command == "xsel":
            args.append("--clipboard")

        try:
            completed = subprocess.run(
                args,
                input=text,
                check=False,
                capture_output=True,
                text=True,
                timeout=10,
            )
        except subprocess.TimeoutExpired:
            return {"status": "timeout", "adapter": command, "error": "clipboard helper timed out"}
        if completed.returncode == 0:
            return {"status": "written", "adapter": command}
        return {"status": "failed", "error": completed.stderr.strip() or completed.stdout.strip()}

    def _write_wl_copy(self, command: str, text: str) -> dict[str, str]:
        try:
            process = subprocess.Popen(
                [command],
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True,
            )
        except OSError as exc:
            return {"status": "failed", "adapter": command, "error": str(exc)}

        try:
            _, stderr = process.communicate(text, timeout=1)
        except subprocess.TimeoutExpired:
            return {"status": "written", "adapter": command}

        if process.returncode == 0:
            return {"status": "written", "adapter": command}
        return {"status": "failed", "adapter": command, "error": (stderr or "").strip()}


def first_available(commands: tuple[str, ...]) -> str | None:
    for command in commands:
        path = shutil.which(command)
        if path:
            return path
    return None
