#!/usr/bin/env bash
set -euo pipefail

APP_DIR="${QLDA_APP_DIR:-/opt/qlda/app}"
VENV_DIR="${QLDA_VENV_DIR:-/opt/qlda/venv}"
SHARED_DIR="${QLDA_SHARED_DIR:-/opt/qlda/shared}"
NGINX_SITE="${QLDA_NGINX_SITE:-/etc/nginx/sites-available/qlda}"

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run with sudo/root: $0" >&2
  exit 1
fi

if [[ ! -f "$SHARED_DIR/qlda.env" ]]; then
  echo "Skip FastAPI reconcile: $SHARED_DIR/qlda.env is missing."
  exit 0
fi
if ! grep -qE '^(DATABASE_URL|QLDA_DATABASE_URL|POSTGRES_URL)=' "$SHARED_DIR/qlda.env"; then
  echo "Skip FastAPI reconcile: PostgreSQL is not configured."
  exit 0
fi
if ! grep -qE '^QLDA_STORAGE_BACKEND[[:space:]]*=[[:space:]]*local[[:space:]]*$' "$SHARED_DIR/qlda.env"; then
  echo "Skip FastAPI reconcile: local VPS backend is not enabled."
  exit 0
fi

install -m 0644 "$APP_DIR/vps/qlda-api.service" /etc/systemd/system/qlda-api.service

# Preserve Certbot-managed HTTPS lines and inject only the /api/ location.
if [[ -f "$NGINX_SITE" ]] && ! grep -q 'location /api/' "$NGINX_SITE"; then
  export QLDA_V627_NGINX_SITE="$NGINX_SITE"
  "$VENV_DIR/bin/python" - <<'PY'
from pathlib import Path
import os

path = Path(os.environ["QLDA_V627_NGINX_SITE"])
text = path.read_text(encoding="utf-8")
block = '''    # QLDA V6.27 FastAPI - internal localhost service
    location /api/ {
        proxy_pass http://127.0.0.1:8001;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 3600;
        proxy_send_timeout 3600;
        proxy_buffering off;
    }

'''
anchor = "    location / {"
if anchor not in text:
    raise SystemExit("Cannot find nginx location / anchor for FastAPI.")
path.write_text(text.replace(anchor, block + anchor, 1), encoding="utf-8")
PY
fi

nginx -t
systemctl daemon-reload
systemctl enable qlda-api.service >/dev/null 2>&1 || true
systemctl restart qlda-api.service
systemctl reload nginx

ok=0
for _ in $(seq 1 30); do
  if curl -fsS http://127.0.0.1:8001/api/health >/dev/null 2>&1; then
    ok=1
    break
  fi
  sleep 1
done
if [[ "$ok" -ne 1 ]]; then
  echo "QLDA FastAPI health check failed." >&2
  systemctl --no-pager -l status qlda-api.service >&2 || true
  exit 1
fi
echo "QLDA V6.27 FastAPI ready on 127.0.0.1:8001."
