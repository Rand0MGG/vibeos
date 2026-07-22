from __future__ import annotations

from .task_codec import decode_contract, decode_task
from ..domain.task import GoalContract, TaskRun, TaskStatus

_TERMINAL = {
    TaskStatus.DRY_RUN,
    TaskStatus.SUCCEEDED,
    TaskStatus.FAILED,
    TaskStatus.CANCELLED,
    TaskStatus.BLOCKED,
}


def decode_task_record(schema_version: str, status: str, raw: str) -> TaskRun:
    resolved_status = TaskStatus(status)
    if schema_version == "v2":
        return decode_task(raw)
    if schema_version == "v1" and resolved_status in _TERMINAL:
        from .task_history_v1 import decode_terminal_task_v1

        return decode_terminal_task_v1(raw)
    raise ValueError("nonterminal v1 task must be dispositioned by migration 0006")


def decode_contract_record(schema_version: str, task_status: str, raw: str) -> GoalContract:
    if schema_version == "v2":
        return decode_contract(raw)
    if schema_version == "v1" and TaskStatus(task_status) in _TERMINAL:
        from .task_history_v1 import decode_terminal_contract_v1

        return decode_terminal_contract_v1(raw)
    raise ValueError("nonterminal v1 contract must be dispositioned by migration 0006")
