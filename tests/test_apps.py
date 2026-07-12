import subprocess

from vibeos.apps import AppRegistry
from vibeos.models import AppEntry


def test_open_app_launches_without_waiting(monkeypatch) -> None:
    calls = []

    def fake_popen(command, **kwargs):
        calls.append((command, kwargs))
        return object()

    monkeypatch.setattr("shutil.which", lambda command: "/usr/bin/gtk-launch" if command == "gtk-launch" else None)
    monkeypatch.setattr("vibeos.apps.subprocess.Popen", fake_popen)

    result = AppRegistry().open_app(AppEntry(desktop_id="org.mozilla.firefox.desktop", name="Firefox"))

    assert result == {"status": "opened", "desktop_id": "org.mozilla.firefox.desktop"}
    assert calls == [
        (
            ["/usr/bin/gtk-launch", "org.mozilla.firefox.desktop"],
            {
                "stdout": subprocess.DEVNULL,
                "stderr": subprocess.DEVNULL,
                "start_new_session": True,
            },
        )
    ]


def test_open_app_reports_spawn_error(monkeypatch) -> None:
    def fake_popen(_command, **_kwargs):
        raise OSError("spawn failed")

    monkeypatch.setattr("shutil.which", lambda command: "/usr/bin/gtk-launch" if command == "gtk-launch" else None)
    monkeypatch.setattr("vibeos.apps.subprocess.Popen", fake_popen)

    result = AppRegistry().open_app(AppEntry(desktop_id="org.mozilla.firefox.desktop", name="Firefox"))

    assert result == {"status": "failed", "error": "spawn failed"}
