#!/usr/bin/env bash

set +e
export HOME=/home/rand0mg
export XDG_RUNTIME_DIR=/run/user/1000
export DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus
export XDG_SESSION_TYPE=wayland
export PATH=/home/rand0mg/vibeos/.venv/bin:/usr/local/bin:/usr/bin

exec >/tmp/vibeos-goal01-gates.txt 2>&1
cd /home/rand0mg/vibeos || exit 90

failures=0
run_gate() {
  local name=$1
  shift
  echo "=== ${name} ==="
  "$@"
  local status=$?
  echo "=== ${name}_EXIT=${status} ==="
  if (( status != 0 )); then
    failures=$((failures + 1))
  fi
}

run_gate RUFF_LINT ruff check .
run_gate RUFF_FORMAT ruff format --check .
run_gate MYPY python -m mypy --strict
run_gate ARCHITECTURE python scripts/architecture_guard.py
run_gate PYTEST python -m pytest -q

echo "GATE_FAILURES=${failures}"
exit "${failures}"
