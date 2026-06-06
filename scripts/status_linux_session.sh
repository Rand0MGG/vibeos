#!/usr/bin/env bash
set -euo pipefail

echo "== VibeOS doctor =="
vibe doctor || true

echo
echo "== vibed systemd user service =="
if command -v systemctl >/dev/null 2>&1; then
  systemctl --user cat vibed.service --no-pager || true
  systemctl --user status vibed.service --no-pager -l || true
else
  echo "systemctl not found"
fi

echo
echo "== vibed journal =="
if command -v journalctl >/dev/null 2>&1; then
  journalctl --user -u vibed.service -n 120 --no-pager || true
else
  echo "journalctl not found"
fi

echo
echo "== VibeOS Agent D-Bus API =="
if command -v gdbus >/dev/null 2>&1; then
  gdbus introspect \
    --session \
    --dest org.vibeos.Agent \
    --object-path /org/vibeos/Agent || true
  echo
  gdbus call \
    --session \
    --dest org.vibeos.Agent \
    --object-path /org/vibeos/Agent \
    --method org.vibeos.Agent.Status || true
else
  echo "gdbus not found"
fi

echo
echo "== vibed HTTP API =="
if command -v curl >/dev/null 2>&1; then
  curl --silent --show-error http://127.0.0.1:8765/v1/status || true
  echo
else
  echo "curl not found"
fi

echo
echo "== GNOME extension =="
if command -v gnome-extensions >/dev/null 2>&1; then
  gnome-extensions list | grep vibeos || true
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
