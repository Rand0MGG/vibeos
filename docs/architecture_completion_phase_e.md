# Architecture Completion - Phase E Historical Approval Migration

Captured: 2026-07-12

This checkpoint records the Phase E work required by
[`architecture_completion_master_goal.md`](architecture_completion_master_goal.md).
The master contract itself remains unchanged.

## Historical approval rule

Only a `review_kind=plan` record created with the immutable
`legacy_review_binding` is eligible for migration. The binding contains the
review kind and format version, plan ID, deterministic hash of the complete
stored plan, the single pending step ID, action, canonical target hash, safety
review ID, approval decision, and capability registration requirement.

At resume time `LegacyPlanReviewMigrator` verifies all of those fields against
the stored task plan and review result. It also verifies that the capability is
still registered and that the stored reviewed action, risk, review requirement,
approval, and reason still agree. It never recreates a plan from the historical
utterance.

The migration deliberately supports exactly one pending historical step. A
multi-step, missing, altered, unknown, expired, or policy-incompatible record
has insufficient approval scope and is rejected rather than guessed.

## Execution and rejection behavior

Verified records are converted to versioned `PlanningService` and `LoopState`
payloads, with a typed, validated migrated step-approval binding. They are then
resumed by `GoalLoop` through the normal registered execution service and
domain tool route. A current safety identifier may include fresh observation
evidence; the legacy approval is accepted only when every verified immutable
approval field still matches.

Unverifiable records return:

```text
status: failed
result.error_code: legacy_review_unverifiable
result.fresh_command_required: true
```

They remain non-executable; no adapter dispatch occurs. A fresh command and
fresh review are required.

## Old runtime isolation

`CapabilityBroker` no longer constructs an `AgentRuntime` or owns an
`agent_session`. Historical plan approval no longer calls `continue_goal`,
`start_goal`, or an old runtime advancement path. The only retained imports
from the old runtime module are data-only compatibility types used while the
public result projection remains in Broker; that projection is scheduled for
Phase F extraction.

## Verification

```text
python -m pytest \
  tests/test_broker.py::test_historical_plan_review_dry_run_remains_isolated_from_fresh_goal_loop \
  tests/test_broker.py::test_verified_historical_plan_review_executes_through_goal_loop \
  tests/test_broker.py::test_unverifiable_historical_plan_review_fails_closed \
  tests/test_broker.py::test_default_goal_loop_projection_has_no_broker_session_state \
  tests/test_architecture.py -q
-> 13 passed, 1 xfailed (Phase F gate)

python -m pytest tests/test_broker.py tests/test_goal_loop.py \
  tests/test_reviews.py tests/test_architecture.py -q
-> 85 passed, 1 xfailed (Phase F gate)
```

The only expected xfail is the next planned milestone: replacing
`CommandPorts` with typed task and result boundaries in Phase F.
