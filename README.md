# VibeOS

VibeOS v0 is a modern Linux user-session agent runtime prototype. It turns natural-language requests into synthesized goals, validated task plans, auditable capability execution, and explicit completion checks for GNOME Wayland sessions.

The first target is intentionally small:

- Synthesize typed goals from user requests using only registered domains and capabilities.
- Plan through explicit domain packs instead of a legacy direct-intent main path.
- Review every capability through a risk policy before execution.
- Separate execution success from acceptance success.
- Record run traces, debug traces, and audit logs for every request.
- Prepare Linux session integration through D-Bus, XDG Desktop Portal, GNOME Shell Extension, and `systemd --user`.

VibeOS v0 does not modify the Linux kernel and does not allow arbitrary shell execution.

## Permission Model

VibeOS v0.1 has a capability permission layer:

- L0 observe-only actions execute automatically.
- L1 low-risk session actions execute automatically and are audited.
- L2 medium-risk actions create a `review_id` and require `vibe approve <review_id>`.
- L3 high-risk actions are rejected.

See [docs/permission_review_layer.md](docs/permission_review_layer.md).
Capability definitions live in [docs/capability_registry.md](docs/capability_registry.md).

L2 approvals are bound to a stored `review_id`, so approving an action does not re-run model parsing.
Real approvals are consumed after one execution attempt; `--dry-run` previews without consuming.
Pending L2 reviews expire after `VIBEOS_REVIEW_TTL_SECONDS`, defaulting to 600 seconds.
Permission review also checks action targets, such as URI scheme, clipboard content, and app/window target shape.

## Quick Start

Core logic can be exercised anywhere with Python 3.11+:

```powershell
python -m pytest
python -m vibeos.cli ask "打开浏览器" --dry-run
python -m vibeos.cli ask "关闭浏览器" --dry-run --json
python -m vibeos.cli doctor --json
python scripts/verify_local.py
```

On a GNOME Wayland Linux VM, install the package and start the daemon:

```bash
chmod +x scripts/*.sh
./scripts/install_linux_session.sh
vibe doctor
```

The installer wires the generated `vibed.service` to the project `.env`, so the daemon and CLI use the same model API settings.

Set model configuration when using the model broker. The recommended local workflow is to copy `.env.example` to `.env` and fill in your key:

```bash
cp .env.example .env
```

For DeepSeek:

```env
VIBEOS_MODEL_PROVIDER=deepseek
DEEPSEEK_API_KEY=sk-your-deepseek-api-key
DEEPSEEK_BASE_URL=https://api.deepseek.com
DEEPSEEK_MODEL=deepseek-v4-flash
```

Do not commit `.env`; it is ignored by git.

You can also set environment variables manually:

```bash
export OPENAI_API_KEY="..."
export OPENAI_BASE_URL="https://api.openai.com/v1"
export OPENAI_MODEL="..."
```

Without an API key, VibeOS uses deterministic local rule-based synthesis and planning for the supported task surface.

## Current Architecture

The primary supported-task path is now:

```text
utterance
  -> utterance analysis
  -> goal synthesis
  -> domain routing
  -> observation
  -> capability exposure
  -> candidate plans
  -> validation
  -> review
  -> execution
  -> post-execution observation
  -> acceptance
  -> bounded retry / bounded replan
  -> run trace / debug trace / audit
```

Public command results expose:

- `execution_status`
- `acceptance_status`
- `overall_status`

Task-plan command results also expose:

- `run`
- `attempts`

Supported tasks run through `GoalLoop` as the single default orchestration
state machine. `CommandService` owns transport-neutral dispatch, and the typed
`TaskApplicationService` owns task start/review resume. The explicit
composition root assembles dependencies; `CapabilityBroker` is only a
compatibility facade, not a parallel retry/replan loop. Registered
implementations live in domain modules under `src/vibeos/tools/`, while
GoalLoop coordinates typed planning, observation, review, execution,
acceptance, and recovery services. See
[docs/runtime_convergence.md](docs/runtime_convergence.md) for the ownership
map, transactional review semantics, trace policy, and the WSL/VM boundary.

