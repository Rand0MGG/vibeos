# VibeOS Capability Registry

The executable capability source of truth is
[`src/vibeos/capabilities.py`](../../src/vibeos/capabilities.py). Query the
running surface with:

```bash
vibe capabilities --json
```

The registry defines action name, risk level, approval requirement, effects,
reversibility, and target constraints. A validated task step reaches one
registered execution route only:

```text
GoalLoop -> StepExecutionService -> CapabilityRecipeRegistry
         -> ToolRegistry -> domain tool -> existing adapter
```

`CapabilityBroker` does not implement capability execution.

## Current registered actions

| Risk | Actions |
| --- | --- |
| L0 — observe automatically | `app.list`, `system.status`, `window.list` |
| L1 — bounded action with audit | `app.open`, `app.search_history`, `browser.open_named_target`, `browser.open_site_search`, `browser.open_url`, `browser.search_web`, `media.pause`, `media.play`, `media.search`, `notification.send`, `window.focus`, `window.maximize`, `window.minimize` |
| L2 — stored review then approval | `clipboard.write`, `portal.open_uri`, `window.close` |
| L3 — rejected | unsupported or malformed requests (`unknown` is not executable) |

Media actions remain registered so planning and review stay typed, but return a
bounded unavailable result on hosts that do not provide a dedicated media
adapter. They never fall back to arbitrary shell or UI control.

## Adding or changing a capability

1. Change the canonical registry and target validation.
2. Add a host-owned recipe in `src/vibeos/tools/registry.py`.
3. Implement one registered domain-tool route and its tests.
4. Verify the permission level, review behavior, trace/audit output, and
   postcondition acceptance where applicable.
5. Update this page and run `vibe capabilities --json`.

Do not add an adapter call to Broker or create a second execution path for an
existing action.
