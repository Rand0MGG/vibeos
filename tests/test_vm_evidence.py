from scripts.collect_vm_evidence import (
    audit_tail_ok,
    blocked_step,
    build_summary,
    command_transport_ok,
    daemon_status_ok,
    dbus_introspect_ok,
    doctor_ok,
    infer_hint,
    parse_gdbus_json,
    systemd_active_ok,
)


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
