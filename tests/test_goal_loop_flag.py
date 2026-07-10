from vibeos.audit import AuditLog
from vibeos.broker import CapabilityBroker
from vibeos.models import CommandRequest, CommandResult, Intent
from vibeos.reviews import ReviewStore
from tests.support_intent_broker import FixtureIntentBroker


def test_goal_loop_is_the_default_task_path(monkeypatch) -> None:
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

    monkeypatch.setattr(broker, "_run_task_plan_goal_loop", fake_goal_loop)

    result = broker.handle(CommandRequest("search web for hello"))

    assert called["value"] is True
    assert result.result == {"path": "goal_loop"}
