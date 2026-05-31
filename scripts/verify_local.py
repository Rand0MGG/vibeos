from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    env = os.environ.copy()
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["VIBEOS_MODEL_PROVIDER"] = env.get("VIBEOS_MODEL_PROVIDER", "local")
    env["VIBEOS_STATE_DIR"] = str(ROOT / ".vibeos-verify" / timestamp_slug())

    checks = [
        run_check("pytest", [sys.executable, "-m", "pytest", "-p", "no:cacheprovider"], env),
        run_check("doctor", [sys.executable, "-m", "vibeos.cli", "doctor", "--json"], env),
        run_check("capabilities", [sys.executable, "-m", "vibeos.cli", "capabilities", "--json"], env),
        run_check("l1_open_dry_run", [sys.executable, "-m", "vibeos.cli", "ask", "打开浏览器", "--dry-run", "--json"], env),
        run_check("l2_review_required", [sys.executable, "-m", "vibeos.cli", "ask", "关闭浏览器", "--json"], env, allow_failure=True),
        run_check("reviews_pending", [sys.executable, "-m", "vibeos.cli", "reviews", "pending", "--json"], env),
        run_check("l3_rejected", [sys.executable, "-m", "vibeos.cli", "ask", "删除下载目录", "--json"], env, allow_failure=True),
        run_check("vm_evidence_safe", [sys.executable, "scripts/collect_vm_evidence.py"], env),
    ]

    review_id = extract_review_id(checks[4]["stdout"])
    if review_id:
        checks.append(
            run_check(
                "approve_review_dry_run",
                [sys.executable, "-m", "vibeos.cli", "approve", review_id, "--dry-run", "--json"],
                env,
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
