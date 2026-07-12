# VibeOS Architecture Completion Master Goal

## Mission

Complete the current VibeOS maintainability refactor without adding new user-facing capabilities.

The purpose of this task is to establish:

1. one production orchestration path;
2. one production capability execution path;
3. explicit ownership of runtime state;
4. no reverse dependency on `CapabilityBroker`;
5. safe migration or rejection of historical approvals;
6. strict transactional review semantics;
7. correct retry and accepted-receipt behavior;
8. enforceable architecture boundaries.

This document is the master architecture contract and acceptance specification.

Implement it through mandatory phases and verification checkpoints. Do not attempt to satisfy the contract through one unreviewed rewrite, but do not stop after only documenting the work or completing the first extraction.

The hard requirements concern behavior, ownership, dependencies, state safety, and testable invariants. Suggested service names, file names, directory layouts, and line counts are design guidance rather than substitutes for those requirements.

---

# 1. Scope

This is an architecture, correctness, safety, and maintainability refactor.

Do not add:

* voice recognition;
* wake-word support;
* text-to-speech;
* personal memory;
* email or calendar integration;
* filesystem automation;
* screenshot-based computer use;
* unrestricted keyboard or pointer control;
* arbitrary shell execution;
* raw D-Bus execution;
* new desktop capabilities;
* new model providers unrelated to the refactor;
* a workflow framework;
* a custom agent DSL.

Do not replace VibeOS with LangGraph, AutoGen, Temporal, or another orchestration framework.

Preserve all current supported capabilities and public command semantics unless a change is required to close a safety issue.

---

# 2. Current State to Verify

Inspect the current repository before editing.

The expected current state is approximately:

* `broker.py` remains several thousand lines;
* `CapabilityBroker.handle()` delegates to `CommandService`;
* `CommandService` still receives a callable-based port bundle;
* `GoalLoop` uses named ports, but some core signatures still use `Any`;
* implementations of GoalLoop ports mainly forward back into `CapabilityBroker`;
* domain tool modules exist under `src/vibeos/tools/`;
* fresh tasks run through `GoalLoop`;
* fresh-task compatibility payloads are projected without mutating the shared session;
* historical `review_kind=plan` records may still use `AgentRuntime.continue_goal()`;
* a shared `broker_session` still exists for that historical path;
* broker-owned execution code and domain tool execution code may coexist;
* planning restoration, review resume, acceptance, result projection, legacy compatibility, and many utilities remain in `broker.py`;
* `ReviewStore` uses SQLite current-state rows but still contains JSONL mutation fallback;
* `ReviewStore.consume()` allows broad state transitions;
* retry and repair behavior may retain failed receipts in the collection later passed to final acceptance;
* CI exists but remote success may not yet be observed.

Verify each statement through code search and tests.

Create a caller inventory for at least:

```text
CapabilityBroker.handle
CapabilityBroker._execute
CapabilityBroker.execute_task_step
CapabilityBroker.assess_task_plan_execution
CapabilityBroker.approve_review
CapabilityBroker.provide_review_input
CapabilityBroker._approve_plan_review_v06
CapabilityBroker._approve_plan_review_legacy
CapabilityBroker._compatibility_runtime_result
CapabilityBroker._task_plan_to_v06_strategy
CapabilityBroker._build_v06_tool_registry
AgentRuntime.continue_goal
agent_session
CommandPorts
BrokerPlanningPort
BrokerExecutionPort
BrokerReviewPort
BrokerAcceptancePort
BrokerObservationPort
BrokerRecoveryPort
ReviewStore.consume
ReviewStore.claim_execution
```

For each item, record:

* production callers;
* test-only callers;
* compatibility callers;
* state mutated;
* adapters reachable;
* whether it should be removed, migrated, isolated, or retained.

Do not classify code as dead based only on its name.

---

# 3. Hard Architecture Invariants

These are mandatory completion conditions.

## 3.1 One production orchestration path

Every new supported task must use:

```text
CommandService
→ task application boundary
→ GoalLoop
```

Every resumable current-format task must resume through the same `GoalLoop`.

No fresh task or current-format review resume may use a second orchestration loop.

## 3.2 One production capability execution path

Every real capability action must use:

