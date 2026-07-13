# Architecture Completion - Final Acceptance Audit

Captured: 2026-07-12

This is the final handoff and requirement-by-requirement audit for
[`architecture_completion_master_goal.md`](architecture_completion_master_goal.md).
The master contract was not modified. Evidence below is from the working tree
and the documented Fedora 44 WSL environment.

## Scope and completion statement

The refactor adds no user-facing capability. It converges the pre-existing
capability surface on one task state machine and one registered execution path.
`AgentRuntime` remains only as an isolated compatibility data model with
isolated tests; no production command path constructs it or calls its loop.

The completion gates in the master contract are satisfied by the code,
architecture tests, static checks, deterministic full suite, and offline smoke
tests recorded below. Real GNOME desktop integration remains deliberately
separate and is not represented as a passing desktop test.

## Phase results

| Phase | Result | Main ownership change | Checkpoint evidence | Compatibility retained |
| --- | --- | --- | --- | --- |
| A | complete | Baseline caller/state inventory and regression markers | 237 baseline tests; 238 passed with 7 temporary xfails | Historical records and public JSON shapes only |
| B | complete | `StepExecutionService`, recipes, registry, domain tools replace Broker execution | `test_tool_modules` 3, `test_goal_loop` 31, `test_broker` 25 passed | Public capability names and output fields |
| C | complete | SQLite authority, explicit review transitions, fail-closed mutation errors | `test_reviews` 16 and `test_broker` 26 passed | One-time JSONL import only |
| D | complete | Planning service, versioned snapshot codec, review resume service | GoalLoop 31, Broker 27, architecture 7 passed (later gates still marked at that checkpoint) | Version-0 decoding only for existing current-format rows |
| E | complete | Verifiable legacy review migration through GoalLoop; old runtime removed from production | Historical safe/unsafe and approval tests: 13 passed; grouped tests: 85 passed | Data-only `AgentRuntime` and isolated historical tests |
| F | complete | Composition root, typed command handler, extraction of review/acceptance/recovery/projection | Focused Broker/GoalLoop/review/boundary/architecture suite: 93 passed | Thin facade compatibility entry points |
| G | complete | Ruff, scoped strict typing, CI jobs, updated status documents, final verification | Ruff, format, mypy, 263-test suite, capability and offline smoke all passed | Legacy modules outside the strict-core scope are documented below |

Phase records are retained in
[`architecture_completion_phase_a.md`](archive/architecture-refactor-2026-07/architecture_completion_phase_a.md),
[`architecture_completion_phase_b.md`](archive/architecture-refactor-2026-07/architecture_completion_phase_b.md),
[`architecture_completion_phase_c.md`](archive/architecture-refactor-2026-07/architecture_completion_phase_c.md),
[`architecture_completion_phase_d.md`](archive/architecture-refactor-2026-07/architecture_completion_phase_d.md),
[`architecture_completion_phase_e.md`](archive/architecture-refactor-2026-07/architecture_completion_phase_e.md),
[`architecture_completion_phase_f.md`](archive/architecture-refactor-2026-07/architecture_completion_phase_f.md),
and [`architecture_completion_phase_g.md`](archive/architecture-refactor-2026-07/architecture_completion_phase_g.md).

## Final dependency graph

```text
CLI / HTTP / D-Bus
        |
        v
CapabilityBroker (construction facade only)
        |
        v
CommandService -- TaskCommandHandler --> TaskApplicationService
        |                                      |
        |                                      v
        |                                  GoalLoop
        |                         / planning | observation | review \
        |                        / execution | acceptance  | recovery \
        v                       v            v             v           v
AuditResultRecorder      PlanningService  Observation   ReviewService  StepExecutionService
                                                                  |              |
                                                                  v              v
                                                            ReviewStore     CapabilityRecipeRegistry
                                                            (SQLite)               |
                                                                                   v
                                                                            ToolRegistry -> domain tools
                                                                                   |
                                                                                   v
                                                                            existing adapters
```

`runtime_composition.py` is the only composition root. It builds immutable
`RuntimeComponents`; no core service receives, imports, or retains Broker.

| Component | Responsibility and state owner | Narrow dependencies | Adapters reachable |
| --- | --- | --- | --- |
| `CommandService` | command routing, trace lifecycle, final handoff | typed task handler, result recorder, trace store | none |
| `TaskApplicationService` | fresh start, approve, input, resume, compatibility direct-plan entry points | planning, GoalLoop services, review store, projector | only through GoalLoop execution service |
| `GoalLoop` | sole production plan state machine; attempt and accepted-receipt selection | typed planning/observation/review/execution/acceptance/recovery ports | none directly |
| `PlanningService` / `loop_snapshot.py` | plan transitions and validated versioned snapshots | intent/candidate/understanding providers | none |
| `ReviewService` / `ReviewResumeService` | policy review, suspension and typed stored-review resume | policy, ReviewStore, PlanningService, GoalLoop factory | none directly |
| `ReviewStore` | authoritative SQLite rows, event history, schema migration, state transitions | SQLite only | none |
| `StepExecutionService` | validated step -> recipe -> tools -> normalized receipt and audit | ToolRegistry, recipe registry, audit | registered domain tools only |
| `AcceptanceService` / `RecoveryService` | verification/semantic acceptance; failure classification and replanning | verifier/acceptance or classifier/replanner interfaces | none |
| `CommandResultProjector` / `AuditResultRecorder` | read-only public compatibility projection and audit metadata | review store, policy, audit | none |

