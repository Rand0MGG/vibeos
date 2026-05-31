# VibeOS

VibeOS v0 is a modern Linux user-session agent runtime prototype. It turns natural-language requests into validated, auditable system capabilities for GNOME Wayland sessions.

The first target is intentionally small:

- Parse user commands into structured intents.
- Allow only registered capabilities.
- Route execution through a capability broker.
- Review every capability through a risk policy before execution.
- Record audit logs for every request.
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

Without an API key, VibeOS uses a conservative local rule parser for the v0 demo commands.

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
See [docs/vm_acceptance_evidence.md](docs/vm_acceptance_evidence.md) for the VM evidence workflow.