```text
GoalLoop
→ typed execution boundary
→ ToolRegistry or equivalent registered execution registry
→ domain-owned tool implementation
→ existing adapter
```

There must not be one adapter invocation implementation for GoalLoop and another for an old runtime.

For each executable capability, exactly one production implementation may invoke the underlying adapter.

## 3.3 No reverse dependency on Broker

The following areas must not import, hold, or call `CapabilityBroker`:

* GoalLoop;
* planning implementation;
* execution implementation;
* review implementation;
* review resume implementation;
* observation implementation;
* acceptance implementation;
* recovery implementation;
* result projection;
* persistence;
* domain tools;
* compatibility conversion code.

`CapabilityBroker` may depend on these components. They must not depend on it.

## 3.4 Historical approvals are migrated or rejected safely

Historical approvals may continue only when their original approval binding can be verified.

A legacy record must not be reused unless the system can validate the relevant binding, including:

* stored plan identity;
* deterministic plan hash or equivalent immutable plan binding;
* pending step identity;
* reviewed action;
* reviewed target and arguments;
* safety-review identity or sufficient equivalent evidence;
* review kind;
* current capability registration;
* current policy compatibility;
* expiration;
* absence of plan mutation.

When this information is missing, inconsistent, unsupported, or unverifiable:

```text
do not execute
→ return a structured legacy_review_unverifiable result
→ mark or supersede the record as non-executable where appropriate
→ require a fresh command and fresh review
```

Do not infer missing approval scope from the original natural-language utterance.

## 3.5 Review persistence fails closed

SQLite current-state data is authoritative.

When SQLite cannot atomically mutate review state:

* approval must not proceed;
* execution claim must fail;
* no adapter may be called;
* no side effect may occur;
* the command must return a structured persistence failure.

JSONL must never become an alternative authoritative mutation store during a database failure.

## 3.6 Attempt history and accepted receipts are separate

All attempts must remain auditable.

Final acceptance must receive only the currently accepted and verified result for each completed required step.

A failed historical attempt must not cause a later successful retry to fail final acceptance merely because both receipts are stored in history.

---

# 4. Target Responsibility Model

The final ownership model should be equivalent to:

```text
CLI / HTTP / D-Bus
        |
        v
CommandService
  - command classification
  - trace lifecycle
  - transport-neutral dispatch
  - final result handoff
        |
        v
Task Application Boundary
  - start fresh task
  - approve stored review
  - provide supplemental input
  - resume stored loop
        |
        v
GoalLoop
  - only production task state machine
  - observe
  - review
  - execute
  - verify
  - retry
  - repair
  - replan
  - suspend
  - resume
  - finish
        |
        +--> planning boundary
        +--> observation boundary
        +--> review boundary
        +--> execution boundary
        +--> acceptance boundary
        +--> recovery boundary
        |
        v
Registered Domain Tools
        |
        v
Existing Adapters
```

Additional responsibilities must be independently owned:

* runtime dependency composition;
* planning snapshot encoding and decoding;
* review persistence and transitions;
* review resume;
* public result projection;
* legacy payload conversion;
* audit metadata projection.

The implementation does not have to use the exact class names in this document.

However, each responsibility must have a clear owner that:

* can be tested independently;
* does not depend on the entire Broker;
* does not mix unrelated responsibilities;
* does not exist only as a one-line forwarding shell.

---

# 5. Broker Completion Criteria

`CapabilityBroker` must become a facade and backward-compatible construction boundary.

It may own:

* compatibility constructor arguments;
* access to assembled runtime components;
* `handle()` delegation;
* `capabilities()`;
* `pending_reviews()`;
* narrowly justified compatibility entry points.

It must not own:

* planning logic;
* understanding transitions;
* planning snapshot encoding;
* planning snapshot restoration;
* GoalLoop state transitions;
* step execution;
* adapter invocation;
* acceptance logic;
* review state transitions;
* review resume logic;
* historical approval execution;
* compatibility strategy generation;
* public payload projection;
* domain-specific diagnostics;
* domain capability mappings;
* tool handler bodies.

`broker.py` below 400 lines is a desirable target.

It is not an absolute completion condition.

The reliable hard condition is that none of the prohibited responsibilities remain in Broker. A Broker above 800 lines requires a written explanation in the final handoff and an explicit review of why the remaining code belongs there.

