#!/usr/bin/env python3
"""Measure bounded SQLite contention for the Goal 02 target concurrency."""

from __future__ import annotations

import argparse
import json
import statistics
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from sqlalchemy import func, select

from vibeos.core.adapters.database import CoreDatabase
from vibeos.core.adapters.metadata import domain_events, outbox, task_runs
from vibeos.core.adapters.task_repository import SqliteTaskRepository
from vibeos.core.domain.task import GoalContract, TaskEvent, TaskEventType, TaskRun, TaskStatus
from vibeos.core.domain.task_transitions import transition

TARGET_TASKS = 64
TARGET_WORKERS = 8
MAX_P95_MS = 2_500.0
MAX_WALL_SECONDS = 20.0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tasks", type=int, default=TARGET_TASKS)
    parser.add_argument("--workers", type=int, default=TARGET_WORKERS)
    args = parser.parse_args(argv)
    with tempfile.TemporaryDirectory(prefix="vibeos-task-benchmark-") as directory:
        result = benchmark(
            Path(directory) / "tasks.sqlite3",
            tasks=args.tasks,
            workers=args.workers,
        )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["thresholds_met"] else 1


def benchmark(path: Path, *, tasks: int, workers: int) -> dict[str, object]:
    database = CoreDatabase(path, busy_timeout_ms=5_000)
    database.upgrade()
    durations: list[float] = []
    errors: list[str] = []

    def create_and_plan(index: int) -> None:
        started = time.perf_counter()
        try:
            repository = SqliteTaskRepository(database)
            timestamp = "2099-01-01T00:00:00.000Z"
            task_id = f"benchmark-task-{index:04d}"
            contract = GoalContract(
                f"benchmark-contract-{index:04d}",
                task_id,
                "benchmark",
                (),
                (),
                (),
                (),
                1,
                timestamp,
            )
            state = TaskRun(
                task_id,
                contract.contract_id,
                TaskStatus.CREATED,
                0,
                timestamp,
                timestamp,
            )
            repository.create(contract, state)
            task_event = TaskEvent(
                event_id=f"benchmark-event-{index:04d}",
                task_id=task_id,
                event_type=TaskEventType.PLAN_REQUESTED,
                occurred_at="2099-01-01T00:00:01.000Z",
            )
            repository.commit(transition(state, task_event))
        except Exception as exc:  # benchmark reports every operational failure
            errors.append(f"{type(exc).__name__}: {exc}")
        finally:
            durations.append((time.perf_counter() - started) * 1_000)

    wall_started = time.perf_counter()
    with ThreadPoolExecutor(max_workers=workers) as executor:
        tuple(executor.map(create_and_plan, range(tasks)))
    wall_seconds = time.perf_counter() - wall_started
    ordered = sorted(durations)
    p95_index = max(0, min(len(ordered) - 1, int(len(ordered) * 0.95) - 1))
    p95_ms = ordered[p95_index] if ordered else 0.0
    with database.engine.connect() as connection:
        counts = {
            "task_runs": int(connection.execute(select(func.count()).select_from(task_runs)).scalar_one()),
            "domain_events": int(connection.execute(select(func.count()).select_from(domain_events)).scalar_one()),
            "outbox": int(connection.execute(select(func.count()).select_from(outbox)).scalar_one()),
        }
    thresholds_met = (
        not errors and counts == {"task_runs": tasks, "domain_events": tasks, "outbox": tasks} and p95_ms <= MAX_P95_MS and wall_seconds <= MAX_WALL_SECONDS
    )
    return {
        "schema_version": 1,
        "target": {"tasks": tasks, "workers": workers},
        "metrics": {
            "wall_seconds": round(wall_seconds, 3),
            "throughput_tasks_per_second": round(tasks / wall_seconds, 2) if wall_seconds else 0.0,
            "mean_task_commit_ms": round(statistics.fmean(durations), 2) if durations else 0.0,
            "p95_task_commit_ms": round(p95_ms, 2),
            "max_task_commit_ms": round(max(durations), 2) if durations else 0.0,
            "lock_or_commit_errors": len(errors),
        },
        "thresholds": {
            "max_p95_task_commit_ms": MAX_P95_MS,
            "max_wall_seconds": MAX_WALL_SECONDS,
            "max_errors": 0,
        },
        "row_counts": counts,
        "errors": errors,
        "thresholds_met": thresholds_met,
    }


if __name__ == "__main__":
    raise SystemExit(main())
