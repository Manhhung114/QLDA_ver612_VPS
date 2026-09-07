#!/usr/bin/env bash
set -euo pipefail
URL="${1:-http://127.0.0.1:8501/_stcore/health}"
curl --fail --silent --show-error --max-time 8 "$URL"
echo
