# Linux VM Permission Test Checklist

Use this checklist inside a Fedora Workstation or Ubuntu GNOME Wayland VM.

## Environment

```bash
echo "$XDG_SESSION_TYPE"
gnome-shell --version
systemctl --user status
```

Expected:

```text
wayland
GNOME 45+
systemd --user available
```

## Install

Optional helper packages:

Fedora:

```bash
sudo dnf install python3-pip glib2 wl-clipboard libnotify
```

Ubuntu:

```bash
sudo apt install python3-venv python3-pip libglib2.0-bin wl-clipboard libnotify-bin
```

```bash
cd ~/vibeos
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env
```

Fill `.env` with DeepSeek settings.

Or run the project helper after filling `.env`:

```bash
chmod +x scripts/*.sh
./scripts/install_linux_session.sh
```

Then check readiness:

```bash
vibe doctor
vibe doctor --json
```

## GNOME Shell Extension

```bash
mkdir -p ~/.local/share/gnome-shell/extensions/vibeos@local
cp -r gnome-extension/vibeos@local/* ~/.local/share/gnome-shell/extensions/vibeos@local/
gnome-extensions enable vibeos@local
```

Log out and log back in if GNOME does not load the extension immediately.

## Daemon

The installer generates `~/.config/systemd/user/vibed.service` with the actual `vibed` executable path from your active Python environment. This avoids pointing systemd at the wrong venv or user-local path.

It also writes:

- `WorkingDirectory=<project-root>`
- `Environment=VIBEOS_ENV_FILE=<project-root>/.env`
- `EnvironmentFile=-<project-root>/.env`

That lets the daemon read the same DeepSeek/OpenAI-compatible settings as the CLI.

Manual install fallback:

```bash
mkdir -p ~/.config/systemd/user
cp packaging/systemd/vibed.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now vibed.service
systemctl --user status vibed.service
```

If the manual fallback is used, make sure `vibed` is on the `systemd --user` service PATH. Prefer `./scripts/install_linux_session.sh` when possible.

## Permission Behavior

L0 observe-only:

```bash
vibe ask "列出窗口" --json
vibe ask "系统状态" --json
vibe capabilities --json
```

L1 automatic low-risk:

```bash
vibe ask "打开浏览器" --json
vibe ask "切到浏览器" --json
vibe ask "最大化当前窗口" --json
vibe ask "最小化当前窗口" --json
vibe ask "发一个通知，内容是 VibeOS 测试成功" --json
```

L2 review-required:

```bash
vibe ask "关闭浏览器" --json
vibe reviews pending --json
vibe approve <review_id-from-previous-output> --json
vibe approve <same-review-id> --json   # expected rejected after consumption
vibe ask "打开 https://deepseek.com" --json
vibe approve <review_id-from-previous-output> --json
vibe ask "写入剪贴板 内容是 hello" --json
vibe approve <review_id-from-previous-output> --json
```

Reject flow:

```bash
vibe ask "关闭浏览器" --json
vibe reviews reject <review_id-from-previous-output> --json
vibe approve <same-review-id> --json   # expected rejected
```

L3 rejected:

```bash
vibe ask "删除下载目录" --json
vibe ask "安装一个软件" --json
vibe ask "执行 shell 命令 rm -rf /tmp/test" --json
```

## Audit Verification

```bash
vibe audit tail -n 20
```

Confirm entries include:

- `utterance`
- `intent`
- `review_id`
- `review.risk_level`
- `review.review_required`
- `approved`
- `status`
- `selected_target`
- `result`

## Full Smoke Test Script

```bash
./scripts/run_vm_smoke_tests.sh
```

This script verifies doctor output, L0/L1/L2/L3 behavior, review-id approval, review rejection, and audit visibility. It uses `--dry-run` for the review-id approval path so it is safe before you try real window-close actions.

It also checks the capability registry and target-policy constraints, including rejection of unsafe `portal.open_uri` targets such as local file URIs.

## Evidence Report

Generate a JSON evidence report:

```bash
python scripts/collect_vm_evidence.py
```

After taking a VMware snapshot, collect real action evidence:

```bash
python scripts/collect_vm_evidence.py --real
```

The report is written under `.vibeos-vm-evidence/`. See `docs/vm_acceptance_evidence.md`.

## Troubleshooting Script

```bash
./scripts/status_linux_session.sh
```

This prints `vibe doctor`, `vibed.service` status, GNOME extension info, and a direct D-Bus call to the VibeOS Shell bridge.

## Uninstall From VM

```bash
./scripts/uninstall_linux_session.sh
```

This removes the user service and GNOME extension but leaves your Python environment and project files untouched.

## Service API Approval Check

After creating a review request through the CLI, verify the D-Bus approval method:

```bash
gdbus call \
  --session \
  --dest org.vibeos.Agent \
  --object-path /org/vibeos/Agent \
  --method org.vibeos.Agent.ApproveReview \
  "<review_id>"
```

Reject a pending review over D-Bus:

```bash
gdbus call \
  --session \
  --dest org.vibeos.Agent \
  --object-path /org/vibeos/Agent \
  --method org.vibeos.Agent.RejectReview \
  "<review_id>"
```

Capability and review inspection methods:

```bash
gdbus call \
  --session \
  --dest org.vibeos.Agent \
  --object-path /org/vibeos/Agent \
  --method org.vibeos.Agent.Capabilities

gdbus call \
  --session \
  --dest org.vibeos.Agent \
  --object-path /org/vibeos/Agent \
  --method org.vibeos.Agent.PendingReviews
```
