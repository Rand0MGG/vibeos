#!/usr/bin/env bash

set -Eeuo pipefail

repo=/home/rand0mg/vibeos
backup=/home/rand0mg/vibeos-pre-goal01-7366820-20260715
candidate=/home/rand0mg/vibeos-goal01-failed-20260715

export HOME=/home/rand0mg
export XDG_RUNTIME_DIR=/run/user/1000
export DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus
export XDG_SESSION_TYPE=wayland

exec >/tmp/vibeos-goal01-resume.txt 2>&1

rollback() {
  local status=$?
  trap - ERR
  set +e
  systemctl --user stop vibed.service
  if [[ -d "${repo}" && ! -e "${candidate}" ]]; then
    mv "${repo}" "${candidate}"
  fi
  if [[ -d "${candidate}/.venv" && ! -e "${backup}/.venv" ]]; then
    mv "${candidate}/.venv" "${backup}/.venv"
  fi
  if [[ -d "${backup}" && ! -e "${repo}" ]]; then
    mv "${backup}" "${repo}"
  fi
  systemctl --user daemon-reload
  systemctl --user start vibed.service
  echo "ROLLBACK_STATUS=${status}"
  exit "${status}"
}
trap rollback ERR

echo "RESUME_STARTED=$(date --iso-8601=seconds)"
[[ -d "${repo}/.git" ]]
[[ -x "${repo}/.venv/bin/python" ]]
[[ -d "${candidate}/src/vibeos/core" ]]
[[ ! -e "${backup}" ]]
[[ -z "$(git -C "${repo}" status --porcelain)" ]]

systemctl --user stop vibed.service
mv "${repo}" "${backup}"
mv "${candidate}" "${repo}"
mv "${backup}/.venv" "${repo}/.venv"
if [[ -f "${backup}/.env" ]]; then
  cp -p "${backup}/.env" "${repo}/.env"
fi
if [[ -d "${backup}/.vibeos" ]]; then
  mv "${backup}/.vibeos" "${repo}/.vibeos"
fi

cd "${repo}"
export PATH="${repo}/.venv/bin:${PATH}"
python -m pip install -e '.[dev]'
bash ./scripts/install_linux_session.sh
gnome-extensions disable vibeos@local || true
gnome-extensions enable vibeos@local || true
systemctl --user restart vibed.service
systemctl --user is-active vibed.service
vibe doctor --json
echo "RESUME_COMPLETED=$(date --iso-8601=seconds)"
