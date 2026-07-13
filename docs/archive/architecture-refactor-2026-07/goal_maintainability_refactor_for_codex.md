# VibeOS Maintainability Refactor Goal for Codex

Last updated: 2026-07-11

## Send this goal to Codex

> Refactor VibeOS for maintainability without adding user-facing capabilities or weakening any safety boundary. `GoalLoop` must remain the sole orchestration state machine for every supported task. Make `CapabilityBroker` a thin compatibility facade, move domain-specific tool implementations out of it, replace `GoalLoop`'s callback-heavy construction with typed ports, eliminate default-path reliance on the shared `broker_session` and the legacy `AgentRuntime` loop, and make review state truly transactional in SQLite. Preserve the current CLI, HTTP, and D-Bus semantic result contract. Work in small gated stages: establish a behavior baseline before changing structure, keep tests green at every stage, and do not delete a legacy path until its live callers and historical-review compatibility have been characterized. Add deterministic CI and update architecture documentation. Do not edit this goal document.

The rest of this document is the mandatory scope, migration plan, and acceptance contract.

---

## 1. Scope and non-goals

This is an architecture and maintainability refactor. It must not add voice, memory, filesystem automation, shell execution, raw D-Bus execution, computer-use automation, or desktop capabilities. Do not replace the project with an agent framework or a workflow engine.

Preserve these invariants:

- the registered capability allowlist remains the only executable authority;
- `PermissionPolicy` remains the authority for `allow`, `deny`, and `review_required`;
- L2 approval remains bound to the persisted plan, step, and review payload; it must never reparse the original request to decide what to execute;
- L3 remains rejected;
- `--dry-run` is side-effect free and never consumes a review;
- provider output may guide bounded semantics but never grants execution authority;
- normal traces retain no raw utterance, raw provider input/output, or supplemental input.

No public result field may disappear or change meaning without an explicit, tested compatibility migration. In particular preserve `status`, `execution_status`, `acceptance_status`, `overall_status`, `review_id`, `trace_run_id`, `audit_id`, and current task-plan result fields.

## 2. Verified starting point

Treat this as a starting hypothesis and re-check it before editing code:

- `src/vibeos/broker.py` is about 4,000 lines and combines ingress, planning bootstrap, GoalLoop assembly, tool definitions, review resume, legacy projections, audit, and result formatting.
- `GoalLoop` is the default supported-task path, but it receives a large set of loose callbacks, many typed as `Any`.
- the broker still owns `broker_session` / `AgentRuntime` state and v0.6 compatibility helpers; some apparent legacy methods still have live callers.
- `ReviewStore` has SQLite event persistence, but no authoritative current-state row with a database-level compare-and-swap transition.
- a GitHub Actions workflow is absent.

Do not assume a method is dead from its name. Create and retain a caller inventory for `_run_v06_runtime_bridge`, `_approve_plan_review_legacy`, `_record_legacy_execution_trace`, `_execute`, compatibility projections, and every `AgentRuntime.continue_goal()` caller. Each retained item needs an explicit caller, reason, and regression test; each removed item needs a static-search proof and an equivalent behavior test.

## 3. Target ownership model

```text
CLI / HTTP / D-Bus
        |
        v
CommandService
  - request validation and transport-neutral dispatch
  - trace lifecycle and final audit
  - fresh request, review approval, rejection, and supplemental-input resume
  - public CommandResult formatting
        |
        +--> PlanningPort
        +--> GoalLoop
        |
        v
GoalLoop
  - the only supported-task control state machine
  - observe -> review -> execute -> observe -> verify
  - retry / repair / replan / suspend / resume / finish
        |
        +--> ExecutionPort
        +--> ObservationPort
        +--> ReviewPort
        +--> PlanningPort
        +--> AcceptancePort
        +--> RecoveryPort
        |
        v
Domain tool modules -> existing adapters
```

`CapabilityBroker` may remain for source compatibility, dependency assembly, `capabilities()`, `pending_reviews()`, and adapter access expected by callers. Its `handle()` method must delegate to `CommandService`; it must not own a second task loop, domain tool handlers, or mutable active-task state.

Keep the number of new abstractions small. Create an interface only when it separates a genuine boundary. Do not replace the broker with another giant coordinator or create empty service wrappers.

## 4. Required typed boundaries

Define stable protocols and use domain types rather than `Callable[..., Any]` at the GoalLoop boundary. Exact names may differ, but their responsibility must not.

