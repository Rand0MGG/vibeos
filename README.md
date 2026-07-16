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

VibeOS has a capability permission layer:

- L0 observe-only actions execute automatically.
- L1 low-risk session actions execute automatically and are audited.
- L2 medium-risk actions create a `review_id` and require `vibe approve <review_id>`.
- L3 high-risk actions are rejected.

See [docs/architecture/capability_registry.md](docs/architecture/capability_registry.md).

L2 approvals are bound to the durable task, step, and current safety-review
digest, so approval does not re-run model parsing or authorize a changed action.
Approval uses the task revision plus a fenced lease, and the same interaction
cannot dispatch twice. `--dry-run` previews without consuming the pending
interaction; execution failures enter durable recovery or reconciliation.
Permission review also checks action targets, such as URI scheme, clipboard
content, and app/window target shape.

## Quick Start

Core logic can be exercised anywhere with Python 3.11+:

```powershell
python -m pytest
python -m vibeos.cli ask "打开浏览器" --offline --dry-run
python -m vibeos.cli ask "关闭浏览器" --offline --dry-run --json
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

Provider-backed planning fails closed when no API key is configured. Use the
explicit `--offline` flag for deterministic local rule-based synthesis and
planning.

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

Supported tasks run through the durable task engine as the single production
state machine. `CommandService` owns transport-neutral dispatch, and the typed
`TaskApplicationService` owns task start, review/clarification resume, and CAS
controls. State, events, outbox messages, leases, proposals, receipts, and
evidence commit to one SQLite database. Registered implementations live in
domain modules under `src/vibeos/tools/`. See
[docs/architecture/runtime_convergence.md](docs/architecture/runtime_convergence.md) for the ownership
map and [docs/architecture/durable_task_engine.md](docs/architecture/durable_task_engine.md)
for recovery and control semantics.

`--debug` on `vibe plan`, `vibe ask`, and `vibe approve` includes raw provider payloads in `debug_trace` with redaction and truncation safeguards.

Normal `vibe ask` and `vibe plan` requests use the configured intent broker on
the main path. The deterministic `RuleIntentBroker` is reserved for explicit
`--offline` operation. Web-like targets such as `open baidu.com` or
`打开百度官网` prefer browser semantics over eager `app.open` coercion.

Missing provider configuration, timeouts, and invalid provider payloads surface
as explicit planning errors instead of silently changing semantics. Use
`--offline` when deterministic local parsing is intended.

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
vibe tasks list --json
vibe tasks show task_... --json
vibe tasks pause task_... --expected-revision 4 --reason "operator hold" --json
vibe tasks resume task_... --expected-revision 5 --json
vibe tasks takeover task_... --expected-revision 6 --owner alice --json
vibe tasks release task_... --expected-revision 7 --json
vibe tasks cancel task_... --expected-revision 8 --json
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

See [docs/product/product_charter.md](docs/product/product_charter.md) for the
current product mission, target users, boundaries, and north-star outcome.
See [docs/product/strategic_goals.md](docs/product/strategic_goals.md) for the
strategic goal hierarchy and recommended sequence of work.
See [docs/product/agent_system_framework.md](docs/product/agent_system_framework.md)
for the target Agent-native architecture, privilege review, rollback, secret
handling, durable-task runtime, and cloud/local model routing.
See [docs/product/decisions/0001-agent-native-direction.md](docs/product/decisions/0001-agent-native-direction.md)
for the accepted direction decisions and open design questions.
See [docs/product/decisions/0002-implementation-foundation.md](docs/product/decisions/0002-implementation-foundation.md)
for the accepted implementation stack, isolation boundaries, and replacement
strategy.
See [docs/goals/agent_native/README.md](docs/goals/agent_native/README.md) for
the dependency-ordered implementation plan and nine directly executable Codex
goal contracts.
See [docs/README.md](docs/README.md) for the current documentation map.
See [docs/architecture/current_status.md](docs/architecture/current_status.md) for implemented scope and exact verification evidence.
See [docs/architecture/runtime_convergence.md](docs/architecture/runtime_convergence.md) for the current default execution architecture.
See [docs/architecture/core_foundation.md](docs/architecture/core_foundation.md) for the Goal 01 layered core, unified database, daemon lifecycle, and migration inventory.
See [docs/operations/gnome_vm_acceptance.md](docs/operations/gnome_vm_acceptance.md) for the manual GNOME VM boundary.
See [docs/zh_cn/README.md](docs/zh_cn/README.md) for the Chinese documentation set.
See [docs/zh_cn/07_wsl_test_standard.md](docs/zh_cn/07_wsl_test_standard.md) for the WSL-specific test scope and workflow.
Continuous verification is configured in
[.github/workflows/test.yml](.github/workflows/test.yml) for `push` and
`pull_request`; its GitHub run status is not asserted here.
