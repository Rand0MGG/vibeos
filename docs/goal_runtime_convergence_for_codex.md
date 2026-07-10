# VibeOS Runtime Convergence Goal for Codex

## How to use this document

Send the following goal to Codex together with this repository:

> Refactor VibeOS so that `GoalLoop` becomes the sole default orchestration path for supported task execution. Preserve the useful execution primitives, tool registry, session/ledger data, and adapter integrations from `AgentRuntime`, but do not merge all runtime responsibilities into `GoalLoop`. Remove the duplicated legacy loop from `CapabilityBroker` after behavior parity is proven. The end state must have one durable, inspectable run/step state machine; one execution path for review, resume, retry, replan, acceptance, trace, and audit; and no permanent feature flag or compatibility loop that can diverge from the default path. Do not add new end-user capabilities during this refactor. Implement the change incrementally, keep all existing behavior covered, run the full test suite, and update the documentation to describe the new default architecture.

The rest of this document is the required technical contract and acceptance criteria for that goal.

---

## 1. Objective

VibeOS currently contains overlapping control-flow responsibilities:

- `CapabilityBroker` still owns a legacy `_run_task_plan_loop()`.
- `GoalLoop` implements newer step-level orchestration but is gated by `VIBEOS_ENABLE_GOAL_LOOP` and is not the default path.
- `AgentRuntime` contains useful session, ledger, strategy, tool-registry, and execution behavior.
- review, trace, audit, and loop snapshots each carry part of the execution state.

Refactor the system into a single maintainable execution spine without weakening the capability allowlist, permission policy, structured planning, or post-execution acceptance behavior.

This is an architectural convergence task, not a capability-expansion task.

## 2. Required target architecture

```text
CLI / D-Bus / HTTP
        |
        v
CapabilityBroker
  - request ingress
  - trace-session bootstrap
  - planning bootstrap / resume lookup
  - GoalLoop invocation
  - CommandResult formatting and final audit
        |
        v
GoalLoop
  - owns the typed LoopState transition sequence
  - observe_pre -> step review -> execute one step -> observe_post -> verify
  - retry / repair / replan / suspend / resume / finish
  - owns no adapter-specific side effects
        |
        +--> AgentRuntime / step executor
        |      - execute one identified step
        |      - tool registry and adapter invocation
        |      - execution receipt and run ledger updates
        |
        +--> ObservationService
        +--> Planner / Replanner
        +--> PermissionPolicy / ReviewStore
        +--> TraceStore / AuditLog
```

### 2.1 `CapabilityBroker`

`CapabilityBroker` may retain public compatibility methods, dependency assembly, planning bootstrap, and response formatting. It must not retain an independent task-loop state machine, retry loop, replan loop, or duplicated review/resume workflow.

When the refactor is complete, a supported task must reach execution through `GoalLoop` whether it is a fresh request, a reviewed request, or a resumed user-input request.

### 2.2 `GoalLoop`

`GoalLoop` is the only owner of the task-control state machine. It must own typed state transitions for at least:

- `planned`
- `observing_pre`
- `waiting_review`
- `executing`
- `observing_post`
- `verifying`
- `retrying` / `replanning`
- `waiting_user_input`
- `completed` / `failed` / `rejected`

It may call ports supplied by the broker, but it must not directly depend on D-Bus, HTTP, CLI parsing, or concrete desktop adapters.

### 2.3 `AgentRuntime`

Preserve the useful runtime primitives, but narrow their responsibility:

- session and goal/turn identity where still needed;
- tool registry and structured tool invocation;
- run-ledger/attempt receipt generation;
- execution of one explicit `TaskStep` or equivalent execution unit;
- compatibility-only strategy execution if required during migration.

`AgentRuntime` must not independently choose replan policy, own the permission decision, or execute a second hidden task-control loop.

### 2.4 Durable state and review

Treat loop state, reviews, and step execution as one consistency boundary.

For this local desktop prototype, SQLite is an acceptable persistence implementation. A heavier distributed workflow system is not required. The implementation must nonetheless make the following semantics explicit and testable:

- a review is bound to one stored plan/loop snapshot;
- review status transitions are atomic;
- concurrent approval attempts cannot execute the same reviewed side effect twice;
- a resumed loop continues from its stored pending step rather than replaying completed steps;
- every executable step has stable run/step/attempt identity and an execution receipt;
- retry or recovery must not duplicate an already-recorded successful side effect.

Append-only JSONL may remain an audit export format, but it must not be the sole authority for transactional review or resume state.

## 3. Mandatory invariants