```python
class PlanningPort(Protocol):
    def plan(self, request: CommandRequest) -> PlanningArtifacts: ...
    def replan(self, current: PlanningArtifacts, request: CommandRequest,
               decision: ReplanDecision, failure: FailureClassification) -> PlanningArtifacts: ...
    def resume_with_user_input(self, stored: PlanningArtifacts,
                               request: CommandRequest) -> PlanningArtifacts: ...

class ExecutionPort(Protocol):
    def execute_step(self, *, context: RunContext, plan: TaskPlan,
                     step: TaskStep, request: CommandRequest,
                     attempt_id: str) -> StepExecutionResult: ...

class ReviewPort(Protocol):
    def review_step(self, *, context: RunContext, plan: TaskPlan,
                    step: TaskStep, observation: LoopObservation | None) -> StepReviewDecision: ...
    def claim_execution(self, review_id: str) -> bool: ...
    def consume(self, review_id: str) -> ReviewRequest | None: ...
    def release_execution(self, review_id: str) -> ReviewRequest | None: ...

class ObservationPort(Protocol): ...
class AcceptancePort(Protocol): ...
class RecoveryPort(Protocol): ...
```

Use a run-scoped context, not mutable broker fields:

```python
@dataclass(frozen=True)
class RunContext:
    run_id: str
    goal_id: str
    transport: str | None
    dry_run: bool
    debug: bool
    review_id: str | None
```

Normal execution must not depend on `self.agent_session` or mutate a shared task session. If older runtime payloads need session/goal/turn data, generate them through a pure projection from `GoalLoopResult`, planning artifacts, and immutable receipts. The projection must not invoke `AgentRuntime.continue_goal()` or mutate shared state.

## 5. Domain tool ownership

Move concrete registered tool handlers out of `broker.py` into domain-owned modules. A suggested structure is:

```text
src/vibeos/tools/
  apps.py
  windows.py
  browser.py
  clipboard.py
  notifications.py
  system.py
  fixtures.py
  registry.py
```

Each domain module owns its tool IDs, typed dependency object, adapter invocation, result normalization, domain evidence, and registration function. Keep existing adapters (`AppRegistry`, `WindowRegistry`, `PortalAdapter`, `ClipboardAdapter`, and `NotificationAdapter`) as adapters; do not recreate them.

The central assembly may compose domain specs but must have no domain-specific handler body:

```python
def build_tool_registry(dependencies: ToolDependencies) -> ToolRegistry:
    return ToolRegistry((
        *app_tool_specs(dependencies.apps),
        *window_tool_specs(dependencies.windows),
        *browser_tool_specs(dependencies.browser),
        *clipboard_tool_specs(dependencies.clipboard),
        *notification_tool_specs(dependencies.notifications),
        *system_tool_specs(dependencies.system),
    ))
```

Browser observation and verification may remain in their existing dedicated services when that is clearer. Do not force unrelated observation or verifier code into a tool module merely to satisfy a directory layout.

## 6. ReviewStore transactional contract

SQLite must become the authority for current review state. Keep append-only review events for audit history and retain legacy JSONL only as a migration/import source, not as the authority.

Use a current-state table with at least review id, status, kind, plan/step identifiers, persisted execution payload, snapshot payload, supplemental-input state, timestamps, and monotonically increasing version. Use a separate event table containing `review_id`, event type, payload, and timestamp.

Every transition must be atomic at the database level. For example, a claim is successful only when this update affects exactly one row:

```sql
UPDATE reviews
SET status = 'executing', version = version + 1
WHERE review_id = ? AND status = 'approved';
```

Required behavior:

```text
pending -> approved | rejected | provided
approved -> executing
executing -> consumed | approved
```

Return structured invalid-transition results; do not silently rewrite state. Validate expiration within the same transition operation. A failed real execution releases `executing` back to `approved`; a successful execution consumes it. The state used to resume must identify the pending step and must not replay successfully completed steps.

Migration requirements:

- import legacy JSONL and existing event-only SQLite stores exactly once and idempotently;
- preserve historical events and review IDs;
- derive one current row per review deterministically from the event sequence;
- version the migration and execute it in a transaction;
- test a rerun, partial legacy data, and an interrupted/rolled-back migration.

Use separate SQLite connections in tests. A process-local lock may be an optimization, but cannot be the correctness mechanism. Prove the claim operation using multiple `ReviewStore` instances; add a multi-process test if the project test environment can run it reliably.

## 7. Staged execution plan and gates

Do not perform this as one rewrite. A later stage must not begin deletion until the prior gate passes.

### Gate 0: baseline and compatibility inventory

1. Run the full deterministic test suite.
2. Add or preserve characterization tests for L0, L1, L2, L3, review approval/rejection/expiry, user-input resume, retry/replan, verifier failure, HTTP/D-Bus failures, trace privacy, and offline dry-run.
3. Capture a machine-readable compatibility fixture for CLI, HTTP, and D-Bus semantic result fields.
4. Produce the caller inventory described in section 2.

Acceptance: all baseline tests pass; every identified legacy path is classified as `live`, `historical-resume-only`, or `dead` with evidence.