Do not reduce Broker size by creating another giant coordinator.

---

# 6. Runtime Composition

Create one explicit composition root responsible for constructing concrete dependencies.

It may be a function, factory, dataclass-backed container, or a small set of cohesive factories.

It must make the object graph visible and testable.

A possible shape is:

```python
@dataclass(frozen=True)
class RuntimeComponents:
    command_service: CommandService
    task_handler: TaskCommandHandler
    goal_loop: GoalLoop
    planning: PlanningPort
    observation: ObservationPort
    reviews: ReviewPort
    execution: ExecutionPort
    acceptance: AcceptancePort
    recovery: RecoveryPort
    result_projector: CommandResultProjector
    review_store: ReviewStore
    tool_registry: ToolRegistry
```

The exact name is not important.

The following properties are important:

* no global mutable service locator;
* no hidden construction inside GoalLoop;
* no circular dependencies;
* no service receives the whole Broker;
* adapters are injected through narrow dependencies;
* tests can replace individual components.

`CapabilityBroker.__init__()` may use the composition root to preserve its current constructor API.

---

# 7. CommandService Refactor

Replace the current arbitrary `Callable` bundle with named typed dependencies.

Do not merely rename `CommandPorts`.

A suitable boundary may expose operations equivalent to:

```python
class TaskCommandHandler(Protocol):
    def start(
        self,
        request: CommandRequest,
        context: RunContext,
    ) -> CommandResult: ...

    def approve(
        self,
        review_id: str,
        request: CommandRequest,
        context: RunContext,
    ) -> CommandResult: ...

    def provide_input(
        self,
        review_id: str,
        supplemental_input: str,
        request: CommandRequest,
        context: RunContext,
    ) -> CommandResult: ...
```

CommandService remains responsible for explicit routing:

```text
review id + supplemental input + approval
→ reject invalid combination

review id + supplemental input
→ provide input

review id
→ approval/resume path

approval without review id
→ reject

otherwise
→ fresh task
```

CommandService must not know how planning, execution, review migration, or adapter invocation works.

---

# 8. Unify Capability Execution

Create one registered execution path.

The execution boundary must:

1. accept a validated `TaskPlan` and `TaskStep`;
2. require a run-scoped context;
3. locate a host-registered capability recipe;
4. invoke registered domain tools;
5. normalize tool results;
6. produce a typed `StepExecutionResult`;
7. record bounded audit and trace metadata;
8. never invoke an unregistered action.

Example responsibility:

```python
class StepExecutionService(ExecutionPort):
    def execute_step(
        self,
        *,
        context: RunContext,
        plan: TaskPlan,
        step: TaskStep,
        request: CommandRequest,
        attempt_id: str,
    ) -> StepExecutionResult:
        ...
```

The production execution path must not depend on `StrategyCandidate`.

A host-defined recipe may contain multiple internal tools, such as:

```text
app.open
→ apps.resolve_installed
→ app.open
```

or:

```text
window.close
→ window.resolve
→ window.close
```

Recipes must be:

* host-defined;
* registered;
* typed;
* bounded;
* associated with known capability IDs;
* subject to the prior step-level review.

Remove broker-owned `_execute` after all production callers have migrated.

Add tests proving each adapter is reachable through only one production implementation.

---

# 9. Remove AgentRuntime from Production

No production command path may call:

```python
AgentRuntime.continue_goal(...)
```

No production path may depend on a shared:

```python
broker_session
agent_session
```

Historical plan reviews must be converted into current structures and handled through the current GoalLoop, or rejected safely.

A migration path may:

1. decode the historical stored plan;
2. validate its schema;
3. validate approval binding;
4. construct a current loop snapshot;
5. identify the exact pending step;
6. resume through GoalLoop;
7. project current results into required compatibility fields.

Do not use the original utterance to regenerate a different plan.

After migration:

* the old runtime may remain only for data-model compatibility or isolated historical tests;
* production construction must not instantiate it;
* production code must not call its loop;
* shared session state must be removed.

Add an architecture test proving there are no production callers of `continue_goal`.

---

# 10. Compatibility Boundary

Move compatibility logic into isolated modules or components.

