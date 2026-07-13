# GNOME Wayland VM Acceptance

WSL and CI provide deterministic code verification. They do not prove the
desktop integrations that require a live GNOME Wayland user session. Run this
checklist on the target VM after the local WSL gate is green.

## Required preflight

```bash
vibe doctor --json
which vibe
which vibed
systemctl --user status vibed.service --no-pager -l
systemctl --user cat vibed.service
```

Confirm that the CLI, daemon `ExecStart`, and repository use the intended
virtual environment. Resolve required doctor warnings before treating an
integration result as evidence.

## Manual integration boundary

Record the command, transport, result JSON, and relevant journal/extension
state for each applicable capability:

- user daemon start and restart through `systemd --user`;
- GNOME Shell extension and D-Bus window list/focus/minimize/maximize/close;
- real `.desktop` application lookup and opening;
- desktop notification and clipboard helpers;
- XDG portal URI navigation;
- browser post-action observation and verifier evidence.

Do not replace these checks with a dry-run or WSL warning-free result. The
historical install/upgrade procedures and earlier evidence are available in
[`../archive/vm-evidence/`](../archive/vm-evidence/); treat them as reference
material and revalidate commands against the current repository before use.
