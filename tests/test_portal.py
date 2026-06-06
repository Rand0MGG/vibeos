import subprocess
from types import SimpleNamespace

from vibeos.portal import PortalAdapter


def test_open_uri_reports_opened_via_portal(monkeypatch) -> None:
    monkeypatch.setattr("vibeos.portal.os.name", "posix")
    monkeypatch.setattr("vibeos.portal.shutil.which", lambda name: "/usr/bin/gdbus" if name == "gdbus" else None)
    monkeypatch.setattr(
        "vibeos.portal.subprocess.run",
        lambda *args, **kwargs: SimpleNamespace(returncode=0, stderr="", stdout=""),
    )

    result = PortalAdapter().open_uri("https://example.com")

    assert result["status"] == "opened"
    assert result["adapter"] == "xdg-desktop-portal"


def test_open_uri_reports_timeout(monkeypatch) -> None:
    monkeypatch.setattr("vibeos.portal.os.name", "posix")
    monkeypatch.setattr("vibeos.portal.shutil.which", lambda name: "/usr/bin/gdbus" if name == "gdbus" else None)

    def raise_timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd=["/usr/bin/gdbus"], timeout=10)

    monkeypatch.setattr("vibeos.portal.subprocess.run", raise_timeout)

    result = PortalAdapter().open_uri("https://example.com")

    assert result["status"] == "timeout"
    assert result["adapter"] == "xdg-desktop-portal"


def test_open_uri_reports_unavailable_when_no_opener_exists(monkeypatch) -> None:
    monkeypatch.setattr("vibeos.portal.os.name", "posix")
    monkeypatch.setattr("vibeos.portal.shutil.which", lambda name: None)

    result = PortalAdapter().open_uri("https://example.com")

    assert result["status"] == "unavailable"


def test_open_uri_reports_unsupported_for_non_http_scheme() -> None:
    result = PortalAdapter().open_uri("file:///etc/passwd")

    assert result["status"] == "unsupported"
