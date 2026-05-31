from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser(description="Collect VibeOS Linux VM acceptance evidence.")
    parser.add_argument("--real", action="store_true", help="execute real notification, URI, and clipboard actions")
    parser.add_argument("--out-dir", default=".vibeos-vm-evidence", help="directory for the evidence JSON report")
    parser.add_argument("--session-state", action="store_true", help="use the normal VibeOS state directory instead of an isolated evidence state directory")
    args = parser.parse_args()

    out_dir = Path(args.out_dir).expanduser()
    if not out_dir.is_absolute():
        out_dir = ROOT / out_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    run_slug = timestamp_slug()
    env = os.environ.copy()
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    state_dir = None
    if not args.session_state:
        state_dir = out_dir / "state" / run_slug
        state_dir.mkdir(parents=True, exist_ok=True)
        env["VIBEOS_STATE_DIR"] = str(state_dir)
    steps: list[dict[str, Any]] = []

    steps.append(run_json_step("doctor", cli("doctor", "--json"), env, validator=lambda value: doctor_ok(value, strict=args.real)))
    steps.append(run_json_step("capabilities", cli("capabilities", "--json"), env, validator=capabilities_ok))
    steps.append(run_json_step("apps", cli("apps"), env, validator=lambda value: isinstance(value, list)))
    steps.append(run_json_step("windows", cli("windows"), env, validator=lambda value: isinstance(value, list)))
    steps.append(run_json_step("system_status", cli("ask", "status", "--json"), env, expected_status="executed"))
    steps.append(run_json_step("open_browser_dry_run", cli("ask", "open browser", "--dry-run", "--json"), env, expected_status="dry_run"))

    close_review = run_json_step(
        "window_close_review_required",
        cli("ask", "close browser", "--json"),
        env,
        expected_status="review_required",
        expected_returncodes={1},
    )
    steps.append(close_review)
    review_id = extract_review_id(close_review.get("parsed"))
    steps.append(run_json_step("reviews_pending", cli("reviews", "pending", "--json"), env, validator=pending_reviews_ok))
    if review_id:
        steps.append(run_json_step("window_close_approve_dry_run", cli("approve", review_id, "--dry-run", "--json"), env, expected_status="dry_run"))
    else:
        steps.append(manual_failure("window_close_approve_dry_run", "missing review_id from window_close_review_required"))

    reject_review = run_json_step(
        "window_close_reject_review_required",
        cli("ask", "close terminal", "--json"),
        env,
        expected_status="review_required",
        expected_returncodes={1},
    )
    steps.append(reject_review)
    reject_review_id = extract_review_id(reject_review.get("parsed"))
    if reject_review_id:
        steps.append(run_json_step("window_close_reject", cli("reviews", "reject", reject_review_id, "--json"), env, expected_status="rejected", expected_returncodes={0}))
        steps.append(run_json_step("window_close_rejected_review_cannot_approve", cli("approve", reject_review_id, "--json"), env, expected_status="rejected", expected_returncodes={1}))
    else:
        steps.append(manual_failure("window_close_reject", "missing review_id from window_close_reject_review_required"))

    steps.append(run_json_step("delete_rejected", cli("ask", "delete downloads", "--json"), env, expected_status="rejected", expected_returncodes={1}))
    steps.append(run_json_step("target_policy_constraints", target_policy_command(), env, validator=target_policy_ok))

    if args.real:
        collect_real_action_evidence(steps, env)

    steps.append(run_json_step("audit_tail", cli("audit", "tail", "-n", "20"), env, validator=lambda value: isinstance(value, list)))

    report = {
        "generated_at": datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
        "mode": "real" if args.real else "safe",
        "state_dir": str(state_dir) if state_dir else "default",
        "overall": "ok" if all(step["ok"] for step in steps) else "fail",
        "steps": steps,
    }
    path = out_dir / f"vibeos_vm_evidence_{run_slug}.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print(json.dumps({"overall": report["overall"], "mode": report["mode"], "report": str(path)}, ensure_ascii=False, indent=2))
    return 0 if report["overall"] == "ok" else 1


