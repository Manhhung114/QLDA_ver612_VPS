#!/usr/bin/env bash
set -euo pipefail

APP_DIR="${QLDA_APP_DIR:-/opt/qlda/app}"
VENV_DIR="${QLDA_VENV_DIR:-/opt/qlda/venv}"
SHARED_DIR="${QLDA_SHARED_DIR:-/opt/qlda/shared}"
SERVICE="${QLDA_SERVICE:-qlda}"
RUN_USER="${QLDA_RUN_USER:-qlda}"

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run with sudo: sudo $0" >&2
  exit 1
fi

PREV_FILE="$SHARED_DIR/previous_commit"
if [[ ! -s "$PREV_FILE" ]]; then
  echo "No previous commit recorded in $PREV_FILE" >&2
  exit 1
fi

PREV="$(cat "$PREV_FILE")"
runuser -u "$RUN_USER" -- git -C "$APP_DIR" reset --hard "$PREV"
runuser -u "$RUN_USER" -- "$VENV_DIR/bin/python" -m pip install --disable-pip-version-check -r "$APP_DIR/requirements.txt"
systemctl restart "$SERVICE"
sleep 3
"$APP_DIR/vps/healthcheck.sh"
echo "Rolled back to $PREV"
