from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import secrets
import subprocess
import sys
import time

from .goal04_fixture_service import fixture_state_dir
from .system_service_contracts import FIXTURE_UNIT
from .system_service_provider import JOURNALCTL, SYNTHETIC_FAILURE_MARKER, SYSTEMCTL


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Pre-task controller for the fixed Goal04 fixture")
    parser.add_argument("command", choices=("prepare", "status"))
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "status":
        result = _run([SYSTEMCTL, "--user", "show", FIXTURE_UNIT, "--no-pager", "--property=LoadState,ActiveState,SubState,Result"])
        print(result.stdout, end="")
        return result.returncode
    return prepare()


def prepare() -> int:
    """Reset and consume exactly one failure token before an Agent task starts."""

    state_dir = fixture_state_dir().resolve()
    expected_suffix = Path("vibeos") / "goal04-fixture"
    if tuple(state_dir.parts[-2:]) != tuple(expected_suffix.parts):
        raise RuntimeError("refusing unexpected fixture state path")
    _run([SYSTEMCTL, "--user", "stop", FIXTURE_UNIT])
    _run([SYSTEMCTL, "--user", "reset-failed", FIXTURE_UNIT])
    state_dir.mkdir(parents=True, exist_ok=True)
    for name in ("fail-next-start",):
        (state_dir / name).unlink(missing_ok=True)
    for consumed in state_dir.glob("consumed-*"):
        if consumed.parent == state_dir:
            consumed.unlink(missing_ok=True)
    token = secrets.token_hex(32)
    token_path = state_dir / "fail-next-start"
    token_path.write_text(token, encoding="utf-8")
    token_path.chmod(0o600)
    attempt_started_at = datetime.now(timezone.utc).isoformat()
    started = _run([SYSTEMCTL, "--user", "start", FIXTURE_UNIT])
    active_state, marker_count = _wait_for_synthetic_failure(attempt_started_at)
    ok = started.returncode != 0 and active_state == "failed" and marker_count == 1 and not token_path.exists()
    print(
        json.dumps(
            {
                "schema_version": "v1",
                "unit": FIXTURE_UNIT,
                "prepared": ok,
                "initial_start_failed": started.returncode != 0,
                "active_state": active_state,
                "synthetic_failure_lines": marker_count,
                "controller_must_not_run_after_task_start": True,
            },
            sort_keys=True,
        )
    )
    return 0 if ok else 1


def _wait_for_synthetic_failure(since: str) -> tuple[str, int]:
    deadline = time.monotonic() + 5.0
    active_state = "unknown"
    marker_count = 0
    while time.monotonic() < deadline:
        state = _run([SYSTEMCTL, "--user", "show", FIXTURE_UNIT, "--property=ActiveState", "--value"])
        journal = _run([JOURNALCTL, "--user", "-u", FIXTURE_UNIT, "--since", since, "-n", "20", "--no-pager", "-o", "cat"])
        active_state = state.stdout.strip()
        marker_count = sum(SYNTHETIC_FAILURE_MARKER in line for line in journal.stdout.splitlines())
        if active_state == "failed" and marker_count == 1:
            break
        time.sleep(0.1)
    return active_state, marker_count


def _run(argv: list[str]) -> subprocess.CompletedProcess[str]:
    if not argv or argv[0] not in {SYSTEMCTL, JOURNALCTL} or FIXTURE_UNIT not in argv:
        raise RuntimeError("controller allows only fixed fixture commands")
    try:
        return subprocess.run(argv, capture_output=True, text=True, timeout=20, check=False)
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        return subprocess.CompletedProcess(argv, 127, "", type(exc).__name__)


if __name__ == "__main__":
    sys.exit(main())
