# Architecture Completion - Phase B Execution Unification

Captured: 2026-07-11

This checkpoint records the execution-path work required by
[`architecture_completion_master_goal.md`](../../architecture_completion_master_goal.md).
The master contract itself remains unchanged.

## Single execution route

Fresh GoalLoop steps now use the following production route:

```text
GoalLoop -> StepExecutionService -> CapabilityRecipeRegistry
         -> ToolRegistry -> domain-owned tool -> existing adapter
```

`CapabilityBroker._execute` and its direct adapter helpers were removed. The
deleted `goal_loop_adapters.py` forwarding layer no longer retains Broker or
re-enters it. Recipes are host-owned, typed, bounded to registered capability
IDs, and run only after GoalLoop's step review.

Failed and no-progress receipts remain in `PlanAttempt` history. The separate
accepted step-result collection contains only the currently accepted receipt
for each completed step, so a successful retry is not invalidated by an older
failed receipt.

## Verification

The Phase B targeted commands passed in the documented Fedora 44 WSL
environment:

```text
python -m pytest tests/test_tool_modules.py -q -> 3 passed
python -m pytest tests/test_goal_loop.py -q    -> 31 passed
python -m pytest tests/test_broker.py -q       -> 25 passed
```

The final full-suite and static-verification results are recorded in
[`architecture_completion_phase_g.md`](architecture_completion_phase_g.md).
