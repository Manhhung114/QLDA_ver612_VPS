#!/usr/bin/env bash
set -euo pipefail

APP_DIR="${QLDA_APP_DIR:-/opt/qlda/app}"
VENV_DIR="${QLDA_VENV_DIR:-/opt/qlda/venv}"
SHARED_DIR="${QLDA_SHARED_DIR:-/opt/qlda/shared}"
SERVICE="${QLDA_SERVICE:-qlda}"
BRANCH="${QLDA_BRANCH:-main}"
RUN_USER="${QLDA_RUN_USER:-qlda}"

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run with sudo: sudo $0" >&2
  exit 1
fi

run_as_app() {
  runuser -u "$RUN_USER" -- "$@"
}

git_app() {
  run_as_app git -C "$APP_DIR" "$@"
}

mkdir -p "$SHARED_DIR"
cd "$APP_DIR"

git_app fetch --prune origin "$BRANCH"
OLD_COMMIT="$(git_app rev-parse HEAD)"
NEW_COMMIT="$(git_app rev-parse "origin/$BRANCH")"

if [[ "$OLD_COMMIT" == "$NEW_COMMIT" ]]; then
  echo "Already up to date: $NEW_COMMIT"
  exit 0
fi

echo "$OLD_COMMIT" > "$SHARED_DIR/previous_commit"
chown "$RUN_USER:$RUN_USER" "$SHARED_DIR/previous_commit"

git_app reset --hard "$NEW_COMMIT"
run_as_app "$VENV_DIR/bin/python" -m pip install --disable-pip-version-check -r "$APP_DIR/requirements.txt"
run_as_app "$VENV_DIR/bin/python" -m py_compile \
  "$APP_DIR/streamlit_app.py" \
  "$APP_DIR/build_v621_webopt.py" \
  "$APP_DIR/v622_auth_refresh_v4.py" \
  "$APP_DIR/v622_boq_multisheet_patch.py" \
  "$APP_DIR/boq_multisheet_v622.py" \
  "$APP_DIR/postgres_backend_v622.py" \
  "$APP_DIR/vps_postgres_resilience.py" \
  "$APP_DIR/streamlit_secrets_v622.py"

systemctl restart "$SERVICE"

ok=0
for _ in $(seq 1 30); do
  if "$APP_DIR/vps/healthcheck.sh" >/dev/null 2>&1; then
    ok=1
    break
  fi
  sleep 2
done

if [[ "$ok" -eq 1 ]]; then
  echo "Deploy OK: $OLD_COMMIT -> $NEW_COMMIT"
  exit 0
fi

echo "Health check failed. Rolling back to $OLD_COMMIT" >&2
git_app reset --hard "$OLD_COMMIT"
run_as_app "$VENV_DIR/bin/python" -m pip install --disable-pip-version-check -r "$APP_DIR/requirements.txt"
systemctl restart "$SERVICE"
sleep 3
"$APP_DIR/vps/healthcheck.sh"
exit 1
