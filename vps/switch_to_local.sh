#!/usr/bin/env bash
set -euo pipefail

APP_DIR="${QLDA_APP_DIR:-/opt/qlda/app}"
VENV_DIR="${QLDA_VENV_DIR:-/opt/qlda/venv}"
SHARED_DIR="${QLDA_SHARED_DIR:-/opt/qlda/shared}"
ENV_FILE="$SHARED_DIR/qlda.env"
DATA_DIR="${QLDA_DATA_DIR:-/opt/qlda/data}"
RUN_USER="${QLDA_RUN_USER:-qlda}"
DB_NAME="${QLDA_LOCAL_DB_NAME:-qlda}"
DOMAIN="qldaxd.id.vn"
FRESH=0

for arg in "$@"; do
  case "$arg" in
    --fresh) FRESH=1 ;;
    --*) echo "Unknown option: $arg" >&2; exit 2 ;;
    *) DOMAIN="$arg" ;;
  esac
done

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run with sudo: sudo bash $0 qldaxd.id.vn --fresh" >&2
  exit 1
fi
if ! id "$RUN_USER" >/dev/null 2>&1; then
  echo "ERROR: Linux user '$RUN_USER' does not exist. Run vps/install.sh first." >&2
  exit 1
fi
if [[ ! -x "$VENV_DIR/bin/python" || ! -f "$APP_DIR/streamlit_app.py" ]]; then
  echo "ERROR: QLDA app/venv not found under /opt/qlda." >&2
  exit 1
fi
if [[ ! -f "$ENV_FILE" ]]; then
  echo "ERROR: $ENV_FILE does not exist." >&2
  exit 1
fi

PUBLIC_BASE="$DOMAIN"
if [[ "$PUBLIC_BASE" != http://* && "$PUBLIC_BASE" != https://* ]]; then
  PUBLIC_BASE="https://$PUBLIC_BASE"
fi
PUBLIC_BASE="${PUBLIC_BASE%/}"

export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y postgresql postgresql-contrib
systemctl enable --now postgresql

# Stop writers before a requested fresh local database reset.
systemctl stop qlda-upload.service >/dev/null 2>&1 || true
if [[ "$FRESH" -eq 1 ]]; then
  systemctl stop qlda.service >/dev/null 2>&1 || true
fi

# Peer authentication over the local Unix socket: Linux qlda -> PostgreSQL qlda.
if ! runuser -u postgres -- psql -Atqc "SELECT 1 FROM pg_roles WHERE rolname='${RUN_USER}'" | grep -qx 1; then
  runuser -u postgres -- createuser --login --no-createdb --no-createrole --no-superuser "$RUN_USER"
fi

if [[ "$FRESH" -eq 1 ]]; then
  runuser -u postgres -- psql -v ON_ERROR_STOP=1 -d postgres -c \
    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname='${DB_NAME}' AND pid <> pg_backend_pid();" >/dev/null
  runuser -u postgres -- dropdb --if-exists "$DB_NAME"
fi
if ! runuser -u postgres -- psql -Atqc "SELECT 1 FROM pg_database WHERE datname='${DB_NAME}'" | grep -qx 1; then
  runuser -u postgres -- createdb --owner="$RUN_USER" "$DB_NAME"
fi
runuser -u postgres -- psql -v ON_ERROR_STOP=1 -d "$DB_NAME" -c "ALTER DATABASE \"${DB_NAME}\" OWNER TO \"${RUN_USER}\";" >/dev/null

mkdir -p "$DATA_DIR/projects" "$DATA_DIR/.tmp" "$DATA_DIR/.trash" /var/log/qlda
chown -R "$RUN_USER:$RUN_USER" "$DATA_DIR" /var/log/qlda
find "$DATA_DIR" -type d -exec chmod 750 {} +
find "$DATA_DIR" -type f -exec chmod 640 {} +

# Preserve existing AI/search settings and secret values. Only local storage/DB
# routing keys are changed; Drive/Neon values may remain in the file but are no
# longer on the live execution path.
BOOTSTRAP_CODE="$(runuser -u "$RUN_USER" -- "$VENV_DIR/bin/python" - <<'PY'
import secrets
print(secrets.token_urlsafe(18))
PY
)"
UPLOAD_SECRET="$(runuser -u "$RUN_USER" -- "$VENV_DIR/bin/python" - <<'PY'
import secrets
print(secrets.token_hex(32))
PY
)"

