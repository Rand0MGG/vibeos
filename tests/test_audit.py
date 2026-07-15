import json
from pathlib import Path
from uuid import uuid4

from vibeos.audit import AuditLog
from vibeos.models import CommandRequest, Intent


def test_audit_log_records_execution_status_fields() -> None:
    audit_path = make_audit_path("status-fields")
    audit = AuditLog(audit_path)
    request = CommandRequest("search web for hello", transport="local")
    intent = Intent(action="browser.search_web", target={"query": "hello"}, reason="test")

    audit_id = audit.record(
        request=request,
        intent=intent,
        status="executed",
        result={"status": "opened"},
        selected_target="https://example.com",
        execution_status="succeeded",
        acceptance_status="passed",
        overall_status="completed",
        trace_run_id="run_1",
        plan_id="plan_1",
        step_id="step_1",
        layer="adapter_execute",
        understanding_id="und_1",
        candidate_set_id="cset_1",
        selected_route_decision_id="rdec_1",
        selected_strategy_decision_id="sdec_1",
        semantic_acceptance_decision_id="adec_1",
        loop_snapshot_id="lsnap_1",
    )

    payload = json.loads(audit_path.read_text(encoding="utf-8").splitlines()[0])

    assert payload["audit_id"] == audit_id
    assert payload["plan_id"] == "plan_1"
    assert payload["step_id"] == "step_1"
    assert payload["execution_status"] == "succeeded"
    assert payload["acceptance_status"] == "passed"
    assert payload["overall_status"] == "completed"
    assert payload["trace_run_id"] == "run_1"
    assert payload["understanding_id"] == "und_1"
    assert payload["candidate_set_id"] == "cset_1"
    assert payload["selected_route_decision_id"] == "rdec_1"
    assert payload["loop_snapshot_id"] == "lsnap_1"


def test_content_bearing_actions_are_redacted_from_audit(tmp_path: Path) -> None:
    canary = "sk-canary person@example.com"
    audit_path = tmp_path / "audit.jsonl"
    AuditLog(audit_path).record(
        request=CommandRequest(f"notify {canary}"),
        intent=Intent(action="notification.send", target={"title": "VibeOS", "body": canary}),
        status="executed",
        result={"tool_invocations": [{"input_payload": {"body": canary}, "message": canary}]},
    )

    serialized = audit_path.read_text(encoding="utf-8")
    assert canary not in serialized
    assert serialized.count("[REDACTED]") >= 3


def make_audit_path(name: str) -> Path:
    return Path(".vibeos") / f"audit-{name}-{uuid4().hex}.jsonl"
