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

run_as_app() { runuser -u "$RUN_USER" -- "$@"; }
git_app() { run_as_app git -C "$APP_DIR" "$@"; }

# V7.6 final-conversion safety boundary: rollback may only target a commit that
# already contains the packaged/native runtime. Never resurrect a V6 root runtime.
validate_packaged_target() {
  local target="$1" version
  git_app cat-file -e "${target}^{commit}"
  for path in \
    VERSION.txt \
    src/qlda/__init__.py \
    src/qlda/presentation/streamlit/app.py \
    src/qlda/presentation/api/app.py \
    src/qlda/modules/excel/worker.py \
    vps/qlda.service \
    vps/qlda-api.service \
    vps/qlda-excel-worker.service; do
    if ! git_app cat-file -e "${target}:${path}" 2>/dev/null; then
      echo "Refusing rollback: $target is not a packaged V7.6 runtime (missing $path)." >&2
      return 1
    fi
  done
  version="$(git_app show "${target}:VERSION.txt" | tr -d '[:space:]')"
  if [[ "$version" != "7.6" ]]; then
    echo "Refusing rollback: target version '$version' is outside the V7.6 final-conversion boundary." >&2
    return 1
  fi
}

validate_packaged_target "$PREV"
git_app reset --hard "$PREV"
run_as_app "$VENV_DIR/bin/python" -m pip install --disable-pip-version-check -r "$APP_DIR/requirements.txt"

# Validate the checked-out package before touching systemd.
run_as_app env PYTHONPATH="$APP_DIR/src" "$VENV_DIR/bin/python" -m compileall -q "$APP_DIR/src/qlda"
run_as_app env PYTHONPATH="$APP_DIR/src" "$VENV_DIR/bin/python" - <<'PY'
import qlda
from qlda.bootstrap import get_application
assert qlda.__version__ == "7.6"
assert qlda.LEGACY_ADAPTERS == ()
assert qlda.LEGACY_RUNTIME is False
assert qlda.STREAMLIT_ENTRYPOINT == "qlda.presentation.streamlit.app"
get_application()
print("QLDA V7.6 rollback target validated")
PY

install -m 0644 "$APP_DIR/vps/qlda.service" /etc/systemd/system/qlda.service
if [[ -f "$APP_DIR/vps/qlda-upload.service" ]]; then install -m 0644 "$APP_DIR/vps/qlda-upload.service" /etc/systemd/system/qlda-upload.service; fi
if [[ -f "$APP_DIR/vps/qlda-excel-worker.service" ]]; then install -m 0644 "$APP_DIR/vps/qlda-excel-worker.service" /etc/systemd/system/qlda-excel-worker.service; fi
if [[ -f "$APP_DIR/vps/qlda-api.service" ]]; then install -m 0644 "$APP_DIR/vps/qlda-api.service" /etc/systemd/system/qlda-api.service; fi
if [[ -f "$APP_DIR/vps/qlda-contractor-data-worker.service" ]]; then
  install -m 0644 "$APP_DIR/vps/qlda-contractor-data-worker.service" /etc/systemd/system/qlda-contractor-data-worker.service
else
  # The unit may have been installed by a newer commit. Do not leave it trying
  # to start a Python module that does not exist in the rollback target.
  systemctl disable --now qlda-contractor-data-worker.service >/dev/null 2>&1 || true
  rm -f /etc/systemd/system/qlda-contractor-data-worker.service
fi
systemctl daemon-reload

systemctl restart "$SERVICE"
systemctl is-enabled --quiet qlda-upload.service 2>/dev/null && systemctl restart qlda-upload.service || true
systemctl is-enabled --quiet qlda-excel-worker.service 2>/dev/null && systemctl restart qlda-excel-worker.service || true
if systemctl is-enabled --quiet qlda-api.service 2>/dev/null; then systemctl restart qlda-api.service || true; fi
if [[ -f "$APP_DIR/vps/qlda-contractor-data-worker.service" ]] && systemctl is-enabled --quiet qlda-contractor-data-worker.service 2>/dev/null; then
  systemctl restart qlda-contractor-data-worker.service || true
fi

ok=0
for _ in $(seq 1 30); do
  if "$APP_DIR/vps/healthcheck.sh" >/dev/null 2>&1; then ok=1; break; fi
  sleep 2
done
if [[ "$ok" -ne 1 ]]; then
  echo "Rollback target failed Streamlit health check." >&2
  systemctl --no-pager -l status "$SERVICE" || true
  journalctl -u "$SERVICE" -n 100 --no-pager || true
  exit 1
fi

echo "Rolled back safely to packaged V7.6 commit $PREV"