export QLDA_SWITCH_ENV_FILE="$ENV_FILE"
export QLDA_SWITCH_PUBLIC_BASE="$PUBLIC_BASE"
export QLDA_SWITCH_DATA_DIR="$DATA_DIR"
export QLDA_SWITCH_DB_NAME="$DB_NAME"
export QLDA_SWITCH_BOOTSTRAP_CODE="$BOOTSTRAP_CODE"
export QLDA_SWITCH_UPLOAD_SECRET="$UPLOAD_SECRET"
export QLDA_SWITCH_FRESH="$FRESH"

"$VENV_DIR/bin/python" - <<'PY'
from pathlib import Path
import os

path = Path(os.environ["QLDA_SWITCH_ENV_FILE"])
text = path.read_text(encoding="utf-8") if path.exists() else ""
lines = text.splitlines()
current = {}
for line in lines:
    s = line.strip()
    if not s or s.startswith("#") or "=" not in s:
        continue
    k, v = s.split("=", 1)
    current[k.strip()] = v.strip()

fresh = os.environ.get("QLDA_SWITCH_FRESH") == "1"
bootstrap = current.get("QLDA_LOCAL_BOOTSTRAP_CODE", "") or os.environ["QLDA_SWITCH_BOOTSTRAP_CODE"]
upload_secret = current.get("QLDA_LOCAL_UPLOAD_SECRET", "") or os.environ["QLDA_SWITCH_UPLOAD_SECRET"]
if fresh:
    # A fresh database needs a known bootstrap code; reuse an existing valid one
    # if present so rerunning the switch remains deterministic.
    bootstrap = current.get("QLDA_LOCAL_BOOTSTRAP_CODE", "") or bootstrap

updates = {
    "DATABASE_URL": f"postgresql:///{os.environ['QLDA_SWITCH_DB_NAME']}?host=/var/run/postgresql",
    "QLDA_STORAGE_BACKEND": "local",
    "QLDA_LOCAL_STORAGE_ROOT": os.environ["QLDA_SWITCH_DATA_DIR"],
    "QLDA_LOCAL_TRASH_ROOT": os.environ["QLDA_SWITCH_DATA_DIR"] + "/.trash",
    "QLDA_PUBLIC_BASE_URL": os.environ["QLDA_SWITCH_PUBLIC_BASE"],
    "QLDA_LOCAL_UPLOAD_SECRET": upload_secret,
    "QLDA_LOCAL_BOOTSTRAP_CODE": bootstrap,
    "QLDA_LOCAL_LEGACY_MAX_UPLOAD_MB": "200",
    "QLDA_LOCAL_DIRECT_MAX_UPLOAD_MB": "2048",
    "QLDA_LOCAL_SESSION_TTL_HOURS": "12",
    "QLDA_LOCAL_FILE_HOST": "127.0.0.1",
    "QLDA_LOCAL_FILE_PORT": "8502",
    "QLDA_DRIVE_ENFORCE_RBAC": "true",
}

seen = set()
out = []
for line in lines:
    stripped = line.strip()
    if stripped and not stripped.startswith("#") and "=" in stripped:
        key = stripped.split("=", 1)[0].strip()
        if key in updates:
            if key not in seen:
                out.append(f"{key}={updates[key]}")
                seen.add(key)
            continue
    out.append(line)

if out and out[-1].strip():
    out.append("")
out.append("# VPS local-first storage/auth (managed by vps/switch_to_local.sh)")
for key, value in updates.items():
    if key not in seen:
        out.append(f"{key}={value}")
path.write_text("\n".join(out).rstrip() + "\n", encoding="utf-8")
print(bootstrap)
PY

# Read the effective bootstrap code without printing the rest of qlda.env.
BOOTSTRAP_CODE="$(grep -m1 '^QLDA_LOCAL_BOOTSTRAP_CODE=' "$ENV_FILE" | cut -d= -f2-)"
chown "$RUN_USER:$RUN_USER" "$ENV_FILE"
chmod 600 "$ENV_FILE"

