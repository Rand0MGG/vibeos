# VibeOS capability registry

The executable source of truth is
[`src/vibeos/capabilities.py`](../../src/vibeos/capabilities.py). Query it with:

```bash
vibe capabilities --json
```

```text
DurableTaskEngine -> DurableActionExecutor -> StepExecutionService
  -> CapabilityRecipeRegistry -> ToolRegistry -> adapter
```

`CapabilityBroker` does not implement capability execution.

| Risk | Actions |
| --- | --- |
| L0 — observation | `app.list`, `system.status`, `window.list` |
| L1 — bounded audited action | `app.open`, `app.search_history`, `browser.open_named_target`, `browser.open_site_search`, `browser.open_url`, `browser.search_web`, `media.pause`, `media.play`, `media.search`, `notification.send`, `window.focus`, `window.maximize`, `window.minimize` |
| L2 — stored review | `clipboard.write`, `portal.open_uri`, `window.close` |
| L3 — reject | unsupported or malformed requests; `unknown` is not executable |

Media search and pause enter durable clarification when no dedicated adapter is
available. Media play may use its declared browser fallback, but completion
remains incomplete until independent browser observation exists. No media path
falls back to arbitrary shell or unregistered UI control.

Every capability has a Goal 03 table-driven contract for normalized target,
risk, dry-run, real or unavailable outcome, error boundary, receipt/evidence,
and public projection. See
[`goal03_replacement_compatibility_matrix.md`](goal03_replacement_compatibility_matrix.md).

To add or change a capability, update the canonical registry and validation,
add one host-owned recipe and registered tool route, prove review/receipt/
evidence/acceptance behavior, and update this document. Do not add adapter calls
to Broker or a second execution path.
