import json

from vibeos.dbus_service import safe_command_result
from vibeos.models import CommandRequest


def test_safe_command_result_returns_structured_failure_and_logs_traceback(capsys) -> None:
    request = CommandRequest("open browser", dry_run=True, transport="dbus")

    def boom():
        raise ValueError("broken planner")

    payload = json.loads(safe_command_result(boom, request=request))
    captured = capsys.readouterr()

    assert "ValueError: broken planner" in captured.err
    assert payload["status"] == "failed"
    assert payload["intent"]["action"] == "unknown"
    assert payload["result"]["error"] == "daemon_internal_error"
    assert payload["result"]["transport"] == "dbus"
    assert payload["result"]["utterance"] == "open browser"
    assert payload["message"] == "daemon command failed: broken planner"
