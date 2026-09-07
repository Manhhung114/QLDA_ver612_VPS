#!/usr/bin/env bash
set -euo pipefail

REPO_URL="${QLDA_REPO_URL:-https://github.com/Manhhung114/QLDA_ver612_VPS.git}"
DOMAIN="${1:-_}"
APP_ROOT="/opt/qlda"
APP_DIR="$APP_ROOT/app"
VENV_DIR="$APP_ROOT/venv"
SHARED_DIR="$APP_ROOT/shared"
RUN_USER="qlda"

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run with sudo: sudo bash vps/install.sh your-domain.example" >&2
  exit 1
fi

apt-get update
DEBIAN_FRONTEND=noninteractive apt-get install -y \
  git curl rsync nginx default-jre-headless build-essential \
  python3 python3-venv python3-pip ca-certificates \
  certbot python3-certbot-nginx

if ! id "$RUN_USER" >/dev/null 2>&1; then
  useradd --system --create-home --home-dir /home/qlda --shell /bin/bash "$RUN_USER"
fi

mkdir -p "$APP_ROOT" "$SHARED_DIR" /var/log/qlda

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
sed "s/__QLDA_DOMAIN__/$DOMAIN/g" "$APP_DIR/vps/nginx.conf.template" > /etc/nginx/sites-available/qlda
ln -sfn /etc/nginx/sites-available/qlda /etc/nginx/sites-enabled/qlda
rm -f /etc/nginx/sites-enabled/default

nginx -t
systemctl daemon-reload
systemctl enable nginx qlda
systemctl restart nginx

echo
echo "QLDA VPS base installation complete."
echo "1) Edit secrets: sudo nano $SHARED_DIR/qlda.env"
echo "2) Start app:    sudo systemctl restart qlda"
echo "3) App status:   sudo systemctl status qlda --no-pager"
echo "4) App logs:     sudo journalctl -u qlda -f"
echo "5) Health:       sudo $APP_DIR/vps/healthcheck.sh"
if [[ "$DOMAIN" != "_" ]]; then
  echo "6) HTTPS after DNS points to this VPS: sudo certbot --nginx -d $DOMAIN"
fi
