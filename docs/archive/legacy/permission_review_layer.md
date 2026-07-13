# VibeOS v0.1 Permission Review Layer

VibeOS v0.1 gives the agent more Linux user-session capabilities, but every action must pass through the permission review layer before execution.

The model only returns intent JSON. It cannot call Linux APIs, D-Bus methods, shell commands, or scripts directly.

Capability metadata is centralized in `src/vibeos/capabilities.py`; see `docs/capability_registry.md`.

## Risk Levels

| Level | Policy | Examples |
| --- | --- | --- |
| L0 Observe | Execute automatically | `app.list`, `window.list`, `system.status` |
| L1 Low Risk | Execute automatically and audit | `app.open`, `window.focus`, `window.minimize`, `window.maximize`, `notification.send` |
| L2 Medium Risk | Requires explicit approval | `window.close`, `portal.open_uri`, `clipboard.write` |
| L3 High Risk | Reject | file deletion, app install, message sending, shell execution, credential access |

## Target Constraints

Permission review checks both the requested action and the action target. This prevents a valid capability name from carrying an unsafe payload.

Current v0.1 target rules:

- `portal.open_uri` only accepts `http` and `https` URIs, rejects local files, script/data URIs, missing hosts, and embedded credentials.
- `clipboard.write` requires non-empty text and rejects oversized content or NUL bytes.
- `notification.send` limits title and body length.
- `app.open` requires an application name.
- window actions accept a short window name or `current`.

## Review Flow

1. User enters a natural-language command.
2. The model returns a structured intent.
3. `PermissionPolicy` classifies the intent.
4. `CapabilityBroker` executes L0/L1 automatically.
5. L2 creates a persistent `review_id` and returns `review_required`.
6. The user approves the exact reviewed intent with `vibe approve <review_id>`.
7. A real approval is consumed after one execution attempt.
8. Pending reviews expire after a short TTL.
9. The user may reject the pending review with `vibe reviews reject <review_id>`.
10. L3 is rejected.
11. Every request is written to the audit log with intent, risk, decision, and result.

## CLI Examples

Low-risk action:

```bash
vibe ask "打开浏览器" --json
```

Dry run:

```bash
vibe ask "最大化当前窗口" --dry-run --json
```

Review-required action:

```bash
vibe ask "关闭浏览器" --json
vibe reviews pending --json
```

The response includes a `review_id`, for example:

```text
rev_11b0a26e1801
```

Approve after reviewing:

```bash
vibe approve rev_11b0a26e1801 --json
```

Preview without consuming the pending approval:

```bash
vibe approve rev_11b0a26e1801 --dry-run --json
```

Reject instead of approving:

```bash
vibe reviews reject rev_11b0a26e1801 --json
```

Open URI requires approval:

```bash
vibe ask "打开 https://deepseek.com" --json
vibe approve <review_id-from-previous-output> --json
```

Clipboard write requires approval:

```bash
vibe ask "写入剪贴板 内容是 hello" --json
vibe approve <review_id-from-previous-output> --json
```

Rejected high-risk action:

```bash
vibe ask "删除下载目录" --json
```

## Supported v0.1 Capabilities

```text
app.list
app.open
clipboard.write
notification.send
portal.open_uri
system.status
window.close
window.focus
window.list
window.maximize
window.minimize
```

## Audit Requirements

Audit entries include:

- user utterance
- parsed intent
- review id
- risk level
- review requirement
- approval flag
- selected target
- execution result
- timestamp

This gives VibeOS an OS-like permission trail instead of a hidden automation script.

## Why Approve By ID

`vibe approve <review_id>` executes the stored intent from the review request. It does not ask the model to parse the natural-language command again. This prevents a user from reviewing one action and accidentally approving a different model output.

Approval is one-time. After a real approval attempt, the review is marked `consumed`; reusing the same `review_id` is rejected. `--dry-run` previews the stored intent but leaves the review `pending`.

Pending L2 reviews also expire. The default TTL is 600 seconds and can be changed with:

```env
VIBEOS_REVIEW_TTL_SECONDS=600
```

Expired reviews are removed from `vibe reviews pending` and cannot be approved or rejected. Legacy review records without an `expires_at` field are treated as expired.

The same rule applies to service integrations:

- HTTP clients pass `{"review_id": "rev_..."}` to `/v1/command`.
- D-Bus clients call `org.vibeos.Agent.ApproveReview("rev_...")`.
- HTTP clients reject with `{"review_id": "rev_...", "reject": true}` to `/v1/command`.
- D-Bus clients call `org.vibeos.Agent.RejectReview("rev_...")`.

## Inspecting Review State

Pending reviews can be inspected without re-running the model:

```bash
vibe reviews pending --json
```

The daemon also exposes this state for future overlays:

- HTTP: `GET /v1/reviews/pending`
- D-Bus: `org.vibeos.Agent.PendingReviews()`

Capability metadata is available through:

```bash
vibe capabilities --json
```

Service equivalents:

- HTTP: `GET /v1/capabilities`
- D-Bus: `org.vibeos.Agent.Capabilities()`
