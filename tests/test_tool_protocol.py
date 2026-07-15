from types import SimpleNamespace

from vibeos.tool_protocol import ToolExecutionContext, ToolRegistry, ToolResult, ToolSpec


def test_tool_registry_reports_unregistered_tools_with_diagnostics() -> None:
    registry = ToolRegistry(())

    envelope, result = registry.invoke("missing.tool", {"x": 1}, make_context())

    assert result.status == "unavailable"
    assert result.failure_class == "unsupported_request"
    assert envelope.tool_id == "missing.tool"
    assert envelope.capability_surface == "unknown"


def test_tool_registry_reports_environment_unavailable_before_runner() -> None:
    registry = ToolRegistry(
        (
            ToolSpec(
                "browser.search_web",
                "action",
                "browser",
                lambda payload, context: ToolResult(status="succeeded"),
                availability=lambda env: False,
            ),
        )
    )

    envelope, result = registry.invoke("browser.search_web", {"query": "hello"}, make_context())

    assert result.status == "unavailable"
    assert result.failure_class == "environment_unreachable"
    assert envelope.family == "action"


def test_tool_registry_returns_envelope_and_runner_output() -> None:
    registry = ToolRegistry(
        (
            ToolSpec(
                "browser.search_web",
                "action",
                "browser",
                lambda payload, context: ToolResult(
                    status="succeeded",
                    output={"uri": "https://example.com"},
                    evidence={"query": payload["query"]},
                ),
            ),
        )
    )

    envelope, result = registry.invoke("browser.search_web", {"query": "hello"}, make_context())

    assert result.status == "succeeded"
    assert envelope.output_payload["uri"] == "https://example.com"
    assert envelope.evidence["query"] == "hello"


def test_tool_registry_redacts_user_content_and_secrets_from_recorded_input() -> None:
    registry = ToolRegistry((ToolSpec("notification.send", "action", "desktop-linux", lambda payload, context: ToolResult(status="succeeded")),))

    envelope, _result = registry.invoke(
        "notification.send",
        {"task_step_id": "notify", "body": "private@example.com", "api_token": "secret-token"},
        make_context(),
    )

    assert envelope.input_payload == {
        "task_step_id": "notify",
        "body": "[REDACTED]",
        "api_token": "[REDACTED]",
    }


def make_context() -> ToolExecutionContext:
    return ToolExecutionContext(
        session_id="session_1",
        goal_id="goal_1",
        turn_id="turn_1",
        attempt_id="attempt_1",
        strategy_id="strategy_1",
        environment=SimpleNamespace(),
    )
