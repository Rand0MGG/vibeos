# Maintainability Refactor Baseline

Captured: 2026-07-11

This document is the Gate 0 evidence record for
`goal_maintainability_refactor_for_codex.md`. It is not the task contract and
may be updated as later gates replace the paths listed below.

## Baseline verification

```text
wsl -d FedoraLinux-44 bash -lc
  "cd /mnt/e/codex_project/vibeos &&
   source /home/rand0mg/.venvs/vibeos/bin/activate &&
   python -m pytest -q"

229 passed in 8.45s
```

The public command-result compatibility fields are `status`,
`execution_status`, `acceptance_status`, `overall_status`, `review_id`,
`trace_run_id`, `audit_id`, and the task-plan result payload. Existing broker,
runtime, daemon, CLI, review, GoalLoop, and trace-privacy tests characterize
their current behavior. A dedicated contract fixture is added with Gate 0.

## Caller inventory

| Path | Current classification | Evidence | Required disposition |
| --- | --- | --- | --- |
| `_run_task_plan_goal_loop` | live default task loop | `CapabilityBroker._handle_task_plan_request()` | retain as the behavior source while moving it behind `CommandService` |
| `_run_v06_runtime_bridge` | static dead candidate | only its definition is found by repository search | delete after a regression test proves no behavior depends on it |
| `_compatibility_runtime_result` | live default-path compatibility projection | called by GoalLoop result finalization | replace with a pure projection that does not mutate `AgentRuntime` |
| `_build_v06_strategy_candidates` | reachable only from the static-dead bridge | repository search shows its bridge caller | delete with the bridge unless a historical payload needs it |
| `_task_plan_to_v06_strategy` | live historical-review path | used by `_approve_plan_review_v06` | replace historical-review execution with GoalLoop or isolate a tested migration reader |
| `_approve_plan_review_v06` | live review-resume path | selected from review payload compatibility branch | route through stored GoalLoop state without reparsing the utterance |
| `_approve_plan_review_legacy` | live legacy-review fallback | called when a stored v0.6 strategy cannot be reconstructed | preserve only until historical review migration is tested, then remove or isolate |
| `_record_legacy_execution_trace` | live only through legacy review fallback | called by `_approve_plan_review_legacy` | replace with the normal trace path before deleting |
| `_execute` | live direct execution helper | used by `execute_task_step` and old review/direct branches | move its domain dispatch into domain tool modules; do not delete until all callers use `ExecutionPort` |
| `AgentRuntime.continue_goal` | live only in legacy bridge and v0.6 review resume | broker references at the bridge and `_approve_plan_review_v06` | remove all default-path callers and isolate/delete legacy resume usage after migration |
| `self.agent_session` | live default-path mutation | `_compatibility_runtime_result` calls `record_external_turn` on it | replace with immutable, pure legacy payload projection |

## Known structural facts

- `src/vibeos/broker.py` is approximately 4,049 lines at this baseline.
- `GoalLoop` receives a large callback set with several `Any` types.
- `ReviewStore` stores event payloads in SQLite and reconstructs state in
  Python; it has no authoritative current-state table yet.
- `.github/workflows/test.yml` does not exist at this baseline.

## Gate 0 exit criteria

Gate 0 is complete when the baseline remains green and automated tests cover
the public result field contract in addition to the existing behavioral tests.

## Gate 1 evidence: domain tool extraction

Completed on 2026-07-11.

- Registered apps, windows, browser, clipboard, notifications, system, and
  fixture tools now live under `src/vibeos/tools/`.
- `CapabilityBroker._build_v06_tool_registry()` only composes those domain
  `ToolSpec` sets; it no longer defines a domain handler.
- Static search found no former nested handler or `_browser_runtime_target`
  definition in `broker.py`.
- The full WSL suite passed: `231 passed in 8.31s`.
- `broker.py` reduced from about 4,049 to 3,617 lines without changing tool
  IDs, adapters, capability boundaries, or dry-run semantics.