Compatibility responsibilities may include:

* reading older payload formats;
* validating historical data;
* migrating historical reviews;
* projecting current typed outcomes into legacy public JSON fields;
* preserving existing output keys.

Compatibility logic must not:

* execute adapters;
* own task retries;
* own recovery;
* make permission decisions;
* invoke the old runtime loop;
* mutate active sessions;
* depend on Broker.

Compatibility projection must be deterministic and side-effect-free.

A suitable interface may be:

```python
class LegacyPayloadProjector(Protocol):
    def project(
        self,
        *,
        context: RunContext,
        planning: PlanningArtifacts,
        loop_result: GoalLoopResult,
    ) -> LegacyRuntimeProjection:
        ...
```

The exact class name and file layout are optional. The pure projection responsibility is mandatory.

---

# 11. Planning Ownership and Snapshot Versioning

Move planning implementation out of Broker.

The planning owner must handle:

* fresh planning;
* understanding transitions;
* refinement;
* supersession;
* replanning;
* route and capability exclusions;
* candidate-domain changes;
* planning trace artifacts.

Create an independently testable snapshot codec or equivalent responsibility.

It must support:

```python
encode(planning: PlanningArtifacts) -> Mapping[str, object]
decode(
    *,
    utterance: str,
    payload: Mapping[str, object],
) -> PlanningArtifacts
```

Stored planning and loop snapshots must carry explicit schema versions.

At minimum:

```text
planning_snapshot_schema_version
loop_snapshot_schema_version
```

Decoding must validate:

* required identifiers;
* plan schema;
* step definitions;
* route definitions;
* review binding;
* compatible version;
* expected types.

Unsupported or malformed snapshots must produce a structured failure, not an uncontrolled exception and not a guessed reconstruction.

Dynamic external payload handling may use:

```python
Mapping[str, object]
JsonValue
validated parsing helpers
```

The requirement is not “zero dynamic values everywhere.” The requirement is that dynamic values are validated before entering the typed core.

---

# 12. Review Responsibilities

Separate these responsibilities from Broker:

## 12.1 Review policy

Own:

* permission policy invocation;
* contextual escalation;
* step safety review records;
* stable safety-review IDs;
* observation fingerprinting;
* re-review after context change.

## 12.2 Review persistence

Own:

* durable review rows;
* event history;
* migrations;
* atomic state transitions;
* expiration;
* query operations.

## 12.3 Review command handling

Own:

* approval;
* rejection;
* claim;
* release;
* completion;
* supplemental input;
* duplicate submission behavior.

## 12.4 Review resume

Own:

* loading the stored record;
* checking the review kind;
* validating its binding;
* decoding planning and loop snapshots;
* invoking GoalLoop resume;
* consuming, releasing, superseding, or replacing the review;
* safe failure for unsupported legacy records.

These may be implemented as two or three cohesive components rather than four mandatory classes. The responsibilities must not remain mixed in Broker.

---

# 13. ReviewStore Authority and Failure Semantics

SQLite is the only authoritative review mutation store.

Do not silently fall back to JSONL for:

* create;
* approve;
* reject;
* claim;
* release;
* complete;
* provide input;
* consume input;
* expire;
* supersede.

When SQLite is unavailable, return a typed result or raise a controlled domain exception that the command layer converts into:

```text
status: failed
overall_status: blocked or failed
error_code: review_persistence_unavailable
```

No side effect may be dispatched.

JSONL may remain for:

* one-time migration input;
* explicit diagnostic export;
* optional audit backup that does not affect current state.

Use database configuration appropriate for daemon concurrency:

* WAL;
* `busy_timeout`;
* explicit transactions;
* rollback on failure;
* compare-and-swap updates;
* deterministic connection lifecycle.

Prefer structured event columns:

```sql
CREATE TABLE review_events (
    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
    review_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    created_at TEXT NOT NULL,
    payload TEXT NOT NULL
);
```

Add indexes appropriate to:

* pending review listing;
* review lookup;
* review event history.

Add a transactional and idempotent schema migration mechanism.

---

# 14. Strict Review State Machine

Replace broad public transitions with explicit domain methods.

## Execution review

```text
pending
→ approved
→ executing
→ consumed
```

Execution failure:

