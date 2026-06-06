import subprocess
from types import SimpleNamespace

from vibeos.clipboard import ClipboardAdapter


def test_clipboard_write_reports_written(monkeypatch) -> None:
    monkeypatch.setattr("vibeos.clipboard.os.name", "posix")
    monkeypatch.setattr("vibeos.clipboard.first_available", lambda commands: "/usr/bin/wl-copy")

    class FakeProcess:
        returncode = 0

        def communicate(self, _input, timeout):
            assert timeout == 1
            return ("", "")

    monkeypatch.setattr("vibeos.clipboard.subprocess.Popen", lambda *args, **kwargs: FakeProcess())

    result = ClipboardAdapter().write("hello")

    assert result["status"] == "written"
    assert result["adapter"] == "/usr/bin/wl-copy"


def test_clipboard_write_reports_written_when_wl_copy_stays_running(monkeypatch) -> None:
    monkeypatch.setattr("vibeos.clipboard.os.name", "posix")
    monkeypatch.setattr("vibeos.clipboard.first_available", lambda commands: "/usr/bin/wl-copy")

    class FakeProcess:
        returncode = None

        def communicate(self, _input, timeout):
            raise subprocess.TimeoutExpired(cmd=["/usr/bin/wl-copy"], timeout=timeout)

    monkeypatch.setattr("vibeos.clipboard.subprocess.Popen", lambda *args, **kwargs: FakeProcess())

    result = ClipboardAdapter().write("hello")

    assert result["status"] == "written"
    assert result["adapter"] == "/usr/bin/wl-copy"


def test_clipboard_write_reports_adapter_timeout_for_non_wl_copy_helper(monkeypatch) -> None:
    monkeypatch.setattr("vibeos.clipboard.os.name", "posix")
    monkeypatch.setattr("vibeos.clipboard.first_available", lambda commands: "/usr/bin/xclip")

    def raise_timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd=["/usr/bin/xclip"], timeout=10)

    monkeypatch.setattr("vibeos.clipboard.subprocess.run", raise_timeout)

    result = ClipboardAdapter().write("hello")

    assert result["status"] == "timeout"
    assert result["adapter"] == "/usr/bin/xclip"
    assert "timed out" in result["error"]


def test_clipboard_write_reports_unavailable_when_no_helper_exists(monkeypatch) -> None:
    monkeypatch.setattr("vibeos.clipboard.os.name", "posix")
    monkeypatch.setattr("vibeos.clipboard.first_available", lambda commands: None)

    result = ClipboardAdapter().write("hello")

    assert result["status"] == "unavailable"
