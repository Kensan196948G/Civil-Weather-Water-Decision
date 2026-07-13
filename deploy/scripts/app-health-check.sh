#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'USAGE'
Usage: app-health-check.sh [options]

Checks production app health without credentials:
  --backend-health-url URL   default http://127.0.0.1:55019/health
  --backend-ready-url URL    default http://127.0.0.1:55019/readyz
  --frontend-url URL         default http://127.0.0.1:34979/
  --frontend-api-url URL     default http://127.0.0.1:34979/api/auth/me
  --public-url URL           default https://cwwd.mirai-dx-platform.com/
  --public-statuses CSV      default 302
  --timeout-seconds N        default 10
USAGE
}

BACKEND_HEALTH_URL="${CWWD_BACKEND_HEALTH_URL:-http://127.0.0.1:55019/health}"
BACKEND_READY_URL="${CWWD_BACKEND_READY_URL:-http://127.0.0.1:55019/readyz}"
FRONTEND_URL="${CWWD_FRONTEND_URL:-http://127.0.0.1:34979/}"
FRONTEND_API_URL="${CWWD_FRONTEND_API_URL:-http://127.0.0.1:34979/api/auth/me}"
PUBLIC_URL="${CWWD_PUBLIC_URL:-https://cwwd.mirai-dx-platform.com/}"
PUBLIC_STATUSES="${CWWD_PUBLIC_STATUSES:-302}"
TIMEOUT_SECONDS="${CWWD_HEALTH_TIMEOUT_SECONDS:-10}"
TMP_FILES=()

fail() {
  echo "$1" >&2
  exit "${2:-2}"
}

cleanup() {
  local path
  for path in "${TMP_FILES[@]}"; do
    [[ -n "$path" ]] && rm -f "$path"
  done
  true
}
trap cleanup EXIT

is_positive_int() {
  [[ "$1" =~ ^[0-9]+$ && "$1" != "0" ]]
}

new_tmp() {
  local path
  path="$(mktemp)"
  TMP_FILES+=("$path")
  printf '%s' "$path"
}

http_get() {
  local url="$1"
  local body="$2"
  local headers="$3"
  curl --silent --show-error --location-trusted --max-redirs 0 \
    --connect-timeout "$TIMEOUT_SECONDS" --max-time "$TIMEOUT_SECONDS" \
    --output "$body" --dump-header "$headers" --write-out '%{http_code}' "$url"
}

http_status_only() {
  local url="$1"
  local body="$2"
  local headers="$3"
  curl --silent --show-error --max-redirs 0 \
    --connect-timeout "$TIMEOUT_SECONDS" --max-time "$TIMEOUT_SECONDS" \
    --output "$body" --dump-header "$headers" --write-out '%{http_code}' "$url"
}

assert_status() {
  local name="$1"
  local actual="$2"
  local expected="$3"
  [[ "$actual" == "$expected" ]] || fail "$name status mismatch: actual=$actual expected=$expected" 3
}

assert_status_in_csv() {
  local name="$1"
  local actual="$2"
  local csv="$3"
  local value
  IFS=',' read -ra values <<< "$csv"
  for value in "${values[@]}"; do
    value="${value//[[:space:]]/}"
    [[ -n "$value" ]] || continue
    if [[ "$actual" == "$value" ]]; then
      return 0
    fi
  done
  fail "$name status mismatch: actual=$actual expected_one_of=$csv" 3
}

assert_json_status_ok() {
  local name="$1"
  local body="$2"
  python3 - "$name" "$body" <<'PY'
from __future__ import annotations

import json
import sys

name = sys.argv[1]
path = sys.argv[2]
with open(path, encoding="utf-8") as handle:
    data = json.load(handle)
if data.get("status") != "ok":
    raise SystemExit(f"{name} json status mismatch: {data.get('status')!r}")
if name == "readyz":
    checks = data.get("checks") or {}
    bad = sorted(key for key, value in checks.items() if value is not True)
    if bad:
        raise SystemExit(f"readyz checks not ok: {','.join(bad)}")
PY
}

