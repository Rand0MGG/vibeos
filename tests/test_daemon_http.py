from vibeos.daemon import safe_http_command_result
from vibeos.models import CommandRequest


def test_safe_http_command_result_returns_structured_failure_and_logs_traceback(capsys) -> None:
    request = CommandRequest("open browser", dry_run=True, transport="http")

    def boom():
        raise ValueError("broken planner")

    result = safe_http_command_result(boom, request=request)
    captured = capsys.readouterr()

    assert "ValueError: broken planner" in captured.err
    assert result.status == "failed"
    assert result.intent.action == "unknown"
    assert result.result["error"] == "daemon_internal_error"
    assert result.result["transport"] == "http"
    assert result.result["utterance"] == "open browser"
    assert result.message == "daemon command failed: broken planner"
