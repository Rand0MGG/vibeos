from __future__ import annotations

from dataclasses import dataclass

from .models import CommandRequest


@dataclass(frozen=True)
class RunContext:
    """Immutable identity and transport data scoped to one command run."""

    run_id: str
    goal_id: str
    transport: str | None
    dry_run: bool
    debug: bool
    review_id: str | None = None

    @classmethod
    def from_request(cls, request: CommandRequest, *, run_id: str, goal_id: str) -> "RunContext":
        return cls(
            run_id=run_id,
            goal_id=goal_id,
            transport=request.transport,
            dry_run=request.dry_run,
            debug=request.debug,
            review_id=request.review_id,
        )
