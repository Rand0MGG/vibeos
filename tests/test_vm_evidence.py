from scripts.collect_vm_evidence import doctor_ok


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