## Broker audit

The checked-out baseline `broker.py` was **3,316** lines; the final facade is
**193** lines. Its remaining responsibilities are construction through
`compose_runtime`, `handle()` delegation, capability and pending-review
queries, and typed compatibility methods that delegate to `TaskApplicationService`
or `ReviewService`. It owns no plan, execution, adapter, review transition,
review-resume, historical migration, acceptance, strategy, tool, or result
projection logic.

`tests/test_architecture.py` rejects prohibited Broker methods, direct adapter
calls, core reverse imports, callable `CommandPorts`, and the deleted
GoalLoop-to-Broker forwarding layer.

## Execution audit

Every capability in `vibe capabilities --json` reaches exactly one registered
production domain-tool implementation. Recipe-only resolver/observer tools do
not invoke an additional capability adapter.

| Capability | Registered recipe/tool owner | Single adapter or bounded implementation |
| --- | --- | --- |
| `app.list` | `tools/apps.py:app.list` | `AppRegistry.list_apps` |
| `app.open` | `tools/apps.py:app.open` | `AppRegistry.open_app` after registered resolver |
| `app.search_history` | `tools/fixtures.py` sequence | bounded `AppSearchFixture`, no arbitrary UI control |
| `window.list` | `tools/windows.py:window.list` | `WindowRegistry.list_windows` |
| `window.focus` | `tools/windows.py:window.focus` | `WindowRegistry.focus` |
| `window.minimize` | `tools/windows.py:window.minimize` | `WindowRegistry.minimize` |
| `window.maximize` | `tools/windows.py:window.maximize` | `WindowRegistry.maximize` |
| `window.close` | `tools/windows.py:window.close` | `WindowRegistry.close` |
| `browser.open_url` | `tools/browser.py` action | `PortalAdapter.open_uri` |
| `browser.open_named_target` | browser resolver + resolved-target action | the same browser action after local catalog resolution |
| `browser.open_site_search` | `tools/browser.py` action | `PortalAdapter.open_uri` |
| `browser.search_web` | `tools/browser.py` action | `PortalAdapter.open_uri` |
| `portal.open_uri` | `tools/system.py:portal.open_uri` | `PortalAdapter.open_uri` for this distinct portal capability |
| `system.status` | `tools/system.py:system.status` | bounded `PortalAdapter.status` and capability query |
| `notification.send` | `tools/notifications.py` | `NotificationAdapter.send` |
| `clipboard.write` | `tools/clipboard.py` | `ClipboardAdapter.write` |
| `media.search` | `tools/media.py` | typed unavailable receipt; no local media adapter exists |
| `media.play` | `tools/media.py` | typed unavailable receipt; no local media adapter exists |
| `media.pause` | `tools/media.py` | typed unavailable receipt; no local media adapter exists |

There is no Broker execution fallback, old-runtime production registry, or
second adapter invocation implementation. `tests/test_tool_modules.py` and
the architecture checks cover the registry route and absence of Broker adapter
mutation calls.

## Historical approvals and removed legacy paths

`LegacyPlanReviewMigrator` accepts a historical `review_kind=plan` record only
when the stored record proves: format/review kind, plan ID and deterministic
full-plan hash, exactly one pending step, action, canonical target hash,
safety-review ID and approval, registered capability, matching stored review
facts, and current policy compatibility. It reconstructs current typed
planning and loop state from the stored plan; it never reparses the original
utterance.

Missing, changed, multi-step, expired, unsupported, or inconsistent bindings
return `legacy_review_unverifiable` with `fresh_command_required: true`; no
adapter is dispatched. Safe records resume through the current GoalLoop.

Removed production legacy code includes Broker `_execute`, direct Broker
adapter helpers, Broker GoalLoop port forwarders, `CommandPorts`,
`goal_loop_adapters.py`, production `AgentRuntime.continue_goal` callers, and
the shared `agent_session`/`broker_session`. `AgentRuntime` types and tests are
retained solely for historical data/test compatibility.

## Review state machine and failure semantics

SQLite is the only authoritative mutation store. It uses WAL, busy timeout,
explicit `BEGIN IMMEDIATE` transactions, rollback-and-fail-closed handling,
structured `review_events`, indexes, and idempotent schema migrations. JSONL
is read only for a one-time legacy import into an empty database; it never
becomes a fallback state authority.

