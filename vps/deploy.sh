#!/usr/bin/env bash
set -euo pipefail

APP_DIR="${QLDA_APP_DIR:-/opt/qlda/app}"
VENV_DIR="${QLDA_VENV_DIR:-/opt/qlda/venv}"
SHARED_DIR="${QLDA_SHARED_DIR:-/opt/qlda/shared}"
SERVICE="${QLDA_SERVICE:-qlda}"
BRANCH="${QLDA_BRANCH:-main}"
RUN_USER="${QLDA_RUN_USER:-qlda}"
GIT_FETCH_RETRIES="${QLDA_GIT_FETCH_RETRIES:-3}"
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

git_fetch_command() {
  local cmd=(
    runuser -u "$RUN_USER" --
    env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u ALL_PROXY -u all_proxy
    git -C "$APP_DIR" -c http.version=HTTP/1.1 -c http.proxy=
  )
  cmd+=(fetch --prune origin "+refs/heads/${BRANCH}:refs/remotes/origin/${BRANCH}")
  if command -v timeout >/dev/null 2>&1; then
    timeout "${GIT_FETCH_TIMEOUT}s" "${cmd[@]}"
  else
    "${cmd[@]}"
  fi
}

fetch_with_temporary_hosts_pin() {
  local ip="$1"
  local backup tmp rc=1
  backup="$(mktemp)"
  tmp="$(mktemp)"
  cp /etc/hosts "$backup"
  awk '!($2 == "github.com" || $3 == "github.com") {print}' "$backup" > "$tmp"
  printf '%s\tgithub.com\t# QLDA_TEMP_GITHUB_IPV4\n' "$ip" >> "$tmp"
  cat "$tmp" > /etc/hosts
  echo "Retrying GitHub fetch with temporary /etc/hosts pin: github.com -> $ip"
  if git_fetch_command; then rc=0; fi
  cat "$backup" > /etc/hosts
  rm -f "$backup" "$tmp"
  return "$rc"
}

fetch_origin_resilient() {
  local attempt ip wait_s
  for attempt in $(seq 1 "$GIT_FETCH_RETRIES"); do
    ip="$(github_ipv4 || true)"
    echo "GitHub fetch attempt ${attempt}/${GIT_FETCH_RETRIES} (HTTP/1.1, proxies disabled)..."
    if [[ -n "$ip" ]]; then
      curl -4 -fsSI --connect-timeout 8 --max-time 15 --resolve "github.com:443:${ip}" https://github.com/ >/dev/null 2>&1 || echo "Warning: direct GitHub IPv4 HTTPS preflight failed."
    fi
    if git_fetch_command; then return 0; fi
    wait_s=$((attempt * 3))
    echo "GitHub fetch attempt ${attempt} failed; retrying in ${wait_s}s..." >&2
    sleep "$wait_s"
  done
  ip="$(github_ipv4 || true)"
  if [[ -n "$ip" ]] && curl -4 -fsSI --connect-timeout 8 --max-time 15 --resolve "github.com:443:${ip}" https://github.com/ >/dev/null 2>&1; then
    if fetch_with_temporary_hosts_pin "$ip"; then return 0; fi
  fi
  echo "ERROR: VPS cannot fetch GitHub." >&2
  echo "Diagnostics (no secrets):" >&2
  getent ahostsv4 github.com >&2 || true
  runuser -u "$RUN_USER" -- env | grep -iE '(^|_)(http|https|all)_proxy=' >&2 || true
  git_app config --show-origin --get-regexp '^(http|https)\.' >&2 || true
  curl -4 -I --connect-timeout 8 --max-time 15 https://github.com/ >&2 || true
  ip route >&2 || true
  return 1
}

local_storage_enabled() {
  [[ -f "$SHARED_DIR/qlda.env" ]] && grep -qE '^QLDA_STORAGE_BACKEND[[:space:]]*=[[:space:]]*local[[:space:]]*$' "$SHARED_DIR/qlda.env"
}