1. The existing capability registry remains the authority for executable actions. No arbitrary shell, raw D-Bus, file deletion, or unregistered execution path may be introduced.
2. Step safety review remains the sole authority for `allow`, `deny`, and `review_required` decisions.
3. `execution_status`, `acceptance_status`, and `overall_status` retain their current public meaning.
4. L2 approval remains bound to the reviewed stored action/plan; approval must never reparse the original utterance.
5. `--dry-run` does not consume a pending approval or create a real desktop side effect.
6. A failure of planning, transport, observation, verification, or execution is represented structurally and remains traceable.
7. Model/provider output may guide semantics but never grants direct execution authority.
8. Trace storage must not silently retain unbounded raw model or user payloads. Add an explicit retention/redaction policy or limit persisted payloads to the debug mode with documented safeguards.

## 4. Migration plan

Implement in small, verifiable stages. Do not begin by deleting the old loop.

### Stage A: characterize current behavior

- Add or preserve contract tests for supported L0/L1/L2/L3 paths.
- Cover fresh execution, `review_required`, `approve --dry-run`, real approval, rejection, expiration, user-input resume, failed step, retry/replan, and acceptance failure.
- Record the expected public result shape, review semantics, audit metadata, and trace/run identity.

### Stage B: establish the execution contract

- Introduce or refine typed interfaces for loop state, step execution request, execution receipt, review transition, and persisted snapshot.
- Extract a single-step execution API from the useful parts of `AgentRuntime`.
- Ensure `GoalLoop` consumes that API rather than duplicating adapter dispatch.

### Stage C: make `GoalLoop` behavior-complete

- Route fresh supported-task execution through `GoalLoop`.
- Route reviewed and user-input-resumed tasks through the same `GoalLoop` path.
- Preserve the structured planning artifacts, attempts, verifier evidence, and acceptance results already exposed by the current system.
- Enable the path by default; do not require `VIBEOS_ENABLE_GOAL_LOOP=1` for normal operation.

### Stage D: remove duplication

- Delete the legacy `CapabilityBroker._run_task_plan_loop()` and its duplicated state transitions once contract tests prove parity.
- Remove `VIBEOS_ENABLE_GOAL_LOOP` rather than leaving it as a permanent split-brain switch.
- Remove dead compatibility helpers and duplicated tests that only exist for the deleted path.

### Stage E: operational hardening

- Make review/resume persistence atomic and concurrency-safe.
- Ensure HTTP and D-Bus return equivalent structured failures for unexpected broker errors.
- Add a bounded trace-retention/redaction policy.
- Update the status and architecture documents so their test counts, default behavior, and version claims match the code.

## 5. Non-goals

Do not use this task to:

- add arbitrary shell execution;
- add new desktop capabilities merely to demonstrate the refactor;
- replace the project with LangGraph, AutoGen, or another framework;
- introduce a new agent DSL;
- move all code into `GoalLoop` or create another large coordinator object;
- depend on a live model, public network, or real desktop state for unit tests;
- leave both control loops enabled indefinitely.

Borrow durable-workflow principles where useful, but keep VibeOS's domain model and capability boundary under host control.

## 6. Required verification

Before declaring completion, Codex must:

1. Run `python -m pytest -q` in the configured WSL development environment.
2. Add focused tests proving that all supported paths use `GoalLoop` by default.
3. Add tests for exact-once review consumption / duplicate approval protection and resume-without-replay behavior.
4. Add tests for structured transport failures over both HTTP and D-Bus.
5. Add tests for the trace retention/redaction policy.
6. Run `vibe doctor --json`, `vibe capabilities --json`, and representative offline dry-run commands.
7. Report the exact commands and results, plus any Linux VM checks that remain user-run only.

## 7. Completion criteria

This refactor is complete only when all of the following are true:

- `GoalLoop` is the default and sole supported-task orchestration path.
- `CapabilityBroker` no longer owns a second task control loop.
- `AgentRuntime` is a bounded step-execution/runtime service rather than a competing orchestration path.
- Review, resume, and step execution semantics are transactionally safe for a local multi-threaded daemon.
- No completed step is replayed merely because the process resumed or a review was submitted twice.
- The legacy feature flag and legacy loop have been removed.
- Existing public command result fields and safety boundaries remain compatible.
- The full test suite and newly added architectural contract tests pass.
- Documentation describes the actual default behavior and current verification baseline.

## 8. Expected Codex handoff

The final handoff must contain:

- a concise summary of the new ownership boundaries;
- the list of deleted legacy paths and feature flags;
- the persistence/concurrency semantics implemented;
- test commands and observed results;
- any intentionally deferred VM-only checks;
- a short migration note for callers that relied on legacy internal behavior.
