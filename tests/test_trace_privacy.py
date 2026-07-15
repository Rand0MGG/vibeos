import json

from vibeos.task_trace import TaskTraceStore


def test_normal_trace_omits_raw_user_and_provider_content(tmp_path) -> None:
    session = TaskTraceStore(tmp_path).start_run(
        run_id="run_normal_trace",
        command_name="ask",
        utterance="private user request",
        mode="auto_low_risk",
        transport="local",
        dry_run=False,
        debug=False,
    )
    session.append_event(
        phase="ingress",
        event_type="request_received",
        actor="test",
        data={"utterance": "private user request", "safe": "metadata"},
    )
    session.append_event(
        phase="execution",
        event_type="notification",
        actor="test",
        data={"body": "person@example.com", "text": "private clipboard text"},
    )
    session.append_model_io(
        phase="analysis",
        provider="test",
        model="test-model",
        request_payload={"content": "private provider request", "api_key": "secret-key"},
        response_payload={"content": "private provider response", "token": "secret-token"},
        normalized_output={"content": "private normalized output", "status": "ok"},
    )

    manifest = json.loads((session.run_dir / "manifest.json").read_text(encoding="utf-8"))
    events = (session.run_dir / "events.jsonl").read_text(encoding="utf-8")
    model_io = (session.run_dir / "model_io.jsonl").read_text(encoding="utf-8")

    assert manifest["utterance"] is None
    assert "private user request" not in events
    assert "person@example.com" not in events
    assert "private clipboard text" not in events
    assert "private provider request" not in model_io
    assert "private provider response" not in model_io
    assert "private normalized output" not in model_io
    assert not list(session.artifacts_dir.iterdir())


def test_debug_trace_redacts_credentials_and_bounds_raw_artifacts(tmp_path) -> None:
    session = TaskTraceStore(tmp_path).start_run(
        run_id="run_debug_trace",
        command_name="ask",
        utterance="debug request",
        mode="auto_low_risk",
        transport="local",
        dry_run=False,
        debug=True,
    )
    session.append_model_io(
        phase="analysis",
        provider="test",
        model="test-model",
        request_payload={"content": "x" * 3_000, "api_key": "secret-key"},
        response_payload={"authorization": "Bearer secret-token", "content": "visible response"},
    )

    artifacts = "\n".join(path.read_text(encoding="utf-8") for path in session.artifacts_dir.iterdir())

    assert "secret-key" not in artifacts
    assert "secret-token" not in artifacts
    assert "[REDACTED]" in artifacts
    assert "[TRUNCATED]" in artifacts