## Gate 2 evidence: command ingress and run-scoped projection

Completed on 2026-07-11.

- `CommandService` owns command dispatch, trace lifecycle, and final result
  recording. `CapabilityBroker.handle()` is now a compatibility facade that
  delegates to it.
- Fresh tasks have no direct intent fallback: `CommandService` sends them to
  the planning and GoalLoop path.
- `RunContext` binds run, goal, transport, dry-run, debug, and review identity
  without mutable broker task state.
- The default GoalLoop compatibility payload is produced by the pure
  `project_legacy_runtime_payload()` projection. It no longer calls
  `AgentRuntime.record_external_turn()` or writes `broker_session` goals or
  turns.
- `AgentRuntime.continue_goal()` remains only in the static-dead bridge and
  legacy stored-review resume paths. Those paths are explicitly deferred to
  Gate 5 historical-review migration and deletion.

## Gate 3 evidence: transactional review current state

Completed on 2026-07-11.

- SQLite `reviews` is the authoritative current-state table; it stores the
  review payload, execution identity, expiry, supplemental input, and a
  monotonic version. `review_events` remains the append-only audit history.
- Legacy JSONL and event-only SQLite databases are replayed deterministically
  into current state when no current-state rows exist. The migration is
  idempotent and preserves historical events.
- Approval, rejection, consumption, supplemental input, execution claim, and
  execution release use database transactions. `claim_execution` performs an
  `UPDATE ... WHERE status = 'approved'` compare-and-swap and checks the
  affected-row count.
- Missing expiry metadata on legacy pending reviews is treated as expired.
- Current-state/version, event-only migration, cross-store claim, duplicate
  approval, and failed-execution retry tests pass. Full WSL verification:
  `236 passed in 9.47s`.

## Gate 4 evidence: typed GoalLoop ports

Completed on 2026-07-11.

- `GoalLoopPorts` now consists of the named `PlanningPort`, `ObservationPort`,
  `ReviewPort`, `ExecutionPort`, `AcceptancePort`, and `RecoveryPort`
  protocols. Production `GoalLoop` construction no longer exposes a
  `Callable[..., Any]` callback bundle.
- `goal_loop_adapters.py` contains the broker adapters; test callback stubs are
  confined to `tests/test_goal_loop.py`.
- The long loop now delegates missing-plan, budget, step-review/suspension,
  and execute/post-observation transitions to dedicated handlers. The main
  loop coordinates state and recovery decisions only.
- Review approval remains bound to the stored loop snapshot and step safety
  review id; resume tests prove completed steps are not replayed.
- WSL verification: `tests/test_goal_loop.py`, `tests/test_goal_loop_flag.py`,
  and `tests/test_broker.py` passed with `56 passed in 6.32s`.

## Gate 5 evidence: default-path deletion, CI, and documentation

Completed on 2026-07-11, except that GitHub has not yet run the new workflow.

- Deleted `_run_v06_runtime_bridge` and its private
  `_build_v06_strategy_candidates` helper after static caller search found no
  callers. Fresh supported tasks enter `CommandService -> GoalLoop` only.
- Retained `_approve_plan_review_v06` and
  `_approve_plan_review_legacy` as historical `review_kind=plan` resume
  compatibility. They are not fresh-task callers; a regression verifies their
  dry-run resume is bound to the stored plan and does not consume approval.
- Added `.github/workflows/test.yml` for Python 3.11, editable dev install,
  full pytest, capabilities JSON, and offline dry-run JSON on `push` and
  `pull_request`. The workflow is configured but has no observed GitHub result.
- Updated README, runtime convergence, current status, and the Chinese WSL
  test baseline. Fedora 44 WSL recovery verification passed with `237 passed
  in 9.98s`, `vibe doctor --json` reporting zero failures, and the offline
  dry-run returning `overall_status=dry_run`.
