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

For the Goal 01 E1 slice, record a non-dry-run `notification.send` result that
contains its typed `action_receipt` and `observation_evidence`, confirm the
adapter reports `sent`, and independently observe the notification on the
desktop. A WSL `unavailable` receipt proves accurate failure handling, not this
desktop acceptance item.

This Goal 01 item passed on the Fedora GNOME Wayland VM on 2026-07-16. The
live-provider request used the production D-Bus daemon and `/usr/bin/notify-send`,
returned an E1 succeeded receipt with passed acceptance and a completed overall
result, produced an independently observed D-Bus `Notify` call, displayed the
notification on GNOME, and left zero active daemon requests. The retained
evidence and the separate incomplete browser result are summarized in the
[Goal 01 GNOME VM evidence report](../archive/vm-evidence/goal01_gnome_vm_acceptance_2026-07-16.md).

The optional WSLg verifier can now produce a real `notify-send` receipt and
independently observe dunst/D-Bus notification state. Keep that evidence as a
useful pre-verification result, but do not substitute it for this supported
Fedora GNOME Wayland checklist.

Do not replace these checks with a dry-run or WSL warning-free result. The
historical install/upgrade procedures and earlier evidence are available in
[`../archive/vm-evidence/`](../archive/vm-evidence/); treat them as reference
material and revalidate commands against the current repository before use.