```text
executing
→ approved
```

Rejection:

```text
pending
→ rejected
```

Expiration:

```text
pending
→ expired

approved
→ expired
```

Context or policy invalidation:

```text
approved
→ superseded

executing
→ superseded
```

## User-input review

```text
pending
→ provided
→ consumed
```

Failed resume that remains retryable must use an explicitly defined transition rather than a generic consume call.

Provide operations equivalent to:

```python
approve(review_id)
reject(review_id)
claim_execution(review_id, expected_binding)
complete_execution(review_id)
release_execution(review_id)
supersede(review_id, reason)
provide_input(review_id, supplemental_input)
consume_input(review_id)
expire(review_id)
```

Hard rules:

* `complete_execution()` accepts only `executing`;
* `release_execution()` accepts only `executing`;
* `consume_input()` accepts only `provided`;
* execution approval cannot bypass claim;
* claim atomically checks expiration;
* claim atomically checks expected review kind;
* claim atomically checks stored binding where possible;
* duplicate claims fail;
* consumed and superseded records are never reusable.

A generic internal transition helper may exist inside persistence. It must not expose unsafe broad domain semantics.

---

# 15. Retry, Repair, Replan, and Receipt Correctness

Represent separately:

1. complete attempt history;
2. accepted results for completed steps;
3. current pending step;
4. verification evidence;
5. replan equivalence mapping.

A suitable model may be:

```python
@dataclass(frozen=True)
class StepAttemptHistory:
    step_id: str
    attempts: tuple[StepExecutionResult, ...]
    accepted_result: StepExecutionResult | None
```

or:

```python
attempt_history: tuple[PlanAttempt, ...]
accepted_step_results: Mapping[str, StepExecutionResult]
```

The exact data structure is optional.

The following semantics are mandatory:

* every attempt remains visible in audit and trace history;
* failed attempts are retained;
* only verified accepted results are passed to final acceptance;
* final acceptance receives at most one accepted receipt per required step;
* retry of the current step does not replay completed predecessors;
* repair of the current step does not replay completed predecessors;
* review resume does not replay completed predecessors;
* user-input resume does not replay completed predecessors unless planning explicitly invalidates them;
* replan completion retention requires an explicit equivalence decision;
* matching step IDs alone are insufficient for replan retention;
* a failed first attempt followed by a successful retry can complete successfully;
* a later successful receipt must not erase the failed attempt from history.

Add a regression test for:

```text
first attempt fails
→ recovery chooses retry_same_attempt
→ second attempt succeeds
→ final acceptance receives only the accepted successful result
→ attempt history contains both attempts
→ final overall status is completed
```

---

# 16. Core Typing Policy

Do not require all historical and external-boundary code to contain zero `Any`.

Apply strict typing to:

* new core architecture modules;
* GoalLoop public interfaces;
* command routing interfaces;
* planning ports;
* review ports;
* execution ports;
* acceptance ports;
* recovery ports;
* run context;
* internal typed outcomes;
* review transition results;
* compatibility projection inputs and outputs.

Replace core signatures such as:

```python
planning: Any
attempts: tuple[Any, ...]
post_observation: Any
```

with domain types such as:

```python
planning: PlanningArtifacts
attempts: tuple[PlanAttempt, ...]
post_observation: LoopObservation
```

At external boundaries, controlled dynamic types are acceptable when followed by validation.

Prefer:

```python
Mapping[str, object]
Sequence[object]
JsonValue
TypedDict
validated dataclass constructors
```

Do not fake type safety through widespread unchecked casts.

Do not solve errors through broad `# type: ignore` or global `ignore_errors`.

---

# 17. Result and Audit Projection

Move public result construction and nested metadata extraction out of Broker.

Prefer an internal typed outcome such as:

```python
@dataclass(frozen=True)
class TaskCommandOutcome:
    context: RunContext
    planning: PlanningArtifacts
    loop_result: GoalLoopResult
    selected_target: str | None
    command_status: str
    execution_status: str
    acceptance_status: str
    overall_status: str
```

A projection boundary should construct:

* `CommandResult`;
* run payload;
* attempt payloads;
* compatibility fields;
* trace metadata;
* audit metadata.

Broker must not manually search nested dictionaries for:

* goal ID;
* plan ID;
* understanding ID;
* candidate-set ID;
* route-decision ID;
* semantic-acceptance ID;
* loop-snapshot ID;
* selected strategy ID.

Public fields must remain compatible unless an explicit safety correction is documented and tested.

---

# 18. Architecture Enforcement Tests

Add durable architecture tests.

The tests must verify responsibilities and dependencies rather than exact file names.

Required assertions:

1. production code does not call `AgentRuntime.continue_goal`;
2. production code does not create `broker_session`;
3. GoalLoop does not import Broker;
4. core services do not import Broker;
5. tools do not import Broker;
6. persistence does not import Broker;
7. compatibility projection does not import Broker;
8. compatibility code cannot invoke adapters;
9. Broker does not define `_execute`;
10. Broker does not contain adapter invocation implementations;
11. fresh tasks execute through the registered execution boundary;
12. review resume executes through GoalLoop;
13. CommandService does not depend on a generic callable bundle;
14. core GoalLoop interfaces contain no public `Any`;
15. SQLite mutation failure prevents execution;
16. production contains only one adapter invocation implementation per capability;
17. no orchestration path bypasses GoalLoop for current supported tasks;
18. historical unverifiable review records fail closed;
19. Broker contains none of the prohibited responsibilities;
20. modules above the agreed complexity threshold are explicitly reviewed.

Use AST, import graph checks, dependency tests, or similarly stable mechanisms.

Avoid tests tied to whitespace or method ordering.

---

# 19. Required Behavioral Tests

Preserve and extend tests for:

## Fresh requests

* L0;
* L1;
* L2;
* L3;
* offline dry-run;
* provider failure;
* clarification;
* blocked planning;
* multi-step execution;
* browser verification;
* app execution;
* window execution;
* notification execution;
* clipboard execution.

## Review lifecycle

* creation;
* approval;
* rejection;
* pending expiration;
* approved expiration;
* atomic claim;
* concurrent claim;
* duplicate approval;
* successful completion;
* failed execution release;
* retry after release;
* supersession after context change;
* supplemental input;
* duplicate supplemental input;
* SQLite mutation failure;
* no adapter invocation when claim fails.

## Resume behavior

* resume from the pending step;
* completed predecessors not replayed;
* changed observation forces re-review;
* invalid snapshot fails safely;
* unsupported snapshot version fails safely;
* historical valid approval migration;
* historical unverifiable approval rejection.

## GoalLoop behavior

* observe-review-act-observe-verify ordering;
* one-step-at-a-time execution;
* budget exhaustion;
* retry;
* repair;
* replan;
* ask user;
* stop;
* accepted-receipt isolation;
* attempt-history preservation;
* explicit semantic retention on replan.

## Public compatibility

* CLI semantic fields;
* HTTP semantic fields;
* D-Bus semantic fields;
* compatibility payload fixture;
* dry-run does not consume approval;
* compatibility projection is deterministic.

---

# 20. Static Checks

Introduce Ruff when it is not already configured.

Run:

```bash
python -m ruff check src tests
python -m ruff format --check src tests
```

Enable type checking in layers.

## Initial strict scope

Strictly check all newly created or significantly rewritten core modules, including the equivalents of:

```text
command service
task application boundary
goal loop
goal ports
planning implementation
planning snapshot codec
execution implementation
review implementation
review resume
acceptance implementation
recovery implementation
run context
result projection
compatibility conversion
tool modules
composition root
review persistence
```

Do not initially require unrelated untouched historical modules to pass strict typing merely to satisfy this refactor.

After the core scope passes, expand type checking where practical.

Document:

* strict modules;
* temporarily excluded legacy modules;
* specific remaining type debt;
* plan for later expansion.

The refactor must not mix an unlimited repository-wide typing cleanup into the critical architecture changes.

---

# 21. CI

Update CI to run:

```text
Ruff lint
Ruff format check
scoped strict type checking
pytest
capability smoke test
offline dry-run smoke test
```

Use Python 3.11 and optionally the latest supported Python version.

CI must not require:

* live model credentials;
* a GNOME session;
* Wayland;
* desktop D-Bus;
* a real browser;
* a real clipboard;
* external network access.

Keep GNOME integration checks separate.