def collect_real_action_evidence(steps: list[dict[str, Any]], env: dict[str, str]) -> None:
    steps.append(run_json_step("real_notification_send", cli("ask", "notify VibeOS evidence", "--json"), env, expected_status="executed"))

    clipboard_review = run_json_step(
        "real_clipboard_review_required",
        cli("ask", "clipboard VibeOS evidence", "--json"),
        env,
        expected_status="review_required",
        expected_returncodes={1},
    )
    steps.append(clipboard_review)
    clipboard_review_id = extract_review_id(clipboard_review.get("parsed"))
    if clipboard_review_id:
        steps.append(run_json_step("real_clipboard_approve", cli("approve", clipboard_review_id, "--json"), env, expected_status="executed"))
    else:
        steps.append(manual_failure("real_clipboard_approve", "missing review_id from real_clipboard_review_required"))

    uri_review = run_json_step(
        "real_open_uri_review_required",
        cli("ask", "open https://example.com", "--json"),
        env,
        expected_status="review_required",
        expected_returncodes={1},
    )
    steps.append(uri_review)
    uri_review_id = extract_review_id(uri_review.get("parsed"))
    if uri_review_id:
        steps.append(run_json_step("real_open_uri_approve", cli("approve", uri_review_id, "--json"), env, expected_status="executed"))
    else:
        steps.append(manual_failure("real_open_uri_approve", "missing review_id from real_open_uri_review_required"))


def run_json_step(
    name: str,
    command: list[str],
    env: dict[str, str],
    expected_status: str | None = None,
    expected_returncodes: set[int] | None = None,
    validator=None,
) -> dict[str, Any]:
    expected_returncodes = expected_returncodes or {0}
    completed = subprocess.run(command, cwd=ROOT, env=env, capture_output=True, text=True, timeout=180)
    parsed = parse_json(completed.stdout)
    ok = completed.returncode in expected_returncodes and parsed is not None
    if expected_status is not None:
        ok = ok and isinstance(parsed, dict) and parsed.get("status") == expected_status
    if validator is not None:
        ok = ok and bool(validator(parsed))
    return {
        "name": name,
        "ok": ok,
        "command": command,
        "returncode": completed.returncode,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
        "parsed": parsed,
    }


def cli(*args: str) -> list[str]:
    return [sys.executable, "-m", "vibeos.cli", *args]


def target_policy_command() -> list[str]:
    code = (
        "import json;"
        "from dataclasses import asdict;"
        "from vibeos.models import Intent;"
        "from vibeos.permissions import PermissionPolicy;"
        "review=PermissionPolicy().review(Intent(action='portal.open_uri', target={'uri':'file:///etc/passwd'}));"
        "print(json.dumps(asdict(review), ensure_ascii=False));"
        "raise SystemExit(0 if review.risk_level == 'L3' and not review.allowed else 1)"
    )
    return [sys.executable, "-c", code]


def parse_json(value: str) -> Any:
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return None


def extract_review_id(value: Any) -> str | None:
    if isinstance(value, dict) and value.get("review_id"):
        return str(value["review_id"])
    return None


def doctor_ok(value: Any, strict: bool = False) -> bool:
    if not isinstance(value, dict) or value.get("summary", {}).get("overall") not in {"ok", "warn"}:
        return False
    if not strict:
        return True
    checks = {check.get("name"): check.get("status") for check in value.get("checks", []) if isinstance(check, dict)}
    required_ok = {
        "platform",
        "session_type",
        "gnome_shell",
        "gdbus",
        "xdg_desktop_portal",
        "systemd_user",
        "vibed_service",
        "gnome_extension_bridge",
        "app_registry",
        "action_helpers",
    }
    return all(checks.get(name) == "ok" for name in required_ok)


def capabilities_ok(value: Any) -> bool:
    return isinstance(value, dict) and bool(value.get("capability_details")) and bool(value.get("permission_policy"))


def pending_reviews_ok(value: Any) -> bool:
    return isinstance(value, list) and any(isinstance(item, dict) and item.get("status") == "pending" for item in value)


def target_policy_ok(value: Any) -> bool:
    return isinstance(value, dict) and value.get("risk_level") == "L3" and value.get("allowed") is False


def manual_failure(name: str, message: str) -> dict[str, Any]:
    return {
        "name": name,
        "ok": False,
        "command": [],
        "returncode": None,
        "stdout": "",
        "stderr": message,
        "parsed": None,
    }


def timestamp_slug() -> str:
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")


if __name__ == "__main__":
    raise SystemExit(main())
