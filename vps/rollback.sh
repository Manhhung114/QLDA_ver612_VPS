#!/usr/bin/env bash
set -euo pipefail

APP_DIR="${QLDA_APP_DIR:-/opt/qlda/app}"
VENV_DIR="${QLDA_VENV_DIR:-/opt/qlda/venv}"
SHARED_DIR="${QLDA_SHARED_DIR:-/opt/qlda/shared}"
SERVICE="${QLDA_SERVICE:-qlda}"
RUN_USER="${QLDA_RUN_USER:-qlda}"
PREV_FILE="$SHARED_DIR/previous_commit"

if [[ "${EUID}" -ne 0 ]]; then echo "Run with sudo: sudo $0" >&2; exit 1; fi
if [[ ! -s "$PREV_FILE" ]]; then echo "No previous commit recorded in $PREV_FILE" >&2; exit 1; fi

PREV="$(tr -d '[:space:]' < "$PREV_FILE")"
if ! [[ "$PREV" =~ ^[0-9a-fA-F]{40}$ ]]; then echo "Invalid previous commit: $PREV" >&2; exit 1; fi

runuser -u "$RUN_USER" -- git -C "$APP_DIR" cat-file -e "${PREV}^{commit}"
runuser -u "$RUN_USER" -- git -C "$APP_DIR" reset --hard "$PREV"
runuser -u "$RUN_USER" -- "$VENV_DIR/bin/python" -m pip install --disable-pip-version-check -r "$APP_DIR/requirements.txt"

if [[ -f "$APP_DIR/vps/qlda.service" ]]; then install -m 0644 "$APP_DIR/vps/qlda.service" /etc/systemd/system/qlda.service; fi
if [[ -f "$APP_DIR/vps/qlda-upload.service" ]]; then install -m 0644 "$APP_DIR/vps/qlda-upload.service" /etc/systemd/system/qlda-upload.service; fi
if [[ -f "$APP_DIR/vps/qlda-excel-worker.service" ]]; then install -m 0644 "$APP_DIR/vps/qlda-excel-worker.service" /etc/systemd/system/qlda-excel-worker.service; fi
if [[ -f "$APP_DIR/vps/qlda-api.service" ]]; then install -m 0644 "$APP_DIR/vps/qlda-api.service" /etc/systemd/system/qlda-api.service; fi
systemctl daemon-reload

systemctl restart "$SERVICE"
systemctl is-enabled --quiet qlda-upload.service 2>/dev/null && systemctl restart qlda-upload.service || true
systemctl is-enabled --quiet qlda-excel-worker.service 2>/dev/null && systemctl restart qlda-excel-worker.service || true
if systemctl is-enabled --quiet qlda-api.service 2>/dev/null; then systemctl restart qlda-api.service || true; fi

sleep 3
"$APP_DIR/vps/healthcheck.sh"
echo "Rolled back to $PREV"
