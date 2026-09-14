#!/usr/bin/env bash
set -euo pipefail

APP_DIR="${QLDA_APP_DIR:-/opt/qlda/app}"
VENV_DIR="${QLDA_VENV_DIR:-/opt/qlda/venv}"
SHARED_DIR="${QLDA_SHARED_DIR:-/opt/qlda/shared}"
RUN_USER="${QLDA_RUN_USER:-qlda}"
MAIN_SERVICE="${QLDA_SERVICE:-qlda}"

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
  "$APP_DIR/excel_worker_v624.py" \
  "$APP_DIR/boq_background_v624.py" \
  "$APP_DIR/boq_persist_v624.py" \
  "$APP_DIR/excel_background_v624.py" \
  "$APP_DIR/local_file_server_background.py" \
  "$APP_DIR/v624_excel_background_patch.py" \
  "$APP_DIR/streamlit_app.py"

# Validate the generated Streamlit source before touching running services.
runuser -u "$RUN_USER" -- env \
  HOME="$SHARED_DIR" \
  QLDA_SETTINGS_DIR="$SHARED_DIR/config" \
  "$VENV_DIR/bin/python" "$APP_DIR/build_v621_webopt.py" >/dev/null

install -m 0644 "$APP_DIR/vps/qlda-excel-worker.service" /etc/systemd/system/qlda-excel-worker.service

# Direct-upload BOQ queue hand-off is part of V6.24.2. Update the upload unit
# when the local VPS storage backend is enabled.
LOCAL_STORAGE=0
if grep -qE '^QLDA_STORAGE_BACKEND[[:space:]]*=[[:space:]]*local[[:space:]]*$' "$SHARED_DIR/qlda.env"; then
  LOCAL_STORAGE=1
  install -m 0644 "$APP_DIR/vps/qlda-upload.service" /etc/systemd/system/qlda-upload.service
fi

chmod +x "$APP_DIR/vps/install_excel_worker_v624.sh" || true
systemctl daemon-reload
systemctl enable qlda-excel-worker.service >/dev/null 2>&1 || true
if [[ "$LOCAL_STORAGE" -eq 1 ]]; then
  systemctl enable qlda-upload.service >/dev/null 2>&1 || true
  systemctl restart qlda-upload.service
fi
systemctl restart qlda-excel-worker.service
# Restart Streamlit so the new BOQ background panel is compiled into the app.
systemctl restart "$MAIN_SERVICE"
sleep 3

if ! systemctl is-active --quiet qlda-excel-worker.service; then
  systemctl --no-pager -l status qlda-excel-worker.service || true
  journalctl -u qlda-excel-worker.service -n 100 --no-pager || true
  exit 1
fi

if ! curl -fsS http://127.0.0.1:8501/_stcore/health >/dev/null 2>&1; then
  echo "Main Streamlit health check failed." >&2
  systemctl --no-pager -l status "$MAIN_SERVICE" || true
  journalctl -u "$MAIN_SERVICE" -n 100 --no-pager || true
  exit 1
fi

if [[ "$LOCAL_STORAGE" -eq 1 ]]; then
  if ! systemctl is-active --quiet qlda-upload.service; then
    systemctl --no-pager -l status qlda-upload.service || true
    journalctl -u qlda-upload.service -n 100 --no-pager || true
    exit 1
  fi
  if ! curl -fsS http://127.0.0.1:8502/health >/dev/null 2>&1; then
    echo "Local upload service health check failed." >&2
    exit 1
  fi
fi

systemctl --no-pager -l status qlda-excel-worker.service
echo "V6.24.2 BOQ background worker/upload stack installed and running."
