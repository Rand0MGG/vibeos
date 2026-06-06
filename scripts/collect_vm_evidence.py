from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from vibeos.windows import unwrap_gdbus_string


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
    if args.real:
        env["VIBEOS_REQUIRE_DAEMON"] = "1"
    state_dir = None
    if not args.session_state:
        state_dir = out_dir / "state" / run_slug
        state_dir.mkdir(parents=True, exist_ok=True)
        env["VIBEOS_STATE_DIR"] = str(state_dir)
    steps: list[dict[str, Any]] = []

    if args.real:
        steps.extend(collect_real_daemon_evidence(env))

    steps.append(
        run_json_step(
            "doctor",
            cli("doctor", "--json"),
            env,
            validator=lambda value: doctor_ok(value, strict=args.real),
            category="baseline",
        )
    )
    steps.append(run_json_step("capabilities", cli("capabilities", "--json"), env, validator=capabilities_ok, category="baseline"))
    steps.append(run_json_step("apps", cli("apps"), env, validator=lambda value: isinstance(value, list), category="baseline"))
    steps.append(run_json_step("windows", cli("windows"), env, validator=lambda value: isinstance(value, list), category="baseline"))
    steps.append(
        run_json_step(
            "system_status",
            cli("ask", "status", "--json"),
            env,
            expected_status="executed",
            validator=command_transport_ok,
            category="baseline",
        )
    )
    steps.append(
        run_json_step(
            "open_browser_dry_run",
            cli("ask", "open browser", "--dry-run", "--json"),
            env,
            expected_status="dry_run",
            validator=command_transport_ok,
            category="baseline",
        )
    )

    close_review = run_json_step(
        "window_close_review_required",
        cli("ask", "close browser", "--json"),
        env,
        expected_status="review_required",
        expected_returncodes={1},
        validator=command_transport_ok,
        category="review_flow",
    )
    steps.append(close_review)
    review_id = extract_review_id(close_review.get("parsed"))
    steps.append(run_json_step("reviews_pending", cli("reviews", "pending", "--json"), env, validator=pending_reviews_ok, category="review_flow"))
    if review_id:
        steps.append(
            run_json_step(
                "window_close_approve_dry_run",
                cli("approve", review_id, "--dry-run", "--json"),
                env,
                expected_status="dry_run",
                validator=command_transport_ok,
                category="review_flow",
                depends_on=["window_close_review_required"],
            )
        )
    else:
        steps.append(blocked_step("window_close_approve_dry_run", "missing review_id from window_close_review_required", depends_on=["window_close_review_required"], category="review_flow"))

    reject_review = run_json_step(
        "window_close_reject_review_required",
        cli("ask", "close terminal", "--json"),
        env,
        expected_status="review_required",
        expected_returncodes={1},
        validator=command_transport_ok,
        category="review_flow",
    )
    steps.append(reject_review)
    reject_review_id = extract_review_id(reject_review.get("parsed"))
    if reject_review_id:
        steps.append(
            run_json_step(
                "window_close_reject",
                cli("reviews", "reject", reject_review_id, "--json"),
                env,
                expected_status="rejected",
                expected_returncodes={0},
                validator=command_transport_ok,
                category="review_flow",
                depends_on=["window_close_reject_review_required"],
            )
        )
        steps.append(
            run_json_step(
                "window_close_rejected_review_cannot_approve",
                cli("approve", reject_review_id, "--json"),
                env,
                expected_status="rejected",
                expected_returncodes={1},
                validator=command_transport_ok,
                category="review_flow",
                depends_on=["window_close_reject"],
            )
        )
    else:
        steps.append(blocked_step("window_close_reject", "missing review_id from window_close_reject_review_required", depends_on=["window_close_reject_review_required"], category="review_flow"))
        steps.append(
            blocked_step(
                "window_close_rejected_review_cannot_approve",
                "reject step did not produce a review transition",
                depends_on=["window_close_reject"],
                category="review_flow",
            )
        )

    steps.append(
        run_json_step(
            "delete_rejected",
            cli("ask", "delete downloads", "--json"),
            env,
            expected_status="rejected",
            expected_returncodes={1},
            validator=command_transport_ok,
            category="policy",
        )
    )
    steps.append(run_json_step("target_policy_constraints", target_policy_command(), env, validator=target_policy_ok, category="policy"))

    if args.real:
        collect_real_action_evidence(steps, env)

    steps.append(run_json_step("audit_tail", cli("audit", "tail", "-n", "20"), env, validator=audit_tail_ok, category="audit"))

    summary = build_summary(steps)

    report = {
        "generated_at": datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
        "mode": "real" if args.real else "safe",
        "state_dir": str(state_dir) if state_dir else "default",
        "overall": "ok" if not summary["failed_steps"] and not summary["blocked_steps"] else "fail",
        "summary": summary,
        "steps": steps,
    }
    path = out_dir / f"vibeos_vm_evidence_{run_slug}.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print(
        json.dumps(
            {
                "overall": report["overall"],
                "mode": report["mode"],
                "report": str(path),
                "failed_steps": summary["failed_steps"],
                "blocked_steps": summary["blocked_steps"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if report["overall"] == "ok" else 1


def collect_real_action_evidence(steps: list[dict[str, Any]], env: dict[str, str]) -> None:
    steps.append(
        run_json_step(
            "contract_clipboard_content_alias",
            direct_intent_dry_run_command("clipboard.write", {"content": "VibeOS evidence"}),
            env,
            expected_status="review_required",
            expected_returncodes={1},
            validator=lambda value: contract_probe_ok(value, "clipboard.write", "text", "VibeOS evidence"),
            category="contract",
        )
    )
    steps.append(
        run_json_step(
            "contract_open_uri_name_alias",
            direct_intent_dry_run_command("portal.open_uri", {"name": "https://example.com", "kind": "uri"}),
            env,
            expected_status="review_required",
            expected_returncodes={1},
            validator=lambda value: contract_probe_ok(value, "portal.open_uri", "uri", "https://example.com"),
            category="contract",
        )
    )
    steps.append(
        run_json_step(
            "real_notification_send",
            cli("ask", "notify VibeOS evidence", "--json"),
            env,
            expected_status="executed",
            validator=command_transport_ok,
            category="real_action",
        )
    )

    clipboard_review = run_json_step(
        "real_clipboard_review_required",
        cli("ask", "clipboard VibeOS evidence", "--json"),
        env,
        expected_status="review_required",
        expected_returncodes={1},
        validator=command_transport_ok,
        category="real_action",
    )
    steps.append(clipboard_review)
    steps.append(
        run_json_step(
            "real_clipboard_adapter_direct",
            direct_clipboard_adapter_command("VibeOS evidence"),
            env,
            category="diagnostic",
        )
    )
    clipboard_review_id = extract_review_id(clipboard_review.get("parsed"))
    if clipboard_review_id:
        steps.append(
            run_json_step(
                "real_clipboard_approve",
                cli("approve", clipboard_review_id, "--json"),
                env,
                expected_status="executed",
                validator=command_transport_ok,
                category="real_action",
                depends_on=["real_clipboard_review_required"],
            )
        )
    else:
        steps.append(blocked_step("real_clipboard_approve", "missing review_id from real_clipboard_review_required", depends_on=["real_clipboard_review_required"], category="real_action"))

    uri_review = run_json_step(
        "real_open_uri_review_required",
        cli("ask", "open https://example.com", "--json"),
        env,
        expected_status="review_required",
        expected_returncodes={1},
        validator=command_transport_ok,
        category="real_action",
    )
    steps.append(uri_review)
    uri_review_id = extract_review_id(uri_review.get("parsed"))
    if uri_review_id:
        steps.append(
            run_json_step(
                "real_open_uri_approve",
                cli("approve", uri_review_id, "--json"),
                env,
                expected_status="executed",
                validator=command_transport_ok,
                category="real_action",
                depends_on=["real_open_uri_review_required"],
            )
        )
    else:
        steps.append(blocked_step("real_open_uri_approve", "missing review_id from real_open_uri_review_required", depends_on=["real_open_uri_review_required"], category="real_action"))


def collect_real_daemon_evidence(env: dict[str, str]) -> list[dict[str, Any]]:
    return [
        run_text_step(
            "systemd_vibed_service_active",
            ["systemctl", "--user", "is-active", "vibed.service"],
            env,
            validator=systemd_active_ok,
            category="daemon",
        ),
        run_text_step(
            "dbus_agent_introspect",
            [
                "gdbus",
                "introspect",
                "--session",
                "--dest",
                "org.vibeos.Agent",
                "--object-path",
                "/org/vibeos/Agent",
            ],
            env,
            validator=dbus_introspect_ok,
            category="daemon",
        ),
        run_json_text_step(
            "dbus_agent_status",
            [
                "gdbus",
                "call",
                "--session",
                "--dest",
                "org.vibeos.Agent",
                "--object-path",
                "/org/vibeos/Agent",
                "--method",
                "org.vibeos.Agent.Status",
            ],
            env,
            parser=parse_gdbus_json,
            validator=lambda value: daemon_status_ok(value, required_transports={"dbus", "http"}),
            category="daemon",
        ),
        run_value_step(
            "http_daemon_status",
            lambda: fetch_http_json("http://127.0.0.1:8765/v1/status"),
            validator=lambda value: daemon_status_ok(value, required_transports={"dbus", "http"}),
            meta={"url": "http://127.0.0.1:8765/v1/status"},
            category="daemon",
        ),
    ]


def run_json_step(
    name: str,
    command: list[str],
    env: dict[str, str],
    expected_status: str | None = None,
    expected_returncodes: set[int] | None = None,
    validator=None,
    category: str = "general",
    depends_on: list[str] | None = None,
) -> dict[str, Any]:
    expected_returncodes = expected_returncodes or {0}
    completed = subprocess.run(command, cwd=ROOT, env=env, capture_output=True, text=True, timeout=180)
    parsed = parse_json(completed.stdout)
    ok = completed.returncode in expected_returncodes and parsed is not None
    if expected_status is not None:
        ok = ok and isinstance(parsed, dict) and parsed.get("status") == expected_status
    if validator is not None:
        ok = ok and bool(validator(parsed))
    return annotate_step(
        {
        "name": name,
        "ok": ok,
        "command": command,
        "returncode": completed.returncode,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
        "parsed": parsed,
        },
        category=category,
        depends_on=depends_on,
    )


def run_text_step(
    name: str,
    command: list[str],
    env: dict[str, str],
    expected_returncodes: set[int] | None = None,
    validator=None,
    category: str = "general",
    depends_on: list[str] | None = None,
) -> dict[str, Any]:
    expected_returncodes = expected_returncodes or {0}
    completed = subprocess.run(command, cwd=ROOT, env=env, capture_output=True, text=True, timeout=180)
    stdout = completed.stdout
    ok = completed.returncode in expected_returncodes
    if validator is not None:
        ok = ok and bool(validator(stdout))
    return annotate_step(
        {
        "name": name,
        "ok": ok,
        "command": command,
        "returncode": completed.returncode,
        "stdout": stdout,
        "stderr": completed.stderr,
        "parsed": None,
        },
        category=category,
        depends_on=depends_on,
    )


def run_json_text_step(
    name: str,
    command: list[str],
    env: dict[str, str],
    parser,
    expected_returncodes: set[int] | None = None,
    validator=None,
    category: str = "general",
    depends_on: list[str] | None = None,
) -> dict[str, Any]:
    expected_returncodes = expected_returncodes or {0}
    completed = subprocess.run(command, cwd=ROOT, env=env, capture_output=True, text=True, timeout=180)
    parsed = None
    try:
        parsed = parser(completed.stdout)
    except ValueError:
        parsed = None
    ok = completed.returncode in expected_returncodes and parsed is not None
    if validator is not None:
        ok = ok and bool(validator(parsed))
    return annotate_step(
        {
        "name": name,
        "ok": ok,
        "command": command,
        "returncode": completed.returncode,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
        "parsed": parsed,
        },
        category=category,
        depends_on=depends_on,
    )


def run_value_step(
    name: str,
    producer,
    validator=None,
    meta: dict[str, Any] | None = None,
    category: str = "general",
    depends_on: list[str] | None = None,
) -> dict[str, Any]:
    try:
        parsed = producer()
    except Exception as exc:
        return annotate_step(
            {
                "name": name,
                "ok": False,
                "command": [],
                "returncode": None,
                "stdout": "",
                "stderr": str(exc),
                "parsed": None,
                "meta": meta or {},
            },
            category=category,
            depends_on=depends_on,
        )
    ok = validator(parsed) if validator is not None else True
    return annotate_step(
        {
            "name": name,
            "ok": bool(ok),
            "command": [],
            "returncode": 0,
            "stdout": json.dumps(parsed, ensure_ascii=False),
            "stderr": "",
            "parsed": parsed,
            "meta": meta or {},
        },
        category=category,
        depends_on=depends_on,
    )


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


def direct_intent_dry_run_command(action: str, target: dict[str, Any]) -> list[str]:
    code = (
        "import json;"
        "from dataclasses import asdict;"
        "from vibeos.broker import CapabilityBroker;"
        "from vibeos.intent import IntentBroker;"
        "from vibeos.models import CommandRequest, Intent;"
        f"target={target!r};"
        f"action={action!r};"
        "Stub=type('Stub',(IntentBroker,),{'parse':lambda self, utterance: Intent(action=action, target=target, reason='contract probe')});"
        "result=CapabilityBroker(intent_broker=Stub()).handle(CommandRequest('contract probe', dry_run=True));"
        "print(json.dumps(asdict(result), ensure_ascii=False));"
        "raise SystemExit(0 if result.status == 'dry_run' else 1)"
    )
    return [sys.executable, "-c", code]


def direct_clipboard_adapter_command(text: str) -> list[str]:
    code = (
        "import json;"
        "from vibeos.clipboard import ClipboardAdapter;"
        f"result=ClipboardAdapter().write({text!r});"
        "print(json.dumps(result, ensure_ascii=False));"
        "raise SystemExit(0 if result.get('status') == 'written' else 1)"
    )
    return [sys.executable, "-c", code]


def parse_json(value: str) -> Any:
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return None


def parse_gdbus_json(value: str) -> Any:
    unwrapped = unwrap_gdbus_string(value.strip())
    return json.loads(unwrapped)


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
        "runtime_entry",
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


def contract_probe_ok(value: Any, action: str, canonical_key: str, expected_value: str) -> bool:
    if not isinstance(value, dict):
        return False
    if value.get("status") != "review_required":
        return False
    payload = value.get("result")
    if not isinstance(payload, dict):
        return False
    validation = payload.get("validation")
    if not isinstance(validation, dict) or not validation.get("ok"):
        return False
    plan = payload.get("plan")
    if not isinstance(plan, dict):
        return False
    steps = plan.get("steps")
    if not isinstance(steps, list) or not steps:
        return False
    step = steps[0]
    if not isinstance(step, dict) or step.get("action") != action:
        return False
    target = step.get("target")
    return isinstance(target, dict) and target.get(canonical_key) == expected_value


def command_transport_ok(value: Any) -> bool:
    return isinstance(value, dict) and bool(value.get("transport"))


def audit_tail_ok(value: Any) -> bool:
    if not isinstance(value, list):
        return False
    command_entries = [item for item in value if isinstance(item, dict) and item.get("intent")]
    return bool(command_entries) and all(item.get("transport") for item in command_entries)


def systemd_active_ok(value: str) -> bool:
    return value.strip() == "active"


def dbus_introspect_ok(value: str) -> bool:
    return "org.vibeos.Agent" in value and "CommandRequest" in value and "Status" in value


def daemon_status_ok(value: Any, required_transports: set[str] | None = None) -> bool:
    if not isinstance(value, dict):
        return False
    if value.get("status") != "ok" or value.get("service") != "vibed":
        return False
    transports = value.get("transports")
    if not isinstance(transports, list):
        return False
    if required_transports is None:
        return True
    return required_transports.issubset({str(item) for item in transports})


def fetch_http_json(url: str) -> Any:
    import urllib.request

    with urllib.request.urlopen(url, timeout=10) as response:
        body = response.read().decode("utf-8")
    return json.loads(body)


def blocked_step(name: str, message: str, *, depends_on: list[str] | None = None, category: str = "general") -> dict[str, Any]:
    return annotate_step(
        {
        "name": name,
        "ok": False,
        "command": [],
        "returncode": None,
        "stdout": "",
        "stderr": message,
        "parsed": None,
        "status": "blocked",
        },
        category=category,
        depends_on=depends_on,
    )


def annotate_step(step: dict[str, Any], *, category: str, depends_on: list[str] | None = None) -> dict[str, Any]:
    status = step.get("status")
    if status is None:
        status = "ok" if step.get("ok") else "fail"
    step["status"] = status
    step["ok"] = status == "ok"
    step["category"] = category
    step["depends_on"] = depends_on or []
    step["root_cause"] = status == "fail"
    hint = infer_hint(step)
    if hint:
        step["hint"] = hint
    return step


def build_summary(steps: list[dict[str, Any]]) -> dict[str, Any]:
    failed_steps = [step["name"] for step in steps if step.get("status") == "fail"]
    blocked_steps = [step["name"] for step in steps if step.get("status") == "blocked"]
    ok_steps = [step["name"] for step in steps if step.get("status") == "ok"]
    categories: dict[str, dict[str, int]] = {}
    for step in steps:
        category = str(step.get("category") or "general")
        bucket = categories.setdefault(category, {"ok": 0, "fail": 0, "blocked": 0})
        bucket[str(step.get("status") or "fail")] += 1
    return {
        "total_steps": len(steps),
        "ok_steps": ok_steps,
        "failed_steps": failed_steps,
        "blocked_steps": blocked_steps,
        "root_failures": failed_steps,
        "categories": categories,
    }


def infer_hint(step: dict[str, Any]) -> str | None:
    if step.get("status") == "blocked":
        depends_on = step.get("depends_on") or []
        if depends_on:
            return f"Dependent step was skipped because {depends_on[0]} did not produce the required review state."
        return "Dependent step was skipped because a prerequisite did not succeed."

    stderr = str(step.get("stderr") or "")
    parsed = step.get("parsed")
    message = ""
    if isinstance(parsed, dict):
        message = str(parsed.get("message") or "")

    combined = f"{stderr}\n{message}".strip()
    if not combined:
        return None
    if "requires non-empty text" in combined:
        return "The clipboard request reached policy validation but the target payload did not expose text in the expected field."
    if "requires a URI target" in combined:
        return "The URI request reached policy validation but the target payload did not expose a usable URI field."
    if "UnknownMethod" in combined and "AuditTail" in combined:
        return "The installed daemon does not expose the AuditTail method expected by this CLI build; reinstall or restart vibed after syncing code."
    if "timed out" in combined and "CommandRequest" in combined:
        return "The daemon accepted the request but the underlying action did not finish before the client timeout; inspect the adapter or portal helper."
    if "timed out" in combined and "wl-copy" in combined:
        return "The clipboard helper did not exit promptly; wl-copy may be holding the process open in this session."
    if "ServiceUnknown" in combined or "org.freedesktop.DBus.Error.ServiceUnknown" in combined:
        return "The target D-Bus service is not registered in the current user session."
    return None


def timestamp_slug() -> str:
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")


if __name__ == "__main__":
    raise SystemExit(main())
