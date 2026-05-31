# VibeOS Linux Session Doctor

`vibe doctor` diagnoses whether the current machine can run VibeOS Linux session capabilities.

It checks:

- OS platform
- `XDG_SESSION_TYPE`
- GNOME Shell version
- `gdbus`
- XDG Desktop Portal availability
- `systemd --user`
- `vibed.service`
- VibeOS GNOME Shell bridge
- `.desktop` application registry
- notification and clipboard helper tools
- model API configuration

Run:

```bash
vibe doctor
vibe doctor --json
```

Typical Windows result is `warn`, because Windows can run the broker tests but cannot execute GNOME Wayland session capabilities.

`action_helpers` reports whether notification and clipboard helper binaries are available:

- `notify-send` for `notification.send`
- `wl-copy`, `xclip`, or `xsel` for `clipboard.write`

These warnings usually mean the VM needs packages such as `wl-clipboard` and `libnotify-bin`/`libnotify`.

Typical Linux VM target:

```text
platform: ok
session_type: ok
gnome_shell: ok
gdbus: ok
xdg_desktop_portal: ok
systemd_user: ok
vibed_service: ok
gnome_extension_bridge: ok
app_registry: ok
action_helpers: ok
model_config: ok
```

If `gnome_extension_bridge` warns after install, log out and log back in so GNOME loads the extension.

For a fuller troubleshooting report, run:

```bash
./scripts/status_linux_session.sh
```
