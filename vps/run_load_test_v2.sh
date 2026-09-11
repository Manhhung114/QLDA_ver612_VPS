#!/usr/bin/env bash
set -euo pipefail

APP_DIR="${QLDA_APP_DIR:-/opt/qlda/app}"
VENV_DIR="${QLDA_VENV_DIR:-/opt/qlda/venv}"
RUN_USER="${QLDA_RUN_USER:-qlda}"
BASE_URL="${QLDA_LOADTEST_URL:-http://127.0.0.1:8501}"
LEVELS="${QLDA_LOADTEST_LEVELS:-1,2,5,10,20,30,40,50,75,100}"
TIMEOUT="${QLDA_LOADTEST_TIMEOUT:-45}"
HOLD="${QLDA_LOADTEST_HOLD:-5}"
STAMP="$(date +%Y%m%d_%H%M%S)"
OUT_DIR="/opt/qlda/shared/loadtest_v2_${STAMP}"

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run with sudo: sudo bash $APP_DIR/vps/run_load_test_v2.sh" >&2
  exit 1
fi

if ! curl -fsS --max-time 8 "${BASE_URL%/}/_stcore/health" | grep -qi '^ok'; then
  echo "QLDA is not healthy at ${BASE_URL%/}/_stcore/health" >&2
  exit 1
fi

mkdir -p "$OUT_DIR"
chown "$RUN_USER:$RUN_USER" "$OUT_DIR"

echo "============================================================"
echo " QLDA V6.22 LOAD TEST V2 - CORRECTED"
echo "============================================================"
echo "Target : $BASE_URL"
echo "Levels : $LEVELS"
echo "Output : $OUT_DIR"
echo "Note   : Read-only benchmark. Hold time is excluded from interactive latency."
echo

runuser -u "$RUN_USER" -- env \
  PYTHONPATH="$APP_DIR${PYTHONPATH:+:$PYTHONPATH}" \
  NO_PROXY="127.0.0.1,localhost" no_proxy="127.0.0.1,localhost" \
  "$VENV_DIR/bin/python" "$APP_DIR/vps/load_test_current_v2.py" \
    --base-url "$BASE_URL" \
    --levels "$LEVELS" \
    --timeout "$TIMEOUT" \
    --hold "$HOLD" \
    --out "$OUT_DIR" \
  2>&1 | tee "$OUT_DIR/console.log"

chown -R "$RUN_USER:$RUN_USER" "$OUT_DIR"

echo
echo "Completed."
echo "Report: $OUT_DIR/load_test_report_v2.md"
echo "JSON  : $OUT_DIR/load_test_v2.json"
echo "Log   : $OUT_DIR/console.log"