assert_header_contains() {
  local name="$1"
  local headers="$2"
  local pattern="$3"
  if ! tr -d '\r' < "$headers" | grep -Eiq "$pattern"; then
    fail "$name missing expected header pattern: $pattern" 3
  fi
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --backend-health-url)
      BACKEND_HEALTH_URL="${2:?--backend-health-url requires a URL}"
      shift 2
      ;;
    --backend-ready-url)
      BACKEND_READY_URL="${2:?--backend-ready-url requires a URL}"
      shift 2
      ;;
    --frontend-url)
      FRONTEND_URL="${2:?--frontend-url requires a URL}"
      shift 2
      ;;
    --frontend-api-url)
      FRONTEND_API_URL="${2:?--frontend-api-url requires a URL}"
      shift 2
      ;;
    --public-url)
      PUBLIC_URL="${2:?--public-url requires a URL or none}"
      shift 2
      ;;
    --public-statuses)
      PUBLIC_STATUSES="${2:?--public-statuses requires a comma-separated list}"
      shift 2
      ;;
    --timeout-seconds)
      TIMEOUT_SECONDS="${2:?--timeout-seconds requires a positive integer}"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

is_positive_int "$TIMEOUT_SECONDS" || fail "--timeout-seconds must be a positive integer" 2
command -v curl >/dev/null 2>&1 || fail "curl is required but was not found in PATH" 127
command -v python3 >/dev/null 2>&1 || fail "python3 is required but was not found in PATH" 127

health_body="$(new_tmp)"
health_headers="$(new_tmp)"
health_status="$(http_get "$BACKEND_HEALTH_URL" "$health_body" "$health_headers")"
assert_status "backend_health" "$health_status" "200"
assert_json_status_ok "health" "$health_body"
assert_header_contains "backend_health" "$health_headers" '^x-content-type-options:[[:space:]]*nosniff$'

ready_body="$(new_tmp)"
ready_headers="$(new_tmp)"
ready_status="$(http_get "$BACKEND_READY_URL" "$ready_body" "$ready_headers")"
assert_status "backend_readyz" "$ready_status" "200"
assert_json_status_ok "readyz" "$ready_body"
assert_header_contains "backend_readyz" "$ready_headers" '^cache-control:[[:space:]]*no-store$'

frontend_body="$(new_tmp)"
frontend_headers="$(new_tmp)"
frontend_status="$(http_get "$FRONTEND_URL" "$frontend_body" "$frontend_headers")"
assert_status "frontend" "$frontend_status" "200"
assert_header_contains "frontend" "$frontend_headers" '^x-frame-options:[[:space:]]*DENY$'
assert_header_contains "frontend" "$frontend_headers" '^content-security-policy-report-only:'

frontend_api_body="$(new_tmp)"
frontend_api_headers="$(new_tmp)"
frontend_api_status="$(http_get "$FRONTEND_API_URL" "$frontend_api_body" "$frontend_api_headers")"
assert_status "frontend_api_proxy" "$frontend_api_status" "401"
assert_header_contains "frontend_api_proxy" "$frontend_api_headers" '^cache-control:[[:space:]]*no-store$'
assert_header_contains "frontend_api_proxy" "$frontend_api_headers" '^x-frame-options:[[:space:]]*DENY$'

echo "backend_health=ok"
echo "backend_readyz=ok"
echo "frontend=ok"
echo "frontend_api_proxy=ok"

if [[ "${PUBLIC_URL,,}" != "none" ]]; then
  public_body="$(new_tmp)"
  public_headers="$(new_tmp)"
  public_status="$(http_status_only "$PUBLIC_URL" "$public_body" "$public_headers")"
  assert_status_in_csv "public_edge" "$public_status" "$PUBLIC_STATUSES"
  if [[ "$public_status" =~ ^30[12378]$ ]]; then
    assert_header_contains "public_edge" "$public_headers" '^location:'
  fi
  echo "public_edge=ok"
  echo "public_status=$public_status"
else
  echo "public_edge=skipped"
fi
