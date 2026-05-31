# VibeOS Capability Registry

VibeOS keeps capability definitions in one source of truth:

```text
src/vibeos/capabilities.py
```

The registry defines:

- action name
- risk level
- whether review is required
- whether the action is allowed
- reason shown during review
- expected effects
- reversibility
- target constraints

Consumers:

- `models.ALLOWED_ACTIONS`
- model system prompt
- `PermissionPolicy`
- `system.status`
- `vibe capabilities`
- `GET /v1/capabilities`
- `org.vibeos.Agent.Capabilities()`
- tests

## Registered v0.1 Capabilities

| Action | Risk | Review | Effect | Target Constraint |
| --- | --- | --- | --- | --- |
| `app.list` | L0 | No | Read available desktop applications. | No target accepted. |
| `window.list` | L0 | No | Read current desktop window metadata. | No target accepted. |
| `system.status` | L0 | No | Read current VibeOS and Linux session integration status. | No target accepted. |
| `app.open` | L1 | No | May launch or focus an application. | Target must name an installed desktop application. |
| `window.focus` | L1 | No | May switch the active window. | Target must name a visible window or use current. |
| `window.minimize` | L1 | No | May hide a window from the current workspace. | Target must name a visible window or use current. |
| `window.maximize` | L1 | No | May resize a window to fill the workspace. | Target must name a visible window or use current. |
| `notification.send` | L1 | No | May display a desktop notification. | Notification title and body are length-limited. |
| `window.close` | L2 | Yes | May close an application window and lose unsaved work. | Target must name a visible window or use current. |
| `portal.open_uri` | L2 | Yes | May open a URI in another application or browser. | Only http and https URI targets are allowed. |
| `clipboard.write` | L2 | Yes | May replace the user's clipboard contents. | Clipboard text must be non-empty and length-limited. |

`unknown` is intentionally not an executable capability. It is a rejected L3 fallback for unclear or unsupported requests.

## Adding A Capability

1. Add the action to `CAPABILITIES`.
2. Implement execution in `CapabilityBroker`.
3. Add parser/model examples if needed.
4. Add tests covering registry, permission review, broker behavior, and audit.
5. Update this document and the Linux VM checklist.
