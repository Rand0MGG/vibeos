from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import os
from pathlib import Path
import signal
import socket
import sys
import time

from .system_service_provider import SYNTHETIC_FAILURE_MARKER


def fixture_state_dir() -> Path:
    base = os.environ.get("XDG_STATE_HOME")
    root = Path(base) if base else Path.home() / ".local" / "state"
    return root / "vibeos" / "goal04-fixture"


def main() -> int:
    state_dir = fixture_state_dir()
    state_dir.mkdir(parents=True, exist_ok=True)
    token_path = state_dir / "fail-next-start"
    candidate = state_dir / f"consumed-{os.getpid()}"
    consumed_path: Path | None = candidate
    try:
        token_path.replace(candidate)
    except FileNotFoundError:
        consumed_path = None
    if consumed_path is not None:
        token = consumed_path.read_text(encoding="utf-8").strip()
        consumed_path.unlink(missing_ok=True)
        digest = hashlib.sha256(token.encode("utf-8")).hexdigest()[:16]
        print(f"{SYNTHETIC_FAILURE_MARKER} token_sha256={digest}", flush=True)
        return 23

    stopping = False

    def stop(_signum: int, _frame: object) -> None:
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    _notify_ready()
    print(f"VIBEOS_GOAL04_HEALTHY_V1 started_at={datetime.now(timezone.utc).isoformat()} pid={os.getpid()}", flush=True)
    while not stopping:
        time.sleep(0.25)
    print("VIBEOS_GOAL04_STOPPED_V1", flush=True)
    return 0


def _notify_ready() -> None:
    address = os.environ.get("NOTIFY_SOCKET")
    if not address:
        return
    target = f"\0{address[1:]}" if address.startswith("@") else address
    with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as channel:
        channel.connect(target)
        channel.sendall(b"READY=1\nSTATUS=Goal04 fixture is healthy")


if __name__ == "__main__":
    sys.exit(main())
