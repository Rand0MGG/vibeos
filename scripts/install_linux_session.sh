#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
EXTENSION_ID="vibeos@local"
EXTENSION_DEST="${HOME}/.local/share/gnome-shell/extensions/${EXTENSION_ID}"
SYSTEMD_DEST="${HOME}/.config/systemd/user"

warn_missing() {
  local command_name="$1"
  local hint="$2"
  if ! command -v "${command_name}" >/dev/null 2>&1; then
    echo "warning: ${command_name} not found. ${hint}"
  fi
}

if [[ "${XDG_SESSION_TYPE:-}" != "wayland" ]]; then
  echo "warning: XDG_SESSION_TYPE=${XDG_SESSION_TYPE:-unset}; VibeOS v0.1 targets GNOME Wayland."
fi

warn_missing gdbus "Install glib2/libglib2.0-bin for D-Bus checks."
warn_missing systemctl "systemd --user is required for vibed.service."
warn_missing notify-send "Install libnotify/libnotify-bin for notification.send."
if ! command -v wl-copy >/dev/null 2>&1 && ! command -v xclip >/dev/null 2>&1 && ! command -v xsel >/dev/null 2>&1; then
  echo "warning: no clipboard helper found. Install wl-clipboard, xclip, or xsel for clipboard.write."
fi

echo "Installing VibeOS package in editable mode..."
python3 -m pip install -e "${ROOT_DIR}[dev]"
VIBED_BIN="$(command -v vibed || true)"
if [[ -z "${VIBED_BIN}" ]]; then
  echo "error: vibed entrypoint was not found on PATH after installation." >&2
  echo "Activate your virtual environment, then rerun this script." >&2
  exit 1
fi

echo "Installing GNOME Shell extension..."
mkdir -p "${EXTENSION_DEST}"
cp -r "${ROOT_DIR}/gnome-extension/${EXTENSION_ID}/." "${EXTENSION_DEST}/"

if command -v gnome-extensions >/dev/null 2>&1; then
  gnome-extensions enable "${EXTENSION_ID}" || true
  gnome-extensions info "${EXTENSION_ID}" || true
else
  echo "warning: gnome-extensions command not found; enable ${EXTENSION_ID} manually."
fi

echo "Installing systemd user service..."
mkdir -p "${SYSTEMD_DEST}"
cat > "${SYSTEMD_DEST}/vibed.service" <<UNIT
[Unit]
Description=VibeOS user-session agent daemon
After=graphical-session.target

[Service]
Type=simple
WorkingDirectory=${ROOT_DIR}
ExecStart=${VIBED_BIN} --dbus
Restart=on-failure
RestartSec=2
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=default.target
UNIT
systemctl --user daemon-reload
systemctl --user enable --now vibed.service
if systemctl --user is-active --quiet vibed.service; then
  echo "vibed.service is active."
else
  echo "warning: vibed.service is not active after install." >&2
  systemctl --user status vibed.service --no-pager -l || true
  journalctl --user -u vibed.service -n 80 --no-pager || true
fi

echo "Running VibeOS doctor..."
vibe doctor

echo
echo "If the GNOME extension bridge is not responding, log out and back in, then run: vibe doctor"
