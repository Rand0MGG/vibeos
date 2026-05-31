# VibeOS Current Status

Last updated: 2026-05-31

## Implemented

- Natural-language command path:
  - `vibe ask`
  - local rule parser fallback
  - OpenAI-compatible model adapter
  - DeepSeek `.env` configuration
- Capability broker:
  - fixed action allowlist
  - centralized capability registry
  - no arbitrary shell execution
  - no raw D-Bus path execution from model output
- Permission review layer:
  - L0 observe-only automatic execution
  - L1 low-risk automatic execution with audit
  - L2 medium-risk persistent `review_id`
  - L2 review expiration with configurable TTL
  - `vibe approve <review_id>` approval flow
  - one-time approval consumption after real execution attempts
  - `vibe reviews reject <review_id>` rejection flow
  - `vibe reviews pending` review inspection
  - L3 rejection
  - target-level constraints for URI scheme, clipboard content, notification text, and app/window targets
- Linux session adapters:
  - `.desktop` app registry
  - XDG portal status and URI open adapter
  - GNOME Shell extension bridge for window list/focus/minimize/maximize/close
  - GNOME Shell metadata declares versions 45-50
  - notification adapter
  - clipboard write adapter
- Operational tooling:
  - `vibe doctor`
  - `vibe capabilities`
  - `vibe reviews pending`
  - `vibe reviews reject`
  - `scripts/install_linux_session.sh` with daemon `.env` wiring
  - `scripts/run_vm_smoke_tests.sh`
  - `scripts/collect_vm_evidence.py`
  - `scripts/status_linux_session.sh`
  - `scripts/uninstall_linux_session.sh`
- Documentation:
  - DeepSeek setup
  - permission review model
  - capability registry
  - Linux session doctor
  - Linux VM permission test checklist
  - VM acceptance evidence workflow
  - v0 Linux session agent plan

## Local Verification

Run on the current development machine:

```powershell
conda activate vibeos
python scripts/verify_local.py
```

The local verification checks:

- unit tests
- `vibe doctor --json`
- `vibe capabilities --json`
- `scripts/collect_vm_evidence.py` safe mode
- L1 dry-run command
- L2 `review_required`
- `vibe reviews pending --json`
- `vibe approve <review_id> --dry-run`
- unit-tested one-time approval consumption and rejection
- unit-tested review expiration
- L3 rejection

On Windows, `vibe doctor` is expected to report `overall: warn` because GNOME Wayland, D-Bus session services, XDG portal, and GNOME Shell extension are not available.

Latest local verification command:

```powershell
python scripts/verify_local.py
```

Expected current result:

```text
overall: ok
pytest: 43 passed
doctor: overall warn on Windows
capabilities: ok
L1 dry-run: ok
L2 review_required: ok
reviews pending: ok
approve review dry-run: ok
one-time approval/reject tests: ok
review expiration tests: ok
L3 rejection: ok
VM evidence safe mode: ok
```

## Still Requires Linux VM Verification

The following items cannot be proven from the current Windows host:

- `vibed.service` actually starts under `systemd --user`
- GNOME Shell loads `vibeos@local`
- `org.vibeos.Shell.ListWindows` responds over D-Bus
- `window.focus`, `window.minimize`, `window.maximize`, and `window.close` affect real windows
- `notification.send` displays a real desktop notification
- `portal.open_uri` opens a real browser/app through the session
- `clipboard.write` writes to the real Linux desktop clipboard

Use:

```bash
chmod +x scripts/*.sh
./scripts/install_linux_session.sh
vibe doctor
./scripts/run_vm_smoke_tests.sh
python scripts/collect_vm_evidence.py --real
```

## Completion Criteria

This milestone should be treated as complete only after:

1. Local verification passes.
2. Linux VM `vibe doctor` shows no `fail` checks.
3. The VM smoke test script completes.
4. At least one real L1 desktop action changes the GNOME session.
5. At least one L2 action creates a `review_id` and can be approved by ID.
6. Approved L2 review ids cannot be reused after consumption.
7. Expired L2 review ids cannot be approved or rejected.
8. Pending L2 reviews can be rejected and rejected ids cannot be approved.
9. Pending reviews are inspectable through CLI and service APIs.
10. A real VM evidence report from `python scripts/collect_vm_evidence.py --real` has `overall: ok`.
11. Audit log entries show utterance, intent, review id, risk level, decision, and result.
