from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy.engine import RowMapping

from ..domain.task import ActionProposal, ActionReceipt, EvidenceBundle, Step, TaskStatus


TERMINAL_TASK_STATUSES = frozenset({TaskStatus.DRY_RUN, TaskStatus.SUCCEEDED, TaskStatus.FAILED, TaskStatus.CANCELLED, TaskStatus.BLOCKED})


def step_from_row(row: RowMapping) -> Step:
    return Step(**{key: row[key] for key in Step.__dataclass_fields__})


def proposal_from_row(row: RowMapping) -> ActionProposal:
    return ActionProposal(**{key: row[key] for key in ActionProposal.__dataclass_fields__})


def receipt_from_row(row: RowMapping) -> ActionReceipt:
    return ActionReceipt(**{key: row[key] for key in ActionReceipt.__dataclass_fields__})


def evidence_from_row(row: RowMapping) -> EvidenceBundle:
    return EvidenceBundle(**{key: row[key] for key in EvidenceBundle.__dataclass_fields__})


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")
