from scripts.collect_vm_evidence import (
    audit_tail_ok,
    blocked_step,
    build_summary,
    command_transport_ok,
    collect_real_action_evidence,
    collect_state_diagnostics,
    compact_command_result,
    daemon_status_ok,
    dbus_introspect_ok,
    doctor_ok,
    enrich_step_context,
    extract_trace_run_id,
    infer_hint,
    parse_gdbus_json,
    systemd_active_ok,
)
from pathlib import Path
import json


def test_doctor_ok_safe_mode_accepts_warnings() -> None:
    report = {"summary": {"overall": "warn"}, "checks": []}
    assert doctor_ok(report)


def test_doctor_ok_strict_requires_linux_session_checks() -> None:
    report = {
        "summary": {"overall": "warn"},
        "checks": [
            {"name": "platform", "status": "ok"},
            {"name": "session_type", "status": "ok"},
            {"name": "gnome_shell", "status": "ok"},
            {"name": "gdbus", "status": "ok"},
            {"name": "xdg_desktop_portal", "status": "ok"},
            {"name": "systemd_user", "status": "ok"},
            {"name": "vibed_service", "status": "ok"},
            {"name": "runtime_entry", "status": "ok"},
            {"name": "gnome_extension_bridge", "status": "ok"},
            {"name": "app_registry", "status": "ok"},
            {"name": "action_helpers", "status": "ok"},
            {"name": "model_config", "status": "warn"},
        ],
    }
    assert doctor_ok(report, strict=True)


def test_doctor_ok_strict_rejects_missing_bridge() -> None:
    report = {
        "summary": {"overall": "warn"},
        "checks": [
            {"name": "platform", "status": "ok"},
            {"name": "session_type", "status": "ok"},
            {"name": "gnome_extension_bridge", "status": "warn"},
        ],
    }
    assert not doctor_ok(report, strict=True)


def test_doctor_ok_strict_rejects_warn_runtime_entry() -> None:
    report = {
        "summary": {"overall": "warn"},
        "checks": [
            {"name": "platform", "status": "ok"},
            {"name": "session_type", "status": "ok"},
            {"name": "gnome_shell", "status": "ok"},
            {"name": "gdbus", "status": "ok"},
            {"name": "xdg_desktop_portal", "status": "ok"},
            {"name": "systemd_user", "status": "ok"},
            {"name": "vibed_service", "status": "ok"},
            {"name": "runtime_entry", "status": "warn"},
            {"name": "gnome_extension_bridge", "status": "ok"},
            {"name": "app_registry", "status": "ok"},
            {"name": "action_helpers", "status": "ok"},
        ],
    }
    assert not doctor_ok(report, strict=True)


def test_command_transport_ok_requires_transport() -> None:
    assert command_transport_ok({"status": "executed", "transport": "local"})
    assert not command_transport_ok({"status": "executed"})


def test_audit_tail_ok_requires_transport_for_command_entries() -> None:
    assert audit_tail_ok([{"intent": {"action": "app.open"}, "transport": "dbus"}])
    assert not audit_tail_ok([{"intent": {"action": "app.open"}}])


def test_systemd_active_ok_requires_active() -> None:
    assert systemd_active_ok("active\n")
    assert not systemd_active_ok("inactive\n")


def test_dbus_introspect_ok_requires_expected_methods() -> None:
    assert dbus_introspect_ok("interface org.vibeos.Agent\nmethod CommandRequest\nmethod Status\n")
    assert not dbus_introspect_ok("interface org.vibeos.Agent\nmethod Command\n")


def test_parse_gdbus_json_unwraps_json_string() -> None:
    assert parse_gdbus_json("""('{"status":"ok","service":"vibed"}',)""") == {"status": "ok", "service": "vibed"}


def test_daemon_status_ok_requires_expected_transports() -> None:
    payload = {"status": "ok", "service": "vibed", "transports": ["dbus", "http"]}
    assert daemon_status_ok(payload, required_transports={"dbus", "http"})
    assert not daemon_status_ok({"status": "ok", "service": "vibed", "transports": ["dbus"]}, required_transports={"dbus", "http"})


def test_blocked_step_is_not_marked_as_root_failure() -> None:
    step = blocked_step("real_open_uri_approve", "missing review_id", depends_on=["real_open_uri_review_required"], category="real_action")
    assert step["status"] == "blocked"
    assert step["ok"] is False
    assert step["root_cause"] is False
    assert step["depends_on"] == ["real_open_uri_review_required"]