Do not claim remote CI success unless an actual workflow run has been observed.

---

# 22. Documentation

Update the relevant architecture and status documents.

They must accurately state:

* GoalLoop is the single production task state machine;
* fresh tasks and current review resumes use GoalLoop;
* all capability execution uses one registered path;
* Broker is a facade;
* no core service depends on Broker;
* no shared production task session exists;
* AgentRuntime is not used as a production loop;
* historical approvals are verifiably migrated or rejected;
* SQLite is authoritative;
* persistence mutation failures block side effects;
* review transitions are explicit;
* attempt history and accepted receipts are separate;
* compatibility payloads are projections;
* static architecture tests exist;
* strict typing currently covers the defined core scope;
* real GNOME checks remain separate.

Remove stale claims contradicted by the code.

---

# 23. Mandatory Phase Plan

Use one master contract but implement through these checkpoints.

Do not start the next phase until the current phase’s targeted tests pass.

## Phase A — Characterization and safety baseline

Tasks:

* run existing tests;
* create the caller and state inventory;
* identify duplicate execution implementations;
* add retry-receipt regression coverage;
* add persistence-failure coverage;
* add legacy approval binding tests;
* add initial architecture tests that document current violations.

Checkpoint report:

* baseline commands;
* pass/fail counts;
* current dependency graph;
* current production paths;
* identified risks.

## Phase B — Execution unification

Tasks:

* introduce the single execution boundary;
* execute fresh GoalLoop steps through registered domain tools;
* remove duplicate adapter invocation implementations;
* remove broker-owned `_execute` when callers reach zero;
* prove one implementation per executable capability.

Checkpoint:

```bash
python -m pytest tests/test_tool_modules.py -q
python -m pytest tests/test_goal_loop.py -q
python -m pytest tests/test_broker.py -q
```

Also run the architecture tests related to execution.

## Phase C — Review state and fail-closed persistence

Tasks:

* replace broad consumption semantics;
* add strict transition methods;
* enforce approved expiration;
* atomically verify claim conditions;
* remove authoritative JSONL mutation fallback;
* add schema migrations and indexes;
* ensure database errors prevent all side effects.

Checkpoint:

```bash
python -m pytest tests/test_reviews.py -q
python -m pytest tests/test_broker.py -q
```

## Phase D — Planning and resume extraction

Tasks:

* move planning transitions out of Broker;
* create versioned planning and loop snapshot handling;
* move review resume out of Broker;
* add strict decoding;
* handle malformed snapshots safely.

Checkpoint:

```bash
python -m pytest tests/test_goal_loop.py -q
python -m pytest tests/test_broker.py -q
```

## Phase E — Historical review migration and old runtime removal

Tasks:

* define verifiable historical approval criteria;
* migrate safe records into current GoalLoop structures;
* reject unsafe records;
* remove production calls to `AgentRuntime.continue_goal`;
* remove shared session state;
* isolate remaining data-only compatibility code.

Checkpoint:

* architecture test shows zero production calls to `continue_goal`;
* valid historical migration test passes;
* unverifiable historical record test fails closed;
* full approval tests pass.

## Phase F — Broker and dependency cleanup

Tasks:

* move acceptance;
* move result projection;
* move audit metadata projection;
* move compatibility projection;
* remove forwarding adapters that only call Broker;
* make services implement GoalLoop ports directly;
* reduce Broker to facade responsibilities.

Checkpoint:

* no core module imports Broker;
* Broker contains no prohibited responsibility;
* dependency tests pass;
* Broker size is reported and reviewed.

## Phase G — Typing, static checks, CI, and documentation

Tasks:

* remove public core `Any`;
* enable Ruff;
* enable scoped strict typing;
* update CI;
* update documentation;
* run full deterministic verification.

Checkpoint:

```bash
python -m ruff check src tests
python -m ruff format --check src tests
python -m mypy <documented strict core scope>
python -m pytest -q
vibe capabilities --json
vibe ask "search web for hello" --json --offline --dry-run
```

Do not leave both old and new production implementations enabled at completion.

---

# 24. Completion Criteria

The refactor is complete only when all conditions below are true.

