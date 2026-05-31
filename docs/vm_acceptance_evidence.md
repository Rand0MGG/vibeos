# VM Acceptance Evidence

Use this after installing VibeOS inside a Fedora Workstation or Ubuntu GNOME Wayland VM.

The evidence collector runs the same acceptance path that matters for v0.1:

- session doctor
- capability registry
- deterministic app/window listing
- L0 status query
- L1 dry-run action
- L2 `review_id` creation
- pending review inspection
- approval by stored review id
- one-time approval consumption through unit coverage
- explicit review rejection through CLI/service APIs
- L3 rejection
- target-policy rejection for unsafe URI targets
- audit tail

## Safe Evidence Run

```bash
python scripts/collect_vm_evidence.py
```

This writes a JSON report under:

```text
.vibeos-vm-evidence/
```

By default the script uses an isolated state directory:

```text
.vibeos-vm-evidence/state/<run-timestamp>/
```

That keeps test reviews and audit logs separate from the normal user-session state.

## Real Action Evidence Run

After taking a VMware snapshot, run:

```bash
python scripts/collect_vm_evidence.py --real
```

`--real` also attempts:

- `notification.send`
- `clipboard.write` through the L2 approval flow
- `portal.open_uri` through the L2 approval flow

The report is considered acceptable only when:

```json
{
  "overall": "ok",
  "mode": "real"
}
```

In `--real` mode the doctor step is strict: platform, Wayland session, GNOME Shell, `gdbus`, XDG Desktop Portal, `systemd --user`, `vibed.service`, GNOME extension bridge, app registry, and action helpers must all report `ok`. Model configuration may still be `warn` if you intentionally run the local rule parser.

Use `--session-state` only when you deliberately want the evidence run to use the normal VibeOS audit/review state:

```bash
python scripts/collect_vm_evidence.py --real --session-state
```

## Helpful VM Packages

Fedora:

```bash
sudo dnf install python3-pip glib2 wl-clipboard libnotify
```

Ubuntu:

```bash
sudo apt install python3-venv python3-pip libglib2.0-bin wl-clipboard libnotify-bin
```

`vibe doctor` reports missing helper tools as `action_helpers` warnings.