`--debug` on `vibe plan`, `vibe ask`, and `vibe approve` includes raw provider payloads in `debug_trace` with redaction and truncation safeguards.

Normal `vibe ask` and `vibe plan` requests use the configured intent broker on the main path. The deterministic `RuleIntentBroker` is reserved for `--offline` and fallback behavior, and web-like targets such as `open baidu.com` or `打开百度官网` prefer browser semantics over eager `app.open` coercion.

Configured model calls no longer silently collapse into rule parsing when the provider times out or returns an invalid payload. `RuleIntentBroker` remains available for `--offline` and missing-model setups, but provider-side failures now surface as explicit planning errors.

Browser postcondition evidence is attempt-scoped:

- every supported-task run produces a bounded `run` with typed `attempts`
- browser navigation evidence is bound to the active `attempt_id`
- verifier and acceptance now consume the same post-execution `browser_context`
- daemon transport failures also return structured `run` and `attempts` payloads

Runtime timeouts are layered and configurable:

```env
VIBEOS_PROVIDER_TIMEOUT_SECONDS=30
VIBEOS_TRANSPORT_TIMEOUT_SECONDS=45
VIBEOS_PORTAL_TIMEOUT_SECONDS=15
```

Browser search is configurable instead of being hardcoded to one engine:

```env
VIBEOS_DEFAULT_SEARCH_ENGINE=baidu
# or:
VIBEOS_SEARCH_ENGINE_URL_TEMPLATE=https://www.baidu.com/s?wd={query}
```

## CLI Examples

```bash
vibe doctor
vibe capabilities --json
vibe ask "打开浏览器" --json
vibe ask "列出窗口" --json
vibe ask "最大化当前窗口" --json
vibe ask "关闭浏览器" --json
vibe reviews pending --json
vibe reviews reject rev_... --json
vibe approve rev_... --json
vibe ask "打开 https://deepseek.com" --json
vibe approve rev_... --json
vibe ask "删除下载目录" --json
vibe audit tail
```

For a VM smoke test:

```bash
./scripts/run_vm_smoke_tests.sh
```

For a JSON acceptance evidence report:

```bash
python scripts/collect_vm_evidence.py
python scripts/collect_vm_evidence.py --real
```

For troubleshooting:

```bash
./scripts/status_linux_session.sh
```

To remove Linux session integration from a VM:

```bash
./scripts/uninstall_linux_session.sh
```

## Target Linux Environment

Primary target:

- Fedora Workstation or a modern Ubuntu GNOME release
- GNOME Wayland session
- `systemd --user`
- D-Bus session bus
- `xdg-desktop-portal`
- GNOME Shell Extension support

VMware is recommended for early testing. Use NAT networking and snapshots before testing new system integrations.

## Status

See [docs/current_status.md](docs/current_status.md) for implemented scope, verification commands, and remaining Linux VM acceptance criteria.
See [docs/runtime_convergence.md](docs/runtime_convergence.md) for the current default execution architecture.
See [docs/vm_acceptance_evidence.md](docs/vm_acceptance_evidence.md) for the VM evidence workflow.
See [docs/linux_vm_install_upgrade_test_runbook.md](docs/linux_vm_install_upgrade_test_runbook.md) for environment setup, uninstall/reinstall steps, and the complete VM test flow.
See [docs/zh_cn/README.md](docs/zh_cn/README.md) for the Chinese module-oriented documentation set.
See [docs/zh_cn/07_wsl_test_standard.md](docs/zh_cn/07_wsl_test_standard.md) for the WSL-specific test scope and workflow.
Continuous verification is configured in
[.github/workflows/test.yml](.github/workflows/test.yml) for `push` and
`pull_request`; its GitHub run status is not asserted here.
