# Architecture Completion - Phase F Broker and Dependency Cleanup

Captured: 2026-07-12

This checkpoint records the Phase F work required by
[`architecture_completion_master_goal.md`](architecture_completion_master_goal.md).
The master contract itself remains unchanged.

## Composition and command boundary

`runtime_composition.py` is now the explicit composition root. Its immutable
`RuntimeComponents` object exposes the command service, typed task application
boundary, GoalLoop services, result projector, review store, audit log, and
trace store. No service receives `CapabilityBroker`.

`CommandService` now depends on two named protocols:

```text
TaskCommandHandler
  start / approve / provide_input / reject

CommandResultRecorder
  record / metadata
```

The former callable `CommandPorts` bundle is removed. `RunContext` is created
at command ingress and passed through the typed task boundary.

## Direct GoalLoop service ownership

`TaskApplicationService` is the sole task application boundary for fresh
starts, approval, supplemental input, stored review resume, and the retained
direct-plan compatibility entries. It builds GoalLoop with real services:

```text
PlanningService
ObservationService
ReviewService
StepExecutionService
AcceptanceService
RecoveryService
```

The old `goal_loop_adapters.py` forwarding layer has been removed. In
particular, no GoalLoop port retains or calls Broker.

`ReviewService` owns policy review, safety-record identity, plan-review
creation, and loop/user-input suspension persistence. `AcceptanceService` owns
postcondition collection, verifier execution, semantic acceptance, and the
accepted plan result. `RecoveryService` owns classifier/replanner delegation.

## Public and audit projections

`CommandResultProjector` now owns GoalLoop-to-public-result conversion,
including legacy-shaped read-only runtime payloads and attempt summaries.
`AuditResultRecorder` owns audit metadata extraction and final audit writes.
The compatibility payload is a projection of GoalLoop records; it does not
instantiate or advance `AgentRuntime`.

## Broker audit

`broker.py` changed from **3,316** lines at the checked-out baseline to
**193** lines. The remaining facade lines are explicit typed signatures for the
retained direct-plan compatibility entries and read-only references to composed
components used by existing HTTP, D-Bus, and local-runtime inspection APIs;
they are not hidden callback or projection logic. Its remaining responsibilities
are limited to:

* preserving the constructor API while invoking the composition root;
* `handle()` delegation;
* capability and pending-review queries;
* narrow direct compatibility entry points that delegate to owned services;
* approval, supplemental-input, and rejection ingress delegation.

It has no planning, execution, adapter, acceptance, review-resume, historical
approval, result-projection, strategy-generation, or tool-registry logic.

## Verification

```text
python -m pytest tests/test_broker.py tests/test_goal_loop.py \
  tests/test_reviews.py tests/test_task_plan_boundaries.py \
  tests/test_architecture.py -q
-> 93 passed
```

Architecture checks now enforce that core services do not import Broker, that
Broker defines no prohibited implementation methods, and that no
`CommandPorts` class remains.
