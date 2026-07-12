from vibeos.audit import AuditLog
from vibeos.broker import CapabilityBroker
from vibeos.loop_models import GoalLoopResult, LoopState
from vibeos.models import CommandRequest
from vibeos.reviews import ReviewStore
from tests.support_intent_broker import FixtureIntentBroker


def test_goal_loop_is_the_default_task_path(monkeypatch) -> None:
    broker = CapabilityBroker(
        intent_broker=FixtureIntentBroker(),
        audit=AuditLog(),
        reviews=ReviewStore(),
    )
    called = {"value": False}

    class FakeGoalLoop:
        def run(self, *, request, planning, run_id, goal_id):
            called["value"] = True
            return GoalLoopResult(
                decision="complete",
                state=LoopState(
                    loop_snapshot_id="snapshot_goal_loop",
                    trace_run_id=run_id,
                    goal_id=goal_id,
                    primary_understanding_id=None,
                    candidate_set_id=None,
                    selected_route_decision_id=None,
                    current_step_id=None,
                    selected_plan_id=planning.plan.plan_id if planning.plan is not None else None,
                ),
                execution_status="succeeded",
                acceptance_status="passed",
                overall_status="completed",
                payload={"path": "goal_loop"},
            )

    monkeypatch.setattr(broker.task_handler, "make_goal_loop", FakeGoalLoop)

    result = broker.handle(CommandRequest("search web for hello"))

    assert called["value"] is True
    assert result.result is not None
    assert result.result["path"] == "goal_loop"
