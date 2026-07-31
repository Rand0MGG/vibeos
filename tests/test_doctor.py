import subprocess

from vibeos.doctor import DoctorCheck, SessionDoctor, summarize
from vibeos.model_gateway.contracts import ProviderRoute, SecretRef
from vibeos.model_gateway.secrets import ProviderRouteRepository


class FakeApps:
    def list_apps(self):
        return []


class FakePortal:
    def status(self):
        return {"available": False, "reason": "portal unavailable"}


def fake_runner(command):
    if command[:2] == ["gnome-shell", "--version"]:
        return subprocess.CompletedProcess(command, 0, "GNOME Shell 48.0\n", "")
    if command[:3] == ["systemctl", "--user", "is-system-running"]:
        return subprocess.CompletedProcess(command, 0, "running\n", "")
    if command[:4] == ["systemctl", "--user", "is-active", "vibed.service"]:
        return subprocess.CompletedProcess(command, 3, "inactive\n", "")
    return subprocess.CompletedProcess(command, 1, "", "unknown command")


def test_summarize_warns_when_any_warning() -> None:
    summary = summarize(
        [
            DoctorCheck("a", "ok", "ok"),
            DoctorCheck("b", "warn", "warn"),
            DoctorCheck("c", "ok", "ok"),
        ]
    )
    assert summary["overall"] == "warn"
    assert summary["ok"] == 2
    assert summary["warn"] == 1


def test_doctor_report_shape(monkeypatch) -> None:
    monkeypatch.setenv("VIBEOS_MODEL_PROVIDER", "local")
    monkeypatch.setattr("vibeos.doctor.detect_runtime_entry", lambda: ("local", "warn", {"mode": "auto"}))
    doctor = SessionDoctor(runner=fake_runner, apps=FakeApps(), portal=FakePortal())
    report = doctor.run()

    assert "summary" in report
    assert "checks" in report
    assert any(check["name"] == "model_config" for check in report["checks"])
    assert any(check["name"] == "app_registry" for check in report["checks"])
    assert any(check["name"] == "action_helpers" for check in report["checks"])
    assert any(check["name"] == "runtime_entry" for check in report["checks"])


def test_missing_model_key_reports_explicit_offline_fallback(monkeypatch) -> None:
    monkeypatch.delenv("VIBEOS_MODEL_PROVIDER", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    doctor = SessionDoctor(runner=fake_runner, apps=FakeApps(), portal=FakePortal())

    check = doctor.check_model_config()

    assert check.status == "warn"
    assert "use --offline" in check.message


def test_model_config_confirms_plain_agent_gateway_route(tmp_path, monkeypatch) -> None:
    repository = ProviderRouteRepository(tmp_path / "routes.json")
    route = ProviderRoute(
        route_id="agent-primary",
        model="deepseek-v4-pro",
        base_url="https://api.deepseek.com",
        secret_ref=SecretRef(secret_id="agent-primary", provider="openai-compatible"),
    )
    repository.save(route)
    monkeypatch.setenv("VIBEOS_MODEL_PROVIDER", "deepseek")
    monkeypatch.setenv("DEEPSEEK_MODEL", route.model)
    monkeypatch.setattr("vibeos.doctor.os.path.exists", lambda _path: True)

    check = SessionDoctor(
        runner=fake_runner,
        apps=FakeApps(),
        portal=FakePortal(),
        route_repository=repository,
    ).check_model_config()

    assert check.status == "ok"
    assert check.detail is not None
    assert check.detail["selected_route"] == route.route_id
    assert check.detail["required_purpose"] == "goal_understanding"


def test_model_config_fails_when_multiple_routes_are_ambiguous(tmp_path, monkeypatch) -> None:
    repository = ProviderRouteRepository(tmp_path / "routes.json")
    for route_id, model in (("route-a", "model-a"), ("route-b", "model-b")):
        repository.save(
            ProviderRoute(
                route_id=route_id,
                model=model,
                base_url=f"https://{route_id}.invalid/v1",
                secret_ref=SecretRef(secret_id=route_id, provider="openai-compatible"),
            )
        )
    monkeypatch.setenv("VIBEOS_MODEL_PROVIDER", "openai-compatible")
    monkeypatch.delenv("VIBEOS_MODEL_ROUTE", raising=False)
    monkeypatch.setattr("vibeos.doctor.os.path.exists", lambda _path: True)

    check = SessionDoctor(
        runner=fake_runner,
        apps=FakeApps(),
        portal=FakePortal(),
        route_repository=repository,
    ).check_model_config()

    assert check.status == "fail"
    assert "deterministic route" in check.message


def test_missing_gdbus_is_warning_off_linux(monkeypatch) -> None:
    monkeypatch.setattr("platform.system", lambda: "Windows")
    doctor = SessionDoctor(runner=fake_runner, apps=FakeApps(), portal=FakePortal())
    check = doctor.check_dbus_tools()
    assert check.status == "warn"


def test_action_helpers_report_missing_tools(monkeypatch) -> None:
    monkeypatch.setattr("shutil.which", lambda _command: None)
    doctor = SessionDoctor(runner=fake_runner, apps=FakeApps(), portal=FakePortal())
    check = doctor.check_action_helpers()
    assert check.status == "warn"
    assert "notify-send" in check.message
    assert "wl-copy/xclip/xsel" in check.message


def test_runtime_entry_reports_dbus_path(monkeypatch) -> None:
    monkeypatch.setattr("vibeos.doctor.detect_runtime_entry", lambda: ("dbus", "ok", {"mode": "auto"}))
    doctor = SessionDoctor(runner=fake_runner, apps=FakeApps(), portal=FakePortal())

    check = doctor.check_runtime_entry()

    assert check.status == "ok"
    assert "D-Bus daemon" in check.message


def test_runtime_entry_reports_fail_when_daemon_required(monkeypatch) -> None:
    monkeypatch.setattr(
        "vibeos.doctor.detect_runtime_entry",
        lambda: ("local", "fail", {"mode": "auto", "require_daemon": True}),
    )
    doctor = SessionDoctor(runner=fake_runner, apps=FakeApps(), portal=FakePortal())

    check = doctor.check_runtime_entry()

    assert check.status == "fail"
    assert "requires daemon transport" in check.message
