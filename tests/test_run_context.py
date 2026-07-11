from vibeos.models import CommandRequest
from vibeos.run_context import RunContext


def test_run_context_is_immutable_and_uses_request_transport_metadata() -> None:
    request = CommandRequest("search web for hello", dry_run=True, debug=True, review_id="rev_1", transport="http")

    context = RunContext.from_request(request, run_id="run_1", goal_id="goal_1")

    assert context.run_id == "run_1"
    assert context.goal_id == "goal_1"
    assert context.transport == "http"
    assert context.dry_run is True
    assert context.debug is True
    assert context.review_id == "rev_1"