restart_local_file_service_if_enabled() {
  if local_storage_enabled; then
    install -m 0644 "$APP_DIR/vps/qlda-upload.service" /etc/systemd/system/qlda-upload.service
    systemctl daemon-reload
    systemctl enable qlda-upload.service >/dev/null 2>&1 || true
    systemctl restart qlda-upload.service
  fi
}

mkdir -p "$SHARED_DIR"
cd "$APP_DIR"

fetch_origin_resilient
OLD_COMMIT="$(git_app rev-parse HEAD)"
REMOTE_REF="refs/remotes/origin/${BRANCH}"
if ! git_app show-ref --verify --quiet "$REMOTE_REF"; then
  echo "ERROR: fetch completed but $REMOTE_REF does not exist." >&2
  exit 1
fi
NEW_COMMIT="$(git_app rev-parse --verify "${REMOTE_REF}^{commit}")"

echo "Local HEAD : $OLD_COMMIT"
echo "Remote HEAD: $NEW_COMMIT"

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
  "$APP_DIR/boq_cost_components_v622.py" \
  "$APP_DIR/boq_claim_terms_v622.py" \
  "$APP_DIR/boq_claim_price_recovery_v622.py" \
  "$APP_DIR/boq_persistence_v622.py" \
  "$APP_DIR/v622_ipc_claim_patch.py" \
  "$APP_DIR/ipc_claim_v622.py" \
  "$APP_DIR/ipc_claim_fast_v622.py" \
  "$APP_DIR/ipc_claim_summary_fix_v622.py" \
  "$APP_DIR/ipc_adaptive_parser_v622.py" \
  "$APP_DIR/ipc_claim_number_fix_v622.py" \
  "$APP_DIR/ipc_claim_period_v622.py" \
  "$APP_DIR/ipc_claim_delete_v622.py" \
  "$APP_DIR/multicore_excel_v622.py" \
  "$APP_DIR/v622_vo_claim_patch.py" \
  "$APP_DIR/vo_claim_v622.py" \
  "$APP_DIR/v622_report_cost_patch.py" \
  "$APP_DIR/report_cost_v622.py" \
  "$APP_DIR/gemini_resilience_v622.py" \
  "$APP_DIR/ai_live_context_v622.py" \
  "$APP_DIR/ai_claim_context_v622.py" \
  "$APP_DIR/ai_vo_context_v622.py" \
  "$APP_DIR/postgres_backend_v622.py" \
  "$APP_DIR/vps_postgres_resilience.py" \
  "$APP_DIR/streamlit_secrets_v622.py" \
  "$APP_DIR/drive_gateway.py" \
  "$APP_DIR/local_vps_backend_v622.py" \
  "$APP_DIR/local_file_server_v622.py" \
  "$APP_DIR/local_vps_runtime_fix_v622.py" \
  "$APP_DIR/v622_local_vps_patch.py"

run_as_app "$VENV_DIR/bin/python" - <<'PY'
from multicore_excel_v622 import runtime_config
cfg = runtime_config()
print("QLDA multicore:", cfg)
assert cfg["cpu_count"] >= 1
assert cfg["child_workers"] >= 1
PY

restart_local_file_service_if_enabled
systemctl restart "$SERVICE"

ok=0
for _ in $(seq 1 30); do
  if "$APP_DIR/vps/healthcheck.sh" >/dev/null 2>&1; then
    ok=1
    break
  fi
  sleep 2
done

if [[ "$ok" -eq 1 ]] && local_storage_enabled; then
  if ! curl -fsS http://127.0.0.1:8502/health >/dev/null 2>&1; then
    echo "Local file service health check failed." >&2
    ok=0
  fi
fi

if [[ "$ok" -eq 1 ]]; then
  echo "Deploy OK: $OLD_COMMIT -> $NEW_COMMIT"
  exit 0
fi

echo "Health check failed. Rolling back to $OLD_COMMIT" >&2
git_app reset --hard "$OLD_COMMIT"
run_as_app "$VENV_DIR/bin/python" -m pip install --disable-pip-version-check -r "$APP_DIR/requirements.txt"
restart_local_file_service_if_enabled || true
systemctl restart "$SERVICE"
sleep 3
"$APP_DIR/vps/healthcheck.sh"
exit 1