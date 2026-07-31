import subprocess

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


def test_clipboard_write_prefers_gnome_shell_bridge(monkeypatch) -> None:
    monkeypatch.setattr("vibeos.clipboard.os.name", "posix")
    monkeypatch.setattr(
        ClipboardAdapter,
        "_write_gnome_shell",
        lambda self, text: {"status": "written", "adapter": "org.vibeos.Shell.SetClipboard"},
    )
    monkeypatch.setattr("vibeos.clipboard.first_available", lambda commands: (_ for _ in ()).throw(AssertionError("fallback must not run")))

    result = ClipboardAdapter().write("hello")

    assert result == {"status": "written", "adapter": "org.vibeos.Shell.SetClipboard"}


def test_clipboard_observe_uses_gnome_shell_bridge(monkeypatch) -> None:
    monkeypatch.setattr("vibeos.clipboard.os.name", "posix")
    monkeypatch.setenv("DBUS_SESSION_BUS_ADDRESS", "unix:path=/run/user/1000/bus")
    monkeypatch.setattr(
        ClipboardAdapter,
        "_get_gnome_clipboard",
        lambda self: async_result(
            {
                "status": "observed",
                "adapter": "org.vibeos.Shell.GetClipboard",
                "text": "hello",
            }
        ),
    )

    assert ClipboardAdapter().observe() == {
        "status": "observed",
        "adapter": "org.vibeos.Shell.GetClipboard",
        "text": "hello",
    }


async def async_result(value):
    return value


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
