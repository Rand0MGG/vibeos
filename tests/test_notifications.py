import subprocess
from types import SimpleNamespace

from vibeos.notifications import NotificationAdapter


def test_notification_send_reports_sent(monkeypatch) -> None:
    monkeypatch.setattr("vibeos.notifications.os.name", "posix")
    monkeypatch.setattr("vibeos.notifications.shutil.which", lambda name: "/usr/bin/notify-send")
    monkeypatch.setattr(
        "vibeos.notifications.subprocess.run",
        lambda *args, **kwargs: SimpleNamespace(returncode=0, stderr="", stdout=""),
    )

    result = NotificationAdapter().send("VibeOS", "hello")

    assert result["status"] == "sent"
    assert result["adapter"] == "/usr/bin/notify-send"


def test_notification_send_reports_timeout(monkeypatch) -> None:
    monkeypatch.setattr("vibeos.notifications.os.name", "posix")
    monkeypatch.setattr("vibeos.notifications.shutil.which", lambda name: "/usr/bin/notify-send")

    def raise_timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd=["/usr/bin/notify-send"], timeout=10)

    monkeypatch.setattr("vibeos.notifications.subprocess.run", raise_timeout)

    result = NotificationAdapter().send("VibeOS", "hello")

    assert result["status"] == "timeout"
    assert result["adapter"] == "/usr/bin/notify-send"


def test_notification_send_reports_unavailable(monkeypatch) -> None:
    monkeypatch.setattr("vibeos.notifications.os.name", "posix")
    monkeypatch.setattr("vibeos.notifications.shutil.which", lambda name: None)

    result = NotificationAdapter().send("VibeOS", "hello")

    assert result["status"] == "unavailable"
