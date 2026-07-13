# VibeOS Linux Session Doctor

`vibe doctor` reports whether the current host can exercise Linux session
capabilities. It is a diagnosis tool, not proof that desktop integration has
passed.

```bash
vibe doctor
vibe doctor --json
```

It checks the platform, session type, GNOME Shell, `gdbus`, XDG portal,
`systemd --user`, `vibed.service`, the GNOME extension bridge, desktop-app
registry, notification/clipboard helpers, and model configuration.

## Interpreting results

- **WSL or non-GNOME host:** `warn` is expected for GNOME Shell, portal,
  daemon, extension bridge, application registry, and desktop helpers. A zero
  `fail` count means the diagnostic command itself completed; it does not make
  those integrations available.
- **Target GNOME Wayland VM:** investigate every warning that is needed by the
  capability you are about to exercise. `vibed.service`, the extension bridge,
  portal, and matching `vibe`/`vibed` virtual-environment paths are especially
  important.

Useful follow-up commands on a real GNOME host:

```bash
which vibe
which vibed
systemctl --user status vibed.service --no-pager -l
systemctl --user cat vibed.service
./scripts/status_linux_session.sh
```

For the full boundary and evidence checklist, see
[GNOME VM acceptance](gnome_vm_acceptance.md). For deterministic WSL tests,
use [the WSL test standard](../zh_cn/07_wsl_test_standard.md).
