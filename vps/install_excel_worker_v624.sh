#!/usr/bin/env bash
set -euo pipefail

APP_DIR="${QLDA_APP_DIR:-/opt/qlda/app}"
VENV_DIR="${QLDA_VENV_DIR:-/opt/qlda/venv}"
SHARED_DIR="${QLDA_SHARED_DIR:-/opt/qlda/shared}"
RUN_USER="${QLDA_RUN_USER:-qlda}"

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run with sudo: sudo $0" >&2
  exit 1
fi

if [[ ! -f "$SHARED_DIR/qlda.env" ]]; then
  echo "Missing $SHARED_DIR/qlda.env" >&2
  exit 1
fi

if ! grep -qE '^(DATABASE_URL|QLDA_DATABASE_URL|POSTGRES_URL)=' "$SHARED_DIR/qlda.env"; then
  echo "PostgreSQL URL is missing from qlda.env; Excel worker was not installed." >&2
  exit 1
fi

mkdir -p "$SHARED_DIR/config" /var/log/qlda
chown -R "$RUN_USER:$RUN_USER" "$SHARED_DIR/config" /var/log/qlda

runuser -u "$RUN_USER" -- "$VENV_DIR/bin/python" -m py_compile \
  "$APP_DIR/excel_jobs_v624.py" \
  "$APP_DIR/excel_worker_v624.py"

install -m 0644 "$APP_DIR/vps/qlda-excel-worker.service" /etc/systemd/system/qlda-excel-worker.service
chmod +x "$APP_DIR/vps/install_excel_worker_v624.sh" || true
systemctl daemon-reload
systemctl enable qlda-excel-worker.service >/dev/null 2>&1 || true
systemctl restart qlda-excel-worker.service
sleep 2

if ! systemctl is-active --quiet qlda-excel-worker.service; then
  systemctl --no-pager -l status qlda-excel-worker.service || true
  journalctl -u qlda-excel-worker.service -n 80 --no-pager || true
  exit 1
fi

systemctl --no-pager -l status qlda-excel-worker.service
echo "V6.24 Excel worker installed and running."
