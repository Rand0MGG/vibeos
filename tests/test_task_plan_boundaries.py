from pathlib import Path
from uuid import uuid4

import pytest

from vibeos.audit import AuditLog
from vibeos.broker import CapabilityBroker
from vibeos.models import Intent
from vibeos.planner import normalize_intent_to_task_plan
from vibeos.reviews import ReviewStore


def test_execute_task_plan_rejects_raw_utterance_input() -> None:
    broker = CapabilityBroker()

    with pytest.raises(TypeError) as exc:
        broker.execute_task_plan("open browser")  # type: ignore[arg-type]

    assert "TaskPlan" in str(exc.value)


def test_review_task_plan_rejects_arbitrary_payload_input() -> None:
    broker = CapabilityBroker()

    with pytest.raises(TypeError) as exc:
        broker.review_task_plan({"utterance": "open browser"})  # type: ignore[arg-type]

    assert "TaskPlan" in str(exc.value)


def test_execute_task_plan_rejects_invalid_plan_before_execution() -> None:
    audit = AuditLog(make_audit_path("invalid-plan-execution"))
    broker = CapabilityBroker(
        audit=audit,
        reviews=ReviewStore(make_review_path("invalid-plan-execution")),
    )
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

    result = broker.execute_task_plan(invalid_plan)

    assert result.status == "rejected"
    assert result.step_results == ()
    assert audit.tail(5) == []


def test_review_task_plan_rejects_invalid_plan_before_creating_review() -> None:
    reviews = ReviewStore(make_review_path("invalid-plan-review"))
    broker = CapabilityBroker(
        audit=AuditLog(make_audit_path("invalid-plan-review")),
        reviews=reviews,
    )
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
    assert reviews.list_pending() == []


def make_review_path(name: str) -> Path:
    return Path(".vibeos") / f"test-{name}-{uuid4().hex}.jsonl"


def make_audit_path(name: str) -> Path:
    return Path(".vibeos") / f"audit-{name}-{uuid4().hex}.jsonl"
