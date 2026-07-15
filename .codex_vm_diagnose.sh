#!/usr/bin/env bash

set +e
export HOME=/home/rand0mg
export XDG_RUNTIME_DIR=/run/user/1000
export DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus
export XDG_SESSION_TYPE=wayland
export PATH=/home/rand0mg/vibeos/.venv/bin:/usr/local/bin:/usr/bin

exec >/tmp/vibeos-goal01-diagnose.txt 2>&1

date --iso-8601=seconds
systemctl --user status vibed.service --no-pager -l
journalctl --user -u vibed.service -n 120 --no-pager
busctl --user list | grep -E 'org\.vibeos|NAME' || true
gdbus introspect --session --dest org.vibeos.Agent \
  --object-path /org/vibeos/Agent
gdbus call --session --dest org.vibeos.Agent \
  --object-path /org/vibeos/Agent --method org.vibeos.Agent.Status
vibe doctor --json
