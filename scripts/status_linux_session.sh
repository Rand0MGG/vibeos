#!/usr/bin/env bash
set -euo pipefail

echo "== VibeOS doctor =="
vibe doctor || true

echo
echo "== vibed systemd user service =="
if command -v systemctl >/dev/null 2>&1; then
  systemctl --user status vibed.service --no-pager || true
else
  echo "systemctl not found"
fi

echo
echo "== GNOME extension =="
if command -v gnome-extensions >/dev/null 2>&1; then
  gnome-extensions info vibeos@local || true
else
  echo "gnome-extensions not found"
fi

echo
echo "== VibeOS Shell bridge =="
if command -v gdbus >/dev/null 2>&1; then
  gdbus call \
    --session \
    --dest org.vibeos.Shell \
    --object-path /org/vibeos/Shell \
    --method org.vibeos.Shell.ListWindows || true
else
  echo "gdbus not found"
fi
