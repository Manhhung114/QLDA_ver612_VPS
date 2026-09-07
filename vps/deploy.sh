#!/usr/bin/env bash
set -euo pipefail

APP_DIR="${QLDA_APP_DIR:-/opt/qlda/app}"
VENV_DIR="${QLDA_VENV_DIR:-/opt/qlda/venv}"
SHARED_DIR="${QLDA_SHARED_DIR:-/opt/qlda/shared}"
SERVICE="${QLDA_SERVICE:-qlda}"
BRANCH="${QLDA_BRANCH:-main}"
RUN_USER="${QLDA_RUN_USER:-qlda}"
GIT_FETCH_RETRIES="${QLDA_GIT_FETCH_RETRIES:-4}"
GIT_FETCH_TIMEOUT="${QLDA_GIT_FETCH_TIMEOUT:-45}"

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

github_ipv4() {
  getent ahostsv4 github.com 2>/dev/null | awk '$2 == "STREAM" {print $1; exit}'
}

git_fetch_once() {
  local ip="${1:-}"
  local cmd=(runuser -u "$RUN_USER" -- git -C "$APP_DIR" -c http.version=HTTP/1.1)
  if [[ -n "$ip" ]]; then
    cmd+=( -c "http.curloptResolve=github.com:443:${ip}" )
  fi
  cmd+=(fetch --prune origin "$BRANCH")

  if command -v timeout >/dev/null 2>&1; then
    timeout "${GIT_FETCH_TIMEOUT}s" "${cmd[@]}"
  else
    "${cmd[@]}"
  fi
}

fetch_origin_resilient() {
  local attempt ip wait_s
  for attempt in $(seq 1 "$GIT_FETCH_RETRIES"); do
    ip="$(github_ipv4 || true)"
    if [[ -n "$ip" ]]; then
      echo "GitHub fetch attempt ${attempt}/${GIT_FETCH_RETRIES} via IPv4 ${ip} (HTTP/1.1)..."
      curl -4 -fsSI --connect-timeout 8 --max-time 15 https://github.com/ >/dev/null 2>&1 || \
        echo "Warning: GitHub HTTPS preflight failed; fetch will still be attempted."
    else
      echo "GitHub fetch attempt ${attempt}/${GIT_FETCH_RETRIES} (IPv4 DNS not resolved; HTTP/1.1 fallback)..."
    fi

    if git_fetch_once "$ip"; then
      return 0
    fi

    wait_s=$((attempt * 3))
    echo "GitHub fetch attempt ${attempt} failed; retrying in ${wait_s}s..." >&2
    sleep "$wait_s"
  done

  echo "ERROR: VPS cannot fetch GitHub after ${GIT_FETCH_RETRIES} attempts." >&2
  echo "Diagnostics (no secrets):" >&2
  getent ahostsv4 github.com >&2 || true
  curl -4 -I --connect-timeout 8 --max-time 15 https://github.com/ >&2 || true
  ip route >&2 || true
  return 1
}

mkdir -p "$SHARED_DIR"
cd "$APP_DIR"

fetch_origin_resilient
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
