#!/usr/bin/env bash
set -euo pipefail

echo "== VibeOS doctor =="
vibe doctor

echo "== L0 status =="
vibe ask "系统状态" --json

echo "== Capability registry =="
vibe capabilities --json

echo "== L1 app open dry run =="
vibe ask "打开浏览器" --dry-run --json

echo "== L2 review id flow =="
REVIEW_JSON="$(vibe ask "关闭浏览器" --json || true)"
echo "${REVIEW_JSON}"
REVIEW_ID="$(python3 -c 'import json,sys; print(json.load(sys.stdin).get("review_id",""))' <<< "${REVIEW_JSON}")"
if [[ -z "${REVIEW_ID}" ]]; then
  echo "error: expected a review_id for window.close" >&2
  exit 1
fi
vibe reviews pending --json
vibe approve "${REVIEW_ID}" --dry-run --json

echo "== L2 reject flow =="
REJECT_JSON="$(vibe ask "关闭终端" --json || true)"
echo "${REJECT_JSON}"
REJECT_ID="$(python3 -c 'import json,sys; print(json.load(sys.stdin).get("review_id",""))' <<< "${REJECT_JSON}")"
if [[ -z "${REJECT_ID}" ]]; then
  echo "error: expected a review_id for reject flow" >&2
  exit 1
fi
vibe reviews reject "${REJECT_ID}" --json
vibe approve "${REJECT_ID}" --json || true

echo "== Target policy constraints =="
python3 - <<'PY'
from vibeos.models import Intent
from vibeos.permissions import PermissionPolicy

review = PermissionPolicy().review(Intent(action="portal.open_uri", target={"uri": "file:///etc/passwd"}))
assert review.risk_level == "L3", review
assert not review.allowed, review
PY

echo "== L3 rejection =="
vibe ask "删除下载目录" --json || true

echo "== audit tail =="
vibe audit tail -n 10

echo "VibeOS VM smoke tests completed."
