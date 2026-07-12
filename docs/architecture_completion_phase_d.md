# Architecture Completion - Phase D Planning and Resume

Captured: 2026-07-12

This checkpoint records the Phase D work required by
[`architecture_completion_master_goal.md`](architecture_completion_master_goal.md).
The master contract itself remains unchanged.

## Extracted ownership

`PlanningService` is now the active owner of fresh planning, constrained
replanning, understanding transitions, planning serialization, and planning
snapshot reconstruction. `PlanningServicePort` implements the GoalLoop planning
port directly and does not retain a `CapabilityBroker`.

`ReviewResumeService` owns stored-review decoding and invokes `GoalLoop` for
both execution-review and user-input-review resumes. Broker only converts the
result into the existing public command projection during this intermediate
phase; result projection moves in Phase F.

## Versioned snapshots and safe decoding

New planning snapshots carry `snapshot_version: 1`; new loop snapshots are
written through `encode_loop_snapshot()` with the same version field. Explicit
version-0 decoding exists solely for pre-existing current-format review rows.
Unknown versions, missing loop identity fields, invalid plan/candidate shapes,
and malformed task payloads raise typed snapshot errors. The resume command
returns:

```text
status: failed
result.error_code: review_snapshot_invalid
execution_status: not_started
```

No GoalLoop or adapter dispatch occurs for that result. The regression test
uses an unsupported planning snapshot version and verifies that the browser
portal remains untouched.

## Verification

```text
python -m pytest tests/test_goal_loop.py -q   -> 31 passed
python -m pytest tests/test_broker.py -q      -> 27 passed
python -m pytest tests/test_architecture.py -q -> 7 passed, 3 xfailed
```

The remaining xfails are only the later Phase E AgentRuntime removal and Phase
F CommandService boundary replacement gates.