def test_build_summary_separates_failed_and_blocked_steps() -> None:
    summary = build_summary(
        [
            {"name": "doctor", "status": "ok", "category": "baseline"},
            {"name": "real_clipboard_review_required", "status": "fail", "category": "real_action"},
            {"name": "real_clipboard_approve", "status": "blocked", "category": "real_action"},
        ]
    )
    assert summary["failed_steps"] == ["real_clipboard_review_required"]
    assert summary["blocked_steps"] == ["real_clipboard_approve"]
    assert summary["categories"]["real_action"] == {"ok": 0, "fail": 1, "blocked": 1}


def test_infer_hint_explains_missing_clipboard_text() -> None:
    step = {
        "status": "fail",
        "stderr": "",
        "parsed": {"message": "clipboard.write requires non-empty text."},
    }
    assert "expected field" in infer_hint(step)


def test_infer_hint_explains_blocked_dependency() -> None:
    step = {
        "status": "blocked",
        "depends_on": ["real_open_uri_review_required"],
    }
    assert "real_open_uri_review_required" in infer_hint(step)


def test_extract_trace_run_id_reads_command_result() -> None:
    assert extract_trace_run_id({"trace_run_id": "run_123"}) == "run_123"
    assert extract_trace_run_id({"status": "executed"}) is None


def test_compact_command_result_keeps_trace_and_plan_review_fields() -> None:
    payload = compact_command_result(
        {
            "status": "review_required",
            "transport": "dbus",
            "trace_run_id": "run_1",
            "review_id": "rev_1",
            "result": {
                "run": {"run_id": "run_1", "goal_id": "goal_1", "status": "needs_review"},
                "plan_review": {"status": "review_required", "review_id": "rev_1", "message": "approval required"},
            },
        }
    )

    assert payload["trace_run_id"] == "run_1"
    assert payload["run"]["goal_id"] == "goal_1"
    assert payload["plan_review"]["status"] == "review_required"


def test_enrich_step_context_surfaces_trace_and_review_ids(monkeypatch) -> None:
    monkeypatch.setattr(
        "scripts.collect_vm_evidence.collect_trace_artifacts",
        lambda run_id, env: {"run_id": run_id, "summary": {"status": "executed"}},
    )
    step = enrich_step_context(
        {
            "name": "real_open_uri_approve",
            "parsed": {"status": "executed", "review_id": "rev_1", "trace_run_id": "run_1"},
        },
        {"VIBEOS_STATE_DIR": "/tmp/test"},
    )

    assert step["review_id"] == "rev_1"
    assert step["trace_run_id"] == "run_1"
    assert step["trace"]["run_id"] == "run_1"


def test_collect_real_action_evidence_adds_reapprove_checks(monkeypatch) -> None:
    calls: list[str] = []

    def fake_run_json_step(name, *args, **kwargs):
        calls.append(name)
        parsed = {}
        if name in {"real_clipboard_review_required", "real_open_uri_review_required"}:
            parsed["review_id"] = f"rev_{name}"
        return {"name": name, "parsed": parsed}

    monkeypatch.setattr("scripts.collect_vm_evidence.run_json_step", fake_run_json_step)
    steps: list[dict[str, object]] = []
    collect_real_action_evidence(steps, {"VIBEOS_STATE_DIR": "/tmp/test"})

    assert "real_clipboard_reapprove_rejected" in calls
    assert "real_open_uri_reapprove_rejected" in calls


def test_collect_state_diagnostics_reads_audit_reviews_and_runs() -> None:
    state_dir = Path(".vibeos") / "vm-evidence-state-test"
    runs_dir = state_dir / "runs" / "2026-06-11" / "run_1"
    runs_dir.mkdir(parents=True, exist_ok=True)
    (state_dir / "audit.jsonl").write_text(json.dumps({"audit_id": "aud_1"}) + "\n", encoding="utf-8")
    (state_dir / "reviews.jsonl").write_text(json.dumps({"review_id": "rev_1"}) + "\n", encoding="utf-8")
    (runs_dir / "summary.json").write_text(json.dumps({"run_id": "run_1", "status": "failed"}), encoding="utf-8")

    payload = collect_state_diagnostics(state_dir)

    assert payload["audit_tail"][0]["audit_id"] == "aud_1"
    assert payload["reviews_tail"][0]["review_id"] == "rev_1"
    assert payload["run_summaries"][0]["run_id"] == "run_1"
