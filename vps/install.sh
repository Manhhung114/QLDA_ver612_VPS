#!/usr/bin/env bash
set -euo pipefail

REPO_URL="${QLDA_REPO_URL:-https://github.com/Manhhung114/QLDA_ver612_VPS.git}"
DOMAIN="${1:-_}"
APP_ROOT="/opt/qlda"
APP_DIR="$APP_ROOT/app"
VENV_DIR="$APP_ROOT/venv"
SHARED_DIR="$APP_ROOT/shared"
DATA_DIR="$APP_ROOT/data"
RUN_USER="qlda"

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run with sudo: sudo bash vps/install.sh your-domain.example" >&2
  exit 1
fi

apt-get update
DEBIAN_FRONTEND=noninteractive apt-get install -y \
  git curl rsync nginx build-essential \
  python3 python3-venv python3-pip ca-certificates \
  postgresql postgresql-contrib \
  certbot python3-certbot-nginx

if ! id "$RUN_USER" >/dev/null 2>&1; then
  useradd --system --create-home --home-dir /home/qlda --shell /bin/bash "$RUN_USER"
fi

mkdir -p "$APP_ROOT" "$SHARED_DIR" "$DATA_DIR/projects" "$DATA_DIR/.tmp" "$DATA_DIR/.trash" /var/log/qlda

if [[ ! -d "$APP_DIR/.git" ]]; then
  rm -rf "$APP_DIR"
  git clone "$REPO_URL" "$APP_DIR"
else
  git -C "$APP_DIR" remote set-url origin "$REPO_URL"
  git -C "$APP_DIR" fetch origin main
  git -C "$APP_DIR" reset --hard origin/main
fi

if [[ ! -x "$VENV_DIR/bin/python" ]]; then
  python3 -m venv "$VENV_DIR"
fi

chown -R "$RUN_USER:$RUN_USER" "$APP_ROOT" /var/log/qlda
find "$DATA_DIR" -type d -exec chmod 750 {} +

runuser -u "$RUN_USER" -- "$VENV_DIR/bin/python" -m pip install --upgrade pip wheel setuptools
runuser -u "$RUN_USER" -- "$VENV_DIR/bin/python" -m pip install -r "$APP_DIR/requirements.txt"

if [[ ! -f "$SHARED_DIR/qlda.env" ]]; then
  cp "$APP_DIR/.env.example" "$SHARED_DIR/qlda.env"
  chown "$RUN_USER:$RUN_USER" "$SHARED_DIR/qlda.env"
  chmod 600 "$SHARED_DIR/qlda.env"
  echo "Created $SHARED_DIR/qlda.env"
fi

chmod +x "$APP_DIR/vps/"*.sh
install -m 0644 "$APP_DIR/vps/qlda.service" /etc/systemd/system/qlda.service
install -m 0644 "$APP_DIR/vps/qlda-upload.service" /etc/systemd/system/qlda-upload.service
sed "s/__QLDA_DOMAIN__/$DOMAIN/g" "$APP_DIR/vps/nginx.conf.template" > /etc/nginx/sites-available/qlda
ln -sfn /etc/nginx/sites-available/qlda /etc/nginx/sites-enabled/qlda
rm -f /etc/nginx/sites-enabled/default

nginx -t
systemctl daemon-reload
systemctl enable nginx qlda postgresql
systemctl restart nginx postgresql

echo
echo "QLDA VPS base installation complete."
echo "1) Switch fresh LIVE data to local VPS: sudo bash $APP_DIR/vps/switch_to_local.sh $DOMAIN --fresh"
echo "2) Edit AI/API secrets if needed: sudo nano $SHARED_DIR/qlda.env"
echo "3) App status:   sudo systemctl status qlda --no-pager"
echo "4) File status:  sudo systemctl status qlda-upload --no-pager"
echo "5) App logs:     sudo journalctl -u qlda -f"
echo "6) Health:       sudo $APP_DIR/vps/healthcheck.sh"
if [[ "$DOMAIN" != "_" ]]; then
  echo "7) HTTPS after DNS points to this VPS: sudo certbot --nginx -d $DOMAIN"
fi