1. Fresh supported tasks use GoalLoop.
2. Current-format review resumes use GoalLoop.
3. Production has one capability execution path.
4. Every adapter is invoked by one production capability implementation.
5. Broker does not implement planning.
6. Broker does not implement execution.
7. Broker does not invoke adapters.
8. Broker does not implement acceptance.
9. Broker does not implement review resume.
10. Broker does not implement compatibility projection.
11. Core services do not depend on Broker.
12. Domain tools do not depend on Broker.
13. Persistence does not depend on Broker.
14. CommandService does not use a generic callable bundle.
15. Port implementations are real services rather than Broker forwarders.
16. No production code calls `AgentRuntime.continue_goal`.
17. No shared production agent session exists.
18. Safe historical reviews migrate to current GoalLoop structures.
19. Unverifiable historical reviews fail closed.
20. Historical approval execution never reparses the original utterance.
21. SQLite is authoritative.
22. SQLite mutation failure blocks side effects.
23. JSONL is not an authoritative mutation fallback.
24. Execution completion requires `executing`.
25. User-input consumption requires `provided`.
26. Duplicate approval cannot execute twice.
27. Concurrent approval cannot execute twice.
28. Failed execution can be retried only after explicit release.
29. Approved review expiration is defined and tested.
30. Failed attempts remain in history.
31. Failed attempts do not pollute final accepted receipts.
32. Completed predecessor steps are not replayed on resume.
33. Completed predecessor steps are not replayed on retry or repair.
34. Replan retention requires explicit semantic equivalence.
35. Core public interfaces do not expose uncontrolled `Any`.
36. Dynamic compatibility payloads are validated at their boundary.
37. Public command semantics remain compatible.
38. Safety policy remains intact.
39. Trace privacy remains intact.
40. Architecture enforcement tests pass.
41. Ruff passes.
42. Scoped strict type checking passes.
43. The full deterministic test suite passes.
44. Smoke tests pass.
45. CI contains the required jobs.
46. Documentation matches the implementation.
47. No new user-facing capability is added.
48. Complexity has not merely moved into another giant component.

Suggested class names and file names are not completion conditions. The responsibilities and dependency boundaries are.

---

# 25. Required Final Handoff

Provide a final report containing the following.

## Phase results

For each phase:

* changes made;
* files affected;
* tests run;
* test results;
* temporary compatibility retained;
* unresolved issue, if any.

## Final architecture

Show the final dependency graph.

For each major component, state:

* responsibility;
* dependencies;
* state owned;
* adapters reachable.

## Broker audit

Report:

* original line count;
* final line count;
* remaining methods;
* why each remaining responsibility belongs in Broker;
* explanation for Broker above 800 lines, if applicable.

## Execution audit

List every executable capability and the single implementation that invokes its adapter.

Confirm no duplicate execution path remains.

## Removed legacy code

List:

* deleted Broker methods;
* removed forwarding adapters;
* removed old runtime callers;
* removed shared session state;
* removed duplicate implementations;
* retained compatibility data types and why they remain.

## Historical review policy

Document:

* required approval binding fields;
* safe migration rules;
* rejection rules;
* structured error returned for unverifiable records.

## Review state machine

Document all valid transitions and their SQL compare-and-swap conditions.

## Retry correctness

Document:

* attempt history storage;
* accepted result selection;
* final acceptance inputs;
* non-replay guarantees;
* replan equivalence behavior.

## Typing

Document:

* strict type-checking scope;
* excluded legacy modules;
* remaining type debt;
* why exclusions do not weaken core architecture safety.

## Verification

Report exact commands and real results for:

```bash
python -m ruff check src tests
python -m ruff format --check src tests
python -m mypy <strict core scope>
python -m pytest -q
vibe capabilities --json
vibe ask "search web for hello" --json --offline --dry-run
```

Do not claim a command passed unless it was actually executed.

## CI

Report:

* workflow path;
* configured jobs;
* observed remote status;
* or explicitly state that remote status was not observed.

## Deferred GNOME verification

List checks that still require a real GNOME Wayland VM:

* systemd user daemon;
* GNOME Shell extension;
* D-Bus window control;
* real app opening;
* real clipboard;
* real notifications;
* real portal navigation;
* real browser observation.

Do not declare the master goal complete while any hard completion criterion remains unmet.
