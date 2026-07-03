from vibeos.audit import AuditLog
from vibeos.broker import CapabilityBroker
from vibeos.models import CommandRequest, CommandResult, Intent
from vibeos.reviews import ReviewStore
from tests.support_intent_broker import FixtureIntentBroker


def test_feature_flag_switches_to_goal_loop_path(monkeypatch) -> None:
    broker = CapabilityBroker(
        intent_broker=FixtureIntentBroker(),
        audit=AuditLog(),
        reviews=ReviewStore(),
    )
    called = {"value": False}

    def fake_goal_loop(request, planning):
        called["value"] = True
        return CommandResult(
            status="executed",
            intent=Intent(action="browser.search_web", target={"query": "hello"}),
            result={"path": "goal_loop"},
            execution_status="succeeded",
            acceptance_status="passed",
            overall_status="completed",
        )

    monkeypatch.setenv("VIBEOS_ENABLE_GOAL_LOOP", "1")
    monkeypatch.setattr(broker, "_run_task_plan_goal_loop", fake_goal_loop)

    result = broker.handle(CommandRequest("search web for hello"))

    assert called["value"] is True
    assert result.result == {"path": "goal_loop"}


def test_legacy_task_plan_loop_remains_default_when_goal_loop_disabled(monkeypatch) -> None:
    broker = CapabilityBroker(
        intent_broker=FixtureIntentBroker(),
        audit=AuditLog(),
        reviews=ReviewStore(),
    )
    called = {"value": False}

    def fake_goal_loop(request, planning):
        called["value"] = True
        raise AssertionError("goal loop path should stay disabled by default")

    monkeypatch.delenv("VIBEOS_ENABLE_GOAL_LOOP", raising=False)
    monkeypatch.setattr(broker, "_run_task_plan_goal_loop", fake_goal_loop)

    result = broker.handle(CommandRequest("search web for hello", dry_run=True))

    assert called["value"] is False
    assert result.status == "dry_run"
