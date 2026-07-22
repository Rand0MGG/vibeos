from __future__ import annotations

from hashlib import sha256

from .core.domain import TaskRun, TaskStatus
from .durable_task_engine import DurableTaskEngine
from .models import CommandRequest, CommandResult, utc_now_iso
from .result_projection import CommandResultProjector
from .run_context import RunContext


class TaskApplicationService:
    """Transport-neutral public use cases backed only by the durable task engine."""

    def __init__(
        self,
        *,
        engine: DurableTaskEngine,
        projector: CommandResultProjector,
    ) -> None:
        self.engine = engine
        self.projector = projector

    def make_run_id(self, seed: str) -> str:
        digest = sha256(f"run:{utc_now_iso()}:{seed}:{len(seed)}".encode("utf-8")).hexdigest()[:12]
        return f"run_{digest}"

    def start(self, request: CommandRequest, context: RunContext) -> CommandResult:
        return self.projector.project(self.engine.start(request, run_id=context.run_id), run_id=context.run_id)

    def approve(self, review_id: str, request: CommandRequest, context: RunContext) -> CommandResult:
        return self.projector.project(self.engine.approve(review_id, request, run_id=context.run_id), run_id=context.run_id)

    def provide_input(
        self,
        review_id: str,
        supplemental_input: str,
        request: CommandRequest,
        context: RunContext,
    ) -> CommandResult:
        result = self.engine.provide_input(review_id, supplemental_input, request, run_id=context.run_id)
        return self.projector.project(result, run_id=context.run_id)

    def reject(self, review_id: str, request: CommandRequest, context: RunContext) -> CommandResult:
        return self.projector.project(self.engine.reject(review_id, request), run_id=context.run_id)

    def list_tasks(self, *, statuses: tuple[TaskStatus, ...] = (), limit: int = 100) -> tuple[TaskRun, ...]:
        return self.engine.repository.list(statuses=statuses, limit=limit)

    def show_task(self, task_id: str) -> TaskRun | None:
        return self.engine.repository.get(task_id)

    def control_task(
        self,
        task_id: str,
        operation: str,
        *,
        expected_revision: int,
        owner: str | None = None,
        reason: str = "",
    ) -> TaskRun:
        return self.engine.control(
            task_id,
            operation,
            expected_revision=expected_revision,
            owner=owner,
            reason=reason,
        )

    def pending_interactions(self) -> tuple[TaskRun, ...]:
        return self.engine.pending_interactions()
