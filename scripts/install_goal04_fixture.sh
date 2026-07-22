#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FIXTURE_BIN="$(command -v vibe-goal04-fixture-service || true)"
DEST_DIR="${HOME}/.config/systemd/user"
DEST_FILE="${DEST_DIR}/vibeos-goal04-fixture.service"

if [[ -z "${FIXTURE_BIN}" ]]; then
  echo "vibe-goal04-fixture-service is not installed in PATH" >&2
  exit 1
fi
if [[ ! "${FIXTURE_BIN}" = /* ]]; then
  echo "fixture executable must resolve to an absolute path" >&2
  exit 1
fi

mkdir -p "${DEST_DIR}"
sed "s|@FIXTURE_EXECUTABLE@|${FIXTURE_BIN}|g" \
  "${ROOT_DIR}/fixtures/systemd/vibeos-goal04-fixture.service.in" > "${DEST_FILE}"
systemctl --user daemon-reload
systemctl --user disable --now vibeos-goal04-fixture.service
echo "Installed ${DEST_FILE}. Run 'vibe-goal04-fixture-controller prepare' before the Goal04 task."
