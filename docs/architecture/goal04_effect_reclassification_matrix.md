# Goal 04 effect and observation migration matrix

This is the approved pre-release replacement matrix used by migration `0006_effect_contract_v2`.
It is not a runtime compatibility alias.

| Capability | Frozen Goal 03 class | Goal 04 class | Disposition |
|---|---:|---:|---|
| `app.list`, `window.list`, `system.status` | L0 | E0 | Observe only |
| `app.open`, `window.focus`, `window.minimize`, `window.maximize` | L1 | E1 | Bounded reversible user-session action |
| `notification.send` | L1 | E1 | Bounded user-session notification |
| `portal.open_uri`, `clipboard.write` | L2 | E1 | Narrow typed target, reversible session state, independent verification required |
| `browser.open_url`, `browser.search_web`, `browser.open_named_target`, `browser.open_site_search` | L1 | E1 | Vetted browser operation; sensitive contextual data escalates to E3 review |
| `media.search`, `media.play`, `media.pause`, `app.search_history` | L1 | E1 | Typed adapter/fixture only; unavailable adapters fail closed |
| `window.close` | L2 | E3 | Possible unsaved-data loss; stored per-action user approval |
| `unknown` | L3 | E4 | Rejected |

E2 exists in the single `EffectPolicy` contract but Goal 04 implements no privileged action. E3 is review-only and E4 is rejected.

Observation depth is a rename, not an effect mapping: L0 becomes O0, L1 becomes O1, and L2 becomes O2. Migration `0006` applies this only to observation fields. If a resumable v1 payload contains an unbound L2/L3 effect that cannot be classified by typed action/capability, it is migrated to E4 and the task is paused with a manual-disposition reason.

Completed v1 tasks and their plans, events, receipts, and evidence remain byte-preserving history. They are accessible only through the explicit read-only v1 decoders and cannot re-enter execution.
