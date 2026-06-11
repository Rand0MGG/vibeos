# Linux VM Install, Upgrade, and Test Runbook

Use this runbook on a Fedora Workstation or Ubuntu GNOME Wayland VM.

It covers:

- preparing the VM environment
- creating and activating the project `venv`
- uninstalling an older VibeOS session install so it no longer stays resident
- installing the current repository version
- verifying which `vibe` and `vibed` are actually running
- running a complete smoke-test and evidence-test flow

## 1. Prerequisites

Target session:

- GNOME Wayland
- `systemd --user`
- D-Bus session bus
- `xdg-desktop-portal`

Fedora packages:

```bash
sudo dnf install python3 python3-pip python3-venv glib2 wl-clipboard libnotify curl
```

Ubuntu packages:

```bash
sudo apt install python3 python3-venv python3-pip libglib2.0-bin wl-clipboard libnotify-bin curl
```

Recommended before testing:

- use a VM snapshot
- log into a normal desktop session, not a pure TTY
- keep one terminal open in the project root

## 2. Configure the Python Environment

From the repository root:

```bash
cd ~/vibeos
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -e ".[dev]"
```

Confirm the active environment:

```bash
which python
which vibe
which vibed
```

Expected shape:

```text
~/vibeos/.venv/bin/python
~/vibeos/.venv/bin/vibe
~/vibeos/.venv/bin/vibed
```

## 3. Configure Model and Runtime Environment

If you use model-backed planning, create `.env`:

```bash
cd ~/vibeos
cp .env.example .env
```

Example:

```env
VIBEOS_MODEL_PROVIDER=deepseek
DEEPSEEK_API_KEY=sk-your-key
DEEPSEEK_BASE_URL=https://api.deepseek.com
DEEPSEEK_MODEL=deepseek-v4-flash
```

Browser search can be configured per environment:

```env
VIBEOS_DEFAULT_SEARCH_ENGINE=baidu
VIBEOS_SEARCH_ENGINE_URL_TEMPLATE=https://www.baidu.com/s?wd={query}
```

If you want to require daemon transport during VM testing:

```env
VIBEOS_REQUIRE_DAEMON=1
```

## 4. Fully Uninstall an Older Session Install

This step is for replacing an older installed version and making sure it is no longer resident.

First activate the project environment:

```bash
cd ~/vibeos
source .venv/bin/activate
```

Run the normal uninstall script:

```bash
./scripts/uninstall_linux_session.sh || true
```

Then verify that no user service remains:

```bash
systemctl --user daemon-reload
systemctl --user cat vibed.service
systemctl --user status vibed.service --no-pager -l
```

Expected result:

- `systemctl --user cat vibed.service` reports no unit found
- `systemctl --user status vibed.service` reports no unit found or inactive

If you want to force-clean any lingering user-session wiring:

```bash
systemctl --user stop vibed.service || true
systemctl --user disable vibed.service || true
rm -f ~/.config/systemd/user/vibed.service
rm -f ~/.config/systemd/user/default.target.wants/vibed.service
rm -rf ~/.local/share/gnome-shell/extensions/vibeos@local
systemctl --user daemon-reload
hash -r
```

Important:

- this removes the user service and GNOME extension install
- it does not delete the repository
- it does not remove the `.venv` unless you delete it yourself

If you also want a fresh Python environment, rebuild it:

```bash
rm -rf .venv
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -e ".[dev]"
```

## 5. Install the Current Repository Version

Use the repository you want to test:

```bash
cd ~/vibeos
source .venv/bin/activate
chmod +x scripts/*.sh
./scripts/install_linux_session.sh
```

This script:

- installs the package in editable mode
- copies the GNOME Shell extension
- writes `~/.config/systemd/user/vibed.service`
- starts `vibed.service`
- runs `vibe doctor`

## 6. Verify the Active Install

Check that the CLI and daemon point to the same environment:

```bash
which vibe
which vibed
systemctl --user cat vibed.service
systemctl --user status vibed.service --no-pager -l
```

What to confirm:

- `which vibe` points to `~/vibeos/.venv/bin/vibe`
- `which vibed` points to `~/vibeos/.venv/bin/vibed`
- `ExecStart=` inside `vibed.service` points to the same `venv`
- the service is active and recently started

If the unit file changed but the daemon is still old, restart it explicitly:

```bash
systemctl --user daemon-reload
systemctl --user restart vibed.service
systemctl --user status vibed.service --no-pager -l
```

## 7. Baseline Diagnostics

Run:

```bash
cd ~/vibeos
source .venv/bin/activate
vibe doctor --json
vibe capabilities --json
./scripts/status_linux_session.sh
```

These checks tell you whether the runtime is using:

- `local`
- `dbus`
- `http`

For daemon-backed VM validation, prefer `dbus` or `http`, not `local`.

## 8. Local Code Regression

Before desktop actions, run the Python test suite:

```bash
cd ~/vibeos
source .venv/bin/activate
python -m pytest -q
```

## 9. Functional VM Smoke Tests

Run these in order:

```bash
cd ~/vibeos
source .venv/bin/activate

vibe plan "open browser" --json
vibe plan "open https://example.com" --json
vibe plan "clipboard hello" --json
vibe ask "search web for hello" --json --offline --dry-run

vibe ask "open browser" --json
vibe ask "list windows" --json
vibe ask "notify hello" --json
vibe ask "open https://example.com" --json
vibe ask "search web for hello" --json
vibe ask "copy hello to clipboard" --json
vibe reviews pending --json
```

For L2 review flow, approve the returned review id:

```bash
vibe approve <review_id> --json
```

## 10. Full Acceptance Evidence

Safe evidence run:

```bash
cd ~/vibeos
source .venv/bin/activate
python scripts/collect_vm_evidence.py
```

Real action evidence run:

```bash
cd ~/vibeos
source .venv/bin/activate
python scripts/collect_vm_evidence.py --real
```

The target result for the real run is:

```json
{
  "overall": "ok",
  "mode": "real"
}
```

## 11. Commands to Prove Which Version Is Running

These are the fastest commands when behavior looks wrong:

```bash
cd ~/vibeos
source .venv/bin/activate
which vibe
which vibed
systemctl --user cat vibed.service
systemctl --user status vibed.service --no-pager -l
vibe ask "search web for hello" --json
```

How to interpret the result:

- if `vibed.service` does not exist, you are not using the user daemon
- if the command result shows `"transport": "local"`, the CLI fell back to the local broker/runtime
- if `run` and `attempts` are present, you are on the structured v0.5 task path
- if `ExecStart=` points to a different Python environment, the service is running a different install than your shell

## 12. Known Failure Modes

### `vibed.service` not found

You have not installed Linux session integration, or you uninstalled it successfully.

Install it:

```bash
./scripts/install_linux_session.sh
```

### `transport` is `local`

The daemon is unavailable, so CLI auto-fell back to local runtime.

Check:

```bash
systemctl --user status vibed.service --no-pager -l
journalctl --user -u vibed.service -n 120 --no-pager
vibe doctor --json
```

### `which vibe` and `ExecStart=` disagree

Your shell and your daemon are using different environments.

Fix by activating the intended `venv`, reinstalling, then restarting:

```bash
source ~/vibeos/.venv/bin/activate
pip install -e ".[dev]"
./scripts/install_linux_session.sh
systemctl --user daemon-reload
systemctl --user restart vibed.service
```

### Browser tasks report success too easily

Check the returned fields:

- `transport`
- `execution_status`
- `acceptance_status`
- `overall_status`
- `run`
- `attempts`
- `acceptance.evidence`

If browser acceptance is suspicious, capture:

```bash
vibe ask "search web for hello" --json
journalctl --user -u vibed.service -n 120 --no-pager
```

## 13. Short Upgrade Flow

When you just want to replace the currently installed session version with the latest repository code:

```bash
cd ~/vibeos
source .venv/bin/activate
./scripts/uninstall_linux_session.sh || true
pip install -e ".[dev]"
./scripts/install_linux_session.sh
vibe doctor --json
systemctl --user status vibed.service --no-pager -l
```

## 14. If `collect_vm_evidence.py --real` Fails

Do not guess from the final `overall != ok` alone. Inspect the failure layer-by-layer.

Run these commands in order:

```bash
cd ~/vibeos
source .venv/bin/activate

vibe doctor --json
./scripts/status_linux_session.sh

systemctl --user status vibed.service --no-pager -l
journalctl --user -u vibed.service -n 200 --no-pager

python scripts/collect_vm_evidence.py --real
ls -lt .vibeos-vm-evidence/
```

Then inspect the newest evidence report:

```bash
python -m json.tool .vibeos-vm-evidence/<latest-report>.json | less
```

If you prefer quick grep-style checks:

```bash
grep -n '"overall"' .vibeos-vm-evidence/<latest-report>.json
grep -n '"doctor"' .vibeos-vm-evidence/<latest-report>.json
grep -n '"runtime_entry"' .vibeos-vm-evidence/<latest-report>.json
grep -n '"vibed_service"' .vibeos-vm-evidence/<latest-report>.json
grep -n '"gnome_extension_bridge"' .vibeos-vm-evidence/<latest-report>.json
grep -n '"transport"' .vibeos-vm-evidence/<latest-report>.json
```

What each layer means:

- `vibe doctor --json`
  - tells you whether the machine is even capable of passing the real evidence run
  - if `vibed_service`, `gnome_extension_bridge`, `xdg_desktop_portal`, or `action_helpers` are `fail`, fix these first
- `status_linux_session.sh`
  - shows the unit file, journal, D-Bus API, HTTP API, and extension state in one place
- `systemctl --user status vibed.service`
  - tells you whether the daemon is active, restarting, or crashing
- `journalctl --user -u vibed.service`
  - tells you why the daemon failed
- the evidence JSON
  - tells you which exact sub-check failed during the scripted acceptance run

Typical failure patterns:

### A. `vibed.service` is missing or inactive

Symptoms:

- `systemctl --user status vibed.service` says not found or inactive
- evidence report shows daemon-related checks failing

Fix:

```bash
source ~/vibeos/.venv/bin/activate
./scripts/install_linux_session.sh
systemctl --user daemon-reload
systemctl --user restart vibed.service
```

### B. `transport` falls back to `local`

Symptoms:

- `vibe ask ... --json` shows `"transport": "local"`
- real evidence should require daemon-backed transport, so this is a real setup issue

Check:

```bash
systemctl --user status vibed.service --no-pager -l
systemctl --user cat vibed.service
vibe doctor --json
```

Fix the daemon first. Do not treat a `local` pass as equivalent to daemon validation.

### C. GNOME Shell bridge is not responding

Symptoms:

- `vibe doctor --json` warns or fails on `gnome_extension_bridge`
- window-related tests fail

Check:

```bash
gnome-extensions list | grep vibeos
gnome-extensions info vibeos@local
gdbus call --session --dest org.vibeos.Shell --object-path /org/vibeos/Shell --method org.vibeos.Shell.ListWindows
```

Fix:

- re-run `./scripts/install_linux_session.sh`
- log out and log back in
- then retry `vibe doctor --json`

### D. Portal / browser action fails

Symptoms:

- browser open/search checks fail in the evidence report
- daemon journal shows portal or URI opener errors

Check:

```bash
vibe ask "open https://example.com" --json
vibe ask "search web for hello" --json
journalctl --user -u vibed.service -n 200 --no-pager
```

Look specifically at:

- `transport`
- `execution_status`
- `acceptance_status`
- `overall_status`
- `run`
- `attempts`

### E. Clipboard or notification helpers are missing

Symptoms:

- `action_helpers` warns or fails in `vibe doctor`
- review/evidence steps for clipboard or notification fail

Install the missing packages:

Fedora:

```bash
sudo dnf install wl-clipboard libnotify
```

Ubuntu:

```bash
sudo apt install wl-clipboard libnotify-bin
```

### F. The service is using the wrong Python environment

Symptoms:

- `which vibe` and `ExecStart=` point to different locations
- CLI behavior and daemon behavior disagree

Check:

```bash
which vibe
which vibed
systemctl --user cat vibed.service
```

Fix:

```bash
source ~/vibeos/.venv/bin/activate
pip install -e ".[dev]"
./scripts/install_linux_session.sh
systemctl --user daemon-reload
systemctl --user restart vibed.service
```

### G. You need one compact bug report bundle

Capture these outputs together:

```bash
cd ~/vibeos
source .venv/bin/activate
vibe doctor --json
systemctl --user status vibed.service --no-pager -l
journalctl --user -u vibed.service -n 200 --no-pager
vibe ask "search web for hello" --json
python scripts/collect_vm_evidence.py --real
```

This bundle is usually enough to tell whether the bug is in:

- environment setup
- daemon transport
- GNOME bridge
- portal/browser integration
- approval flow
- or acceptance logic
