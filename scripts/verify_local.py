from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    base_env = os.environ.copy()
    base_env["PYTHONDONTWRITEBYTECODE"] = "1"
    base_env["VIBEOS_MODEL_PROVIDER"] = base_env.get("VIBEOS_MODEL_PROVIDER", "local")
    base_env["VIBEOS_STATE_DIR"] = str(ROOT / ".vibeos-verify" / timestamp_slug())
    pythonpath_entries = [str(ROOT / "src")]
    if base_env.get("PYTHONPATH"):
        pythonpath_entries.append(base_env["PYTHONPATH"])
    base_env["PYTHONPATH"] = os.pathsep.join(pythonpath_entries)
    cli_env = dict(base_env)
    cli_env["VIBEOS_PREFER_LOCAL_BROKER"] = cli_env.get("VIBEOS_PREFER_LOCAL_BROKER", "1")

    checks = [
        run_check("pytest", [sys.executable, "-m", "pytest", "-p", "no:cacheprovider"], base_env),
        run_check("doctor", [sys.executable, "-m", "vibeos.cli", "doctor", "--json"], cli_env),
        run_check("capabilities", [sys.executable, "-m", "vibeos.cli", "capabilities", "--json"], cli_env),
        run_check("l1_open_dry_run", [sys.executable, "-m", "vibeos.cli", "ask", "open browser", "--dry-run", "--json"], cli_env),
        run_check("l2_review_required", [sys.executable, "-m", "vibeos.cli", "ask", "close browser", "--json"], cli_env, allow_failure=True),
        run_check("reviews_pending", [sys.executable, "-m", "vibeos.cli", "reviews", "pending", "--json"], cli_env),
        run_check("l3_rejected", [sys.executable, "-m", "vibeos.cli", "ask", "delete downloads", "--json"], cli_env, allow_failure=True),
        run_check("vm_evidence_safe", [sys.executable, "scripts/collect_vm_evidence.py"], base_env),
    ]

    review_id = extract_review_id(checks[4]["stdout"])
    if review_id:
        checks.append(
            run_check(
                "approve_review_dry_run",
                [sys.executable, "-m", "vibeos.cli", "approve", review_id, "--dry-run", "--json"],
                cli_env,
            )
        )
    else:
        checks.append(
            {
                "name": "approve_review_dry_run",
                "ok": False,
                "returncode": None,
                "stdout": "",
                "stderr": "missing review_id from l2_review_required",
            }
        )

    report = {
        "overall": "ok" if all(check["ok"] for check in checks) else "fail",
        "checks": checks,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["overall"] == "ok" else 1


def run_check(name: str, command: list[str], env: dict[str, str], allow_failure: bool = False) -> dict[str, object]:
    completed = subprocess.run(command, cwd=ROOT, env=env, capture_output=True, text=True, timeout=120)
    ok = completed.returncode == 0 or allow_failure and is_expected_nonzero(name, completed.stdout)
    return {
        "name": name,
        "ok": ok,
        "returncode": completed.returncode,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
    }


def is_expected_nonzero(name: str, stdout: str) -> bool:
    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError:
        return False
    expected_status = {
        "l2_review_required": "review_required",
        "l3_rejected": "rejected",
    }.get(name)
    return payload.get("status") == expected_status


def extract_review_id(stdout: str) -> str | None:
    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError:
        return None
    review_id = payload.get("review_id")
    return str(review_id) if review_id else None


def timestamp_slug() -> str:
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")


if __name__ == "__main__":
    raise SystemExit(main())
