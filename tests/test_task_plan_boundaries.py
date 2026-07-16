from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest

from vibeos.audit import AuditLog
from vibeos.broker import CapabilityBroker
from vibeos.models import Intent
from vibeos.planner import normalize_intent_to_task_plan


def test_direct_execution_entry_points_are_not_public() -> None:
    broker = CapabilityBroker()
    assert not hasattr(broker, "execute_task_plan")
    assert not hasattr(broker, "execute_task_step")
    assert not hasattr(broker.task_handler, "execute_task_plan")
    assert not hasattr(broker.task_handler, "execute_task_step")


def test_review_task_plan_rejects_arbitrary_payload_input() -> None:
    broker = CapabilityBroker()

    with pytest.raises(TypeError) as exc:
        broker.review_task_plan({"utterance": "open browser"})  # type: ignore[arg-type]

    assert "TaskPlan" in str(exc.value)


def test_review_task_plan_rejects_invalid_plan_before_creating_review() -> None:
    broker = CapabilityBroker(audit=AuditLog(make_audit_path("invalid-plan-review")))
    plan = normalize_intent_to_task_plan(
        Intent(action="app.open", target={"name": "browser"}, reason="user asked to open browser"),
        "open browser",
    )
    invalid_plan = type(plan)(
        schema_version=plan.schema_version,
        plan_id=plan.plan_id,
        utterance=plan.utterance,
        display=plan.display,
        status=plan.status,
        source_span_id=plan.source_span_id,
        selected_route_id="missing_route",
        routes=plan.routes,
        steps=plan.steps,
        provenance=plan.provenance,
        needs_user_input=plan.needs_user_input,
    )

    review = broker.review_task_plan(invalid_plan)

    assert review.status == "rejected"
    assert review.review_id is None
    assert broker.pending_reviews() == []


def make_audit_path(name: str) -> Path:
    return Path(".vibeos") / f"audit-{name}-{uuid4().hex}.jsonl"