# Compile the new local backend before touching systemd/nginx.
runuser -u "$RUN_USER" -- "$VENV_DIR/bin/python" -m py_compile \
  "$APP_DIR/local_vps_backend_v622.py" \
  "$APP_DIR/local_file_server_v622.py" \
  "$APP_DIR/local_vps_runtime_fix_v622.py" \
  "$APP_DIR/v622_local_vps_patch.py" \
  "$APP_DIR/drive_gateway.py" \
  "$APP_DIR/streamlit_app.py"

install -m 0644 "$APP_DIR/vps/qlda-upload.service" /etc/systemd/system/qlda-upload.service

# Preserve the live Certbot HTTPS config. Inject only the localhost file-service
# location into the existing qlda site instead of replacing the whole file.
NGINX_SITE="/etc/nginx/sites-available/qlda"
if [[ ! -f "$NGINX_SITE" ]]; then
  sed "s/__QLDA_DOMAIN__/${DOMAIN#https://}/g; s/__QLDA_DOMAIN__/${DOMAIN#http://}/g" \
    "$APP_DIR/vps/nginx.conf.template" > "$NGINX_SITE"
fi
export QLDA_SWITCH_NGINX_SITE="$NGINX_SITE"
"$VENV_DIR/bin/python" - <<'PY'
from pathlib import Path
import os

path = Path(os.environ["QLDA_SWITCH_NGINX_SITE"])
text = path.read_text(encoding="utf-8")
if "location /qlda-files/" not in text:
    block = '''    # QLDA signed local VPS file/upload service\n    location /qlda-files/ {\n        proxy_pass http://127.0.0.1:8502/;\n        proxy_http_version 1.1;\n        proxy_set_header Host $host;\n        proxy_set_header X-Real-IP $remote_addr;\n        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;\n        proxy_set_header X-Forwarded-Proto $scheme;\n        proxy_request_buffering off;\n        proxy_buffering off;\n        proxy_read_timeout 3600;\n        proxy_send_timeout 3600;\n    }\n\n'''
    anchor = "    location / {"
    if anchor not in text:
        raise SystemExit("Cannot find nginx location / anchor")
    text = text.replace(anchor, block + anchor, 1)
if "client_max_body_size" not in text:
    anchor = "server {"
    text = text.replace(anchor, anchor + "\n    client_max_body_size 2048M;", 1)
path.write_text(text, encoding="utf-8")
PY

nginx -t
systemctl daemon-reload
systemctl enable qlda-upload.service qlda.service nginx postgresql >/dev/null

# Initialize local auth/file tables using exactly the same env as the services.
runuser -u "$RUN_USER" -- bash -lc '
  set -a
  source /opt/qlda/shared/qlda.env
  set +a
  cd /opt/qlda/app
  /opt/qlda/venv/bin/python - <<"PY"
from local_vps_backend_v622 import health
h = health()
print("Local backend OK; initialized=", bool(h.get("initialized")), "; free_GB=", round(int(h.get("disk_free", 0))/1024**3, 1))
PY
'

systemctl restart qlda-upload.service
systemctl restart qlda.service
systemctl reload nginx

ok_upload=0
for _ in $(seq 1 20); do
  if curl -fsS http://127.0.0.1:8502/health >/dev/null 2>&1; then ok_upload=1; break; fi
  sleep 1
done
if [[ "$ok_upload" -ne 1 ]]; then
  echo "ERROR: local file service health check failed." >&2
  systemctl status qlda-upload.service --no-pager >&2 || true
  exit 1
fi

ok_app=0
for _ in $(seq 1 30); do
  if curl -fsS http://127.0.0.1:8501/_stcore/health >/dev/null 2>&1; then ok_app=1; break; fi
  sleep 2
done
if [[ "$ok_app" -ne 1 ]]; then
  echo "ERROR: QLDA app health check failed." >&2
  systemctl status qlda.service --no-pager >&2 || true
  exit 1
fi

printf '\n=== QLDA LOCAL VPS READY ===\n'
printf 'Database      : PostgreSQL local / %s\n' "$DB_NAME"
printf 'File storage  : %s\n' "$DATA_DIR"
printf 'File service  : http://127.0.0.1:8502 (localhost only)\n'
printf 'Public app    : %s\n' "$PUBLIC_BASE"
printf 'Neon/Drive    : NOT used by live storage/auth path\n'
printf '\nMã khởi tạo Admin lần đầu: %s\n' "$BOOTSTRAP_CODE"
printf 'Hãy giữ mã này riêng tư; không gửi nội dung qlda.env vào chat.\n'
