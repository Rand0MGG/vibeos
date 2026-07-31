from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
from typing import Any


class ClipboardAdapter:
    def write(self, text: str) -> dict[str, str]:
        if os.name != "posix":
            return {"status": "unsupported", "error": "clipboard adapters are only implemented for Linux sessions"}
        shell_result = self._write_gnome_shell(text)
        if shell_result is not None:
            return shell_result
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

    def _write_gnome_shell(self, text: str) -> dict[str, str] | None:
        if not os.environ.get("DBUS_SESSION_BUS_ADDRESS"):
            return None
        try:
            return asyncio.run(self._set_gnome_clipboard(text))
        except (ImportError, OSError, RuntimeError, ValueError):
            return None

    @staticmethod
    async def _set_gnome_clipboard(text: str) -> dict[str, str] | None:
        from dbus_next import BusType, Message, MessageType
        from dbus_next.aio import MessageBus

        bus: Any = None
        try:
            bus = await MessageBus(bus_type=BusType.SESSION).connect()
            reply = await bus.call(
                Message(
                    destination="org.vibeos.Shell",
                    path="/org/vibeos/Shell",
                    interface="org.vibeos.Shell",
                    member="SetClipboard",
                    signature="s",
                    body=[text],
                )
            )
            if reply.message_type is MessageType.ERROR or len(reply.body) != 1 or not isinstance(reply.body[0], str):
                return None
            payload = json.loads(reply.body[0])
            if not isinstance(payload, dict) or payload.get("status") != "written":
                return None
            return {"status": "written", "adapter": "org.vibeos.Shell.SetClipboard"}
        finally:
            if bus is not None:
                bus.disconnect()


def first_available(commands: tuple[str, ...]) -> str | None:
    for command in commands:
        path = shutil.which(command)
        if path:
            return path
    return None
