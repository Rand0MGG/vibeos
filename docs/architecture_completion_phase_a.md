# Architecture Completion — Phase A Baseline

Captured: 2026-07-11

This is working evidence for
[`architecture_completion_master_goal.md`](architecture_completion_master_goal.md).
It is not the master contract and may be updated only as later phases replace
the paths recorded here.

## Baseline verification

```text
wsl -d FedoraLinux-44 -- bash -lc
  "cd /mnt/e/codex_project/vibeos &&
   source /home/rand0mg/.venvs/vibeos/bin/activate &&
   python -m pytest -q"

237 passed in 9.47s
```

After adding the Phase A architecture debt markers, the full WSL suite passed
with `238 passed, 7 xfailed in 9.87s`. The xfails are deliberate completion
gates, not accepted production behavior.

At the baseline, `broker.py` is 3,316 lines. Ruff and mypy are not configured
in `pyproject.toml`; this is Phase G work, not evidence of a passing check.

## Caller and ownership inventory

| Item | Production callers / reachable path | State or adapters reached | Disposition |
| --- | --- | --- | --- |
| `CapabilityBroker.handle` | Public command entry; delegates to `CommandService.handle` | Starts trace and eventually invokes broker callbacks | retain as thin facade only |
| `CommandPorts` | Constructed in `CapabilityBroker.__init__` | Lambdas route fresh, approve, input, audit, and result metadata into Broker | replace in Phase F with typed task handler/finalizer |
| `Broker*Port` adapters | Built by `CapabilityBroker._make_goal_loop` | Each retains the whole Broker and re-enters broker planning, execution, review, acceptance, or recovery methods | replace in Phases B, D, and F |
| `execute_task_step` | `BrokerExecutionPort`; `execute_task_plan` | Re-reviews a step, calls broker `_execute`, invokes adapters, records audit | replace in Phase B with registered execution service |
| `assess_task_plan_execution` | `BrokerAcceptancePort`; `execute_task_plan` | Post-observation, verifiers, acceptance provider | move in Phase F |
| `_execute` | `execute_task_step`; historical approval fallbacks | Direct app/window/browser/clipboard/notification/portal adapter invocation | delete after Phase B caller count is zero |
| `approve_review` | `CommandPorts` callback | Claims/releases/consumes ReviewStore records; chooses plan, loop, user-input, or direct legacy flow | move command/resume ownership in Phases C and D |
| `provide_review_input` | `CommandPorts` callback | Persists input and resumes loop | move in Phase D |
| `_approve_plan_review_v06` | `approve_review` for historical `review_kind=plan` | Starts/continues `AgentRuntime`, shared session, and tool registry | replace in Phase E with safe migration or structured rejection |
| `_approve_plan_review_legacy` | `_approve_plan_review_v06` fallback | Direct `execute_task_plan` and legacy trace | remove with Phase E migration |
| `_compatibility_runtime_result` | GoalLoop result finalization | Pure projection inputs plus compatibility strategy selection | move to compatibility projection in Phase F |
| `_task_plan_to_v06_strategy` | Historical plan-review resume | Legacy strategy/tool recipe generation | delete or move to data-only conversion in Phase E |
| `_build_v06_tool_registry` | Broker initialization for `AgentRuntime` | Composes domain tool specs | remove when AgentRuntime construction disappears in Phase E |
| `AgentRuntime.continue_goal` | `_approve_plan_review_v06` only | Mutates `agent_session` goal/turn state and invokes registry tools | zero production callers in Phase E |
| `agent_session` | Broker initialization and historical v0.6 review resume | Shared session/goal/turn state | delete in Phase E |
| `ReviewStore.consume` | Approval and user-input paths in Broker | Broad `approved`/`executing`/`provided -> consumed` state mutation | split into explicit execution and input completion in Phase C |
| `ReviewStore.claim_execution` | `approve_review` | SQLite CAS `approved -> executing`; JSONL fallback when connection is absent | enforce binding, expiry, and fail-closed semantics in Phase C |

## Confirmed architecture debts

1. Fresh GoalLoop execution reaches `BrokerExecutionPort ->
   CapabilityBroker.execute_task_step -> CapabilityBroker._execute`, while
   historical runtime execution reaches `ToolRegistry -> domain tools`. This is
   a duplicate capability execution path.
2. `ReviewStore` mutates JSONL when SQLite is unavailable and its generic
   `consume()` accepts execution and user-input states. Both conflict with the
   master safety contract.
3. `GoalLoop` and `CommandService` still expose broad dynamic/callback
   boundaries. The dynamic data must be validated at persistence/transport
   boundaries before entering typed orchestration.
4. Historical plan approvals have a runtime-shaped payload but not yet a
   verified migration contract for pending-step binding. They cannot be treated
   as equivalent to current GoalLoop reviews.

## Phase A regression intent

`tests/test_architecture.py` contains passing import-boundary checks and
strict-xfail debt checks for the remaining production `continue_goal` caller,
shared session, Broker `_execute`, callable command bundle, JSONL mutation
fallback, and broad review consumption. Each xfail must be converted to an
ordinary passing test when the corresponding phase lands.

The existing GoalLoop retry tests retain attempt history but do not yet prove
that final acceptance receives only accepted successful receipts. Phase A adds
that regression as a strict xfail; Phase B/D completion must make it pass.

## Phase B checkpoint

Fresh GoalLoop step dispatch now constructs a `RunContext` and reaches
`StepExecutionService -> CapabilityRecipeRegistry -> ToolRegistry -> domain
tools`. `BrokerExecutionPort` holds only that service; it no longer holds the
Broker. Browser evidence is scoped to the command run id, so GoalLoop post-step
observation reads the evidence recorded by the registered browser tool.

`CapabilityBroker._execute` and its private direct browser/adapter helpers
were removed. Unknown review kinds now return a structured fail-closed result
instead of falling through to direct capability execution. A source-level
regression test forbids direct adapter mutation calls from `broker.py`.

Post-execution observation now accepts the immutable attempt ID from GoalLoop.
The observation service reopens that existing browser receipt only while
building the post-step observation, and acceptance does the same for the
accepted receipt. This preserves attempt isolation without making GoalLoop
depend on browser-specific state.

The retry receipt invariant is now implemented: failed/no-progress receipts
remain in `PlanAttempt` history, while `step_results` contains only the current
accepted receipt for each completed step. The former strict-xfail regression is
now an ordinary passing test.

Verification on WSL (`FedoraLinux-44`, Python environment
`/home/rand0mg/.venvs/vibeos`):

```text
python -m pytest tests/test_tool_modules.py -q     -> 3 passed
python -m pytest tests/test_goal_loop.py -q        -> 31 passed
python -m pytest tests/test_broker.py -q           -> 25 passed
python -m pytest tests/test_architecture.py -q     -> 4 passed, 5 xfailed
```

The five remaining xfails are the intentionally deferred Phase C, E, and F
completion gates. The Phase B sources also pass `python -m compileall -q
src/vibeos tests` and `git diff --check`.
