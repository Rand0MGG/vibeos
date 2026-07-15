#!/usr/bin/env bash

set -u
export HOME=/home/rand0mg
export XDG_RUNTIME_DIR=/run/user/1000
export DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus
export XDG_SESSION_TYPE=wayland
export PATH=/home/rand0mg/vibeos/.venv/bin:/usr/local/bin:/usr/bin
export VIBEOS_RUNTIME=dbus
export VIBEOS_REQUIRE_DAEMON=1
export VIBEOS_ENABLE_MODEL_UNDERSTANDING=0
export VIBEOS_ENABLE_MODEL_UNDERSTANDING_TRANSITION=0
export VIBEOS_ENABLE_MODEL_GOAL_SYNTHESIS=0
export VIBEOS_ENABLE_MODEL_ROUTE_SELECTION=0
export VIBEOS_ENABLE_MODEL_CLARIFICATION=0
export VIBEOS_ENABLE_MODEL_REPLANNING=0
export VIBEOS_ENABLE_MODEL_SEMANTIC_ACCEPTANCE=0
export VIBEOS_ENABLE_MODEL_STRATEGY_SELECTION=0

exec >/tmp/vibeos-goal01-e1.txt 2>&1

echo '=== E0 ==='
vibe ask 'status' --json
echo '=== E1 ==='
vibe ask 'notify Goal 01 GNOME VM verified' --json
echo '=== COMPLETED ==='
