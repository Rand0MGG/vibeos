#!/usr/bin/env bash
set -euo pipefail

EXTENSION_ID="vibeos@local"
EXTENSION_DEST="${HOME}/.local/share/gnome-shell/extensions/${EXTENSION_ID}"
SERVICE_PATH="${HOME}/.config/systemd/user/vibed.service"

echo "Stopping VibeOS systemd user service..."
if command -v systemctl >/dev/null 2>&1; then
  systemctl --user disable --now vibed.service || true
fi

if [[ -f "${SERVICE_PATH}" ]]; then
  echo "Removing ${SERVICE_PATH}"
  rm -f "${SERVICE_PATH}"
fi

if command -v systemctl >/dev/null 2>&1; then
  systemctl --user daemon-reload || true
fi

echo "Disabling GNOME Shell extension..."
if command -v gnome-extensions >/dev/null 2>&1; then
  gnome-extensions disable "${EXTENSION_ID}" || true
fi

if [[ -d "${EXTENSION_DEST}" ]]; then
  echo "Removing ${EXTENSION_DEST}"
  rm -rf "${EXTENSION_DEST}"
fi

echo "VibeOS Linux session integration removed. Python package/env is left intact."