### Gate 1: domain tool extraction

Move tool handlers into domain modules without altering routing, adapter behavior, capability IDs, review risk levels, verifier requirements, or response payloads.

Acceptance: domain module tests prove their `ToolSpec` sets; central assembly has the same expected tool IDs; public behavior fixtures remain unchanged.

### Gate 2: command service and per-run state

Introduce `CommandService` and `RunContext`. Route fresh requests, approvals, rejection, and supplemental-input resumption through it and then through `GoalLoop`. Replace mutable runtime compatibility behavior with a pure projection.

Acceptance: every supported fresh/review/resume task enters GoalLoop; concurrent runs do not share mutable task state; `AgentRuntime.continue_goal()` has no default-path caller; no implicit `None` fallback in `CapabilityBroker.handle()` remains.

### Gate 3: transactional reviews

Add the current-state table, migration, compare-and-swap transitions, and cross-instance tests described in section 6.

Acceptance: exactly one concurrent approval can claim a side effect; success consumes; failure releases; expired/consumed reviews cannot execute; resume does not replay completed steps.

### Gate 4: GoalLoop ports and internal simplification

Replace callback injection with the named ports. Split long loop code into readable transition handlers such as review, execution, verification, failure, replan, suspend, and finish. Keep Python control flow explicit; do not add a workflow DSL.

Acceptance: no broad `Callable[..., Any]` dependency surface remains in GoalLoop; routes with verifiers require evidence-backed progress; non-verifier routes accept only bounded successful receipts; dry-run remains deterministic.

### Gate 5: delete dead paths, CI, and documentation

Delete only paths classified dead and covered by replacement tests. Add `.github/workflows/test.yml` for `push` and `pull_request`, using Python 3.11+:

```bash
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
python -m pytest -q
vibe capabilities --json
vibe ask "search web for hello" --json --offline --dry-run
```

Acceptance: no legacy loop or stale feature flag remains on the default path; CI workflow exists and is syntactically valid; documentation matches the code. Do not claim CI has passed until the workflow has actually run successfully.

## 8. Mandatory regression tests

Add or update tests that prove:

- all fresh supported tasks, approved reviews, and supplemental-input resumes use GoalLoop;
- a completed step is not replayed after review or user-input resume;
- retry/replan does not duplicate a verified side effect;
- duplicate approval dispatches a reviewed side effect at most once;
- failed approved execution can be explicitly retried;
- review transitions are correct across independent store instances and do not rely on a Python lock alone;
- tool module registration preserves the complete capability-to-tool mapping;
- compatibility projection is pure and does not mutate shared task state;
- CLI JSON, HTTP, and D-Bus preserve the semantic result contract;
- unexpected HTTP and D-Bus broker exceptions are structured failures;
- normal traces omit raw utterances, provider payloads, nested credential values, and supplemental input;
- `vibe ask "search web for hello" --json --offline --dry-run` succeeds without a provider request;
- no model output can dispatch shell, raw D-Bus, or unregistered actions.

## 9. Documentation and final handoff

Update `README.md`, `docs/current_status.md`, `docs/runtime_convergence.md`, and affected Chinese documentation. State the actual architecture, database migration semantics, trace policy, CI workflow path/status, current test command/result, and VM-only verification boundary. Remove stale test counts and stale version claims.

Before completion run and report exact results for:

```bash
python -m pytest -q
vibe capabilities --json
vibe ask "search web for hello" --json --offline --dry-run
```

The final handoff must include:

1. the new ownership boundaries and a concise module map;
2. every deleted legacy path and every intentionally retained compatibility interface, with caller and reason;
3. ReviewStore schema, migration, transition semantics, and duplicate-approval behavior;
4. exact commands and observed test results;
5. CI workflow path and actual run status;
6. user-run-only Linux GNOME VM checks: systemd user service, GNOME extension, D-Bus window control, notifications, clipboard, portal navigation, and browser observation;
7. migration notes for internal callers.

## 10. Overall completion criteria

Do not declare the overall refactor complete unless all of the following are true:

- GoalLoop is the sole supported-task state machine;
- CapabilityBroker is a thin facade with no domain tool handlers or alternate hidden loop;
- normal runs are isolated by `RunContext`, not `broker_session`;
- domain tools are independently owned and centrally composed;
- GoalLoop has typed ports and readable transition handlers;
- legacy compatibility output is a pure projection;
- SQLite current state is authoritative and execution claim is database-atomic;
- no completed side effect is replayed by resume or duplicate approval;
- safety, trace privacy, and public result semantics are preserved;
- deterministic tests pass and CI is present;
- no new end-user capabilities were added.

Reducing `broker.py` is a useful signal, not an acceptance criterion. Do not merely move its complexity into another giant class.