| Operation | CAS precondition / transition |
| --- | --- |
| approve | `pending -> approved` |
| reject | `pending -> rejected` |
| claim execution | verified, unexpired `approved -> executing` in one `BEGIN IMMEDIATE` transaction |
| complete execution | `executing -> consumed` |
| release failed execution | `executing -> approved` |
| provide user input | `user_input` and `pending -> provided` |
| consume user input | `user_input` and `provided -> consumed` |
| expire | `pending|approved -> expired`, including claim/list/read expiry checks |
| supersede | `approved|executing -> superseded` |

The claim checks the immutable review binding before its update, so duplicate
or concurrent approvals cannot execute twice. Any SQLite error raises
`ReviewPersistenceError`; `CommandService` returns
`review_persistence_unavailable` before a side effect is sent.

## Retry and acceptance correctness

All attempts are retained as `PlanAttempt` records. GoalLoop separately keeps
only the accepted current `StepExecutionResult` for each completed required
step; final acceptance receives that accepted collection, not historical
failures. Resume and same-plan recovery restore completed accepted steps and
do not replay predecessors. Replanning is constrained by the explicit
semantic-equivalence checks in GoalLoop before prior completion can be retained.

## Typing, CI, and documentation

`mypy --strict` covers the command boundary, task application service,
GoalLoop and ports, planning/snapshot, execution and registry, review/
resume/migration/store, acceptance, recovery, result projection, and
composition root (16 files). Dynamic external or historical payloads are
validated at their persistence/snapshot boundaries before entering this typed
core. Unrelated legacy modules and individual domain tool implementations are
temporarily outside the strict scope; they cannot reintroduce Broker
dependencies because architecture tests enforce the dependency boundary.

[`../.github/workflows/test.yml`](../.github/workflows/test.yml) defines
`static-quality` (Ruff lint/format/scoped strict mypy) and
`deterministic-tests` (pytest, capabilities JSON, offline dry-run) on Python
3.11. No remote workflow run was observed, so no remote CI success is claimed.

Architecture/status documentation is updated in
[`runtime_convergence.md`](architecture/runtime_convergence.md),
[`current_status.md`](architecture/current_status.md), and the phase records cited above.

## Exact verification evidence

Executed in Fedora 44 WSL as `rand0mg` with
`/home/rand0mg/.venvs/vibeos/bin/activate`:

```text
python -m ruff check src tests
  All checks passed

python -m ruff format --check src tests
  122 files already formatted

python -m mypy --strict
  Success: no issues found in 16 source files

python -m pytest -q
  263 passed in 11.90s

vibe capabilities --json
  exit 0; 19 registered capabilities

vibe ask "search web for hello" --json --offline --dry-run
  exit 0; status=dry_run

vibe doctor --json
  overall=warn; ok=4, warn=8, fail=0
```

The WSL command host may print a Windows localhost/NAT forwarding warning
after successful commands. It did not affect command exit status or the Python
environment; the documented interpreter, `vibe`, and `vibed` paths all resolve
from the `rand0mg` virtual environment.

## Deferred real-GNOME verification

The following require a real GNOME Wayland VM or desktop session and were not
falsely marked as passing in WSL: systemd user daemon, GNOME Shell extension,
D-Bus window control, real application opening, real clipboard write, real
notification delivery, real portal navigation, and real browser observation.

## Completion-criteria audit

| Master criteria | Status and evidence |
| --- | --- |
| 1-4 | pass: fresh/current resumes use GoalLoop; each capability uses the registry/tool path documented above |
| 5-10 | pass: Broker facade audit and AST architecture tests show no planning, execution, adapter, acceptance, resume, or projection ownership |
| 11-15 | pass: core/domain/persistence modules have no Broker dependency; typed services replace callable/Broker-forwarding ports |
| 16-20 | pass: no production old-runtime caller/session; migration is bound, GoalLoop-based, and never reparses legacy utterances |
| 21-29 | pass: SQLite authority, fail-closed errors, explicit CAS transitions, duplicate claim protection, release and expiry behavior are implemented and tested |
| 30-34 | pass: full attempt history, accepted-receipt selection, no predecessor replay, and semantic-equivalence retention are exercised by GoalLoop tests |
| 35-39 | pass: scoped strict core, validated dynamic boundaries, compatible projection, unchanged policy, and trace handling are covered |
| 40-46 | pass: architecture tests, Ruff, strict mypy, 263-test suite, smoke tests, CI jobs, and updated documentation are recorded above |
| 47-48 | pass: no new user-facing capability was added; responsibilities are split among cohesive services rather than another coordinator |

The audit was performed after all Phase G checks, using the contract from its
first scope requirement through its final handoff requirements.
