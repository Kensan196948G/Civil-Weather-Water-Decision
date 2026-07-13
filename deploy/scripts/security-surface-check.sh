#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'USAGE'
Usage: security-surface-check.sh [options]

Checks production security surface without credentials:
  --backend-url URL       default http://127.0.0.1:55019
  --frontend-url URL      default http://127.0.0.1:34979/
  --timeout-seconds N     default 10
USAGE
}

BACKEND_URL="${CWWD_BACKEND_URL:-http://127.0.0.1:55019}"
FRONTEND_URL="${CWWD_FRONTEND_URL:-http://127.0.0.1:34979/}"
TIMEOUT_SECONDS="${CWWD_SECURITY_TIMEOUT_SECONDS:-10}"
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

trim_trailing_slash() {
  local value="$1"
  while [[ "$value" == */ && "$value" != "/" ]]; do
    value="${value%/}"
  done
  printf '%s' "$value"
}

join_url() {
  local base="$1"
  local path="$2"
  base="$(trim_trailing_slash "$base")"
  printf '%s%s' "$base" "$path"
}

http_get() {
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

assert_backend_security_headers() {
  local name="$1"
  local headers="$2"
  assert_header_contains "$name" "$headers" '^x-content-type-options:[[:space:]]*nosniff$'
  assert_header_contains "$name" "$headers" '^x-frame-options:[[:space:]]*DENY$'
  assert_header_contains "$name" "$headers" '^referrer-policy:[[:space:]]*no-referrer$'
  assert_header_contains "$name" "$headers" '^permissions-policy:[[:space:]]*geolocation=\(\), microphone=\(\), camera=\(\)$'
  assert_header_contains "$name" "$headers" '^strict-transport-security:[[:space:]]*max-age=31536000$'
  assert_header_contains "$name" "$headers" '^cross-origin-opener-policy:[[:space:]]*same-origin$'
  assert_header_contains "$name" "$headers" '^cross-origin-resource-policy:[[:space:]]*same-site$'
  assert_header_contains "$name" "$headers" '^x-permitted-cross-domain-policies:[[:space:]]*none$'
  assert_header_contains "$name" "$headers" '^x-download-options:[[:space:]]*noopen$'
  assert_header_contains "$name" "$headers" '^cache-control:[[:space:]]*no-store$'
  assert_header_contains "$name" "$headers" "^content-security-policy:.*default-src 'none'"
}

assert_frontend_security_headers() {
  local name="$1"
  local headers="$2"
  assert_header_contains "$name" "$headers" '^x-content-type-options:[[:space:]]*nosniff$'
  assert_header_contains "$name" "$headers" '^x-frame-options:[[:space:]]*DENY$'
  assert_header_contains "$name" "$headers" '^referrer-policy:[[:space:]]*no-referrer$'
  assert_header_contains "$name" "$headers" '^permissions-policy:[[:space:]]*geolocation=\(\), microphone=\(\), camera=\(\)$'
  assert_header_contains "$name" "$headers" '^strict-transport-security:[[:space:]]*max-age=31536000$'
  assert_header_contains "$name" "$headers" '^cross-origin-opener-policy:[[:space:]]*same-origin$'
  assert_header_contains "$name" "$headers" '^cross-origin-resource-policy:[[:space:]]*same-site$'
  assert_header_contains "$name" "$headers" '^x-permitted-cross-domain-policies:[[:space:]]*none$'
  assert_header_contains "$name" "$headers" '^x-download-options:[[:space:]]*noopen$'
  assert_header_contains "$name" "$headers" '^cache-control:[[:space:]]*no-store$'
  assert_header_contains "$name" "$headers" "^content-security-policy-report-only:.*default-src 'self'"
  assert_header_contains "$name" "$headers" "^content-security-policy-report-only:.*frame-ancestors 'none'"
  assert_header_contains "$name" "$headers" '^content-security-policy-report-only:.*connect-src .*http://127\.0\.0\.1:\*'
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --backend-url)
      BACKEND_URL="${2:?--backend-url requires a URL}"
      shift 2
      ;;
    --frontend-url)
      FRONTEND_URL="${2:?--frontend-url requires a URL}"
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
health_status="$(http_get "$(join_url "$BACKEND_URL" "/health")" "$health_body" "$health_headers")"
assert_status "backend_health_security" "$health_status" "200"
assert_json_status_ok "backend_health_security" "$health_body"
assert_backend_security_headers "backend_health_security" "$health_headers"
echo "backend_health_security=ok"

auth_body="$(new_tmp)"
auth_headers="$(new_tmp)"
auth_status="$(http_get "$(join_url "$BACKEND_URL" "/api/sites")" "$auth_body" "$auth_headers")"
assert_status "backend_auth_guard" "$auth_status" "401"
assert_backend_security_headers "backend_auth_guard" "$auth_headers"
echo "backend_auth_guard=ok"

for docs_path in /docs /redoc /openapi.json; do
  docs_body="$(new_tmp)"
  docs_headers="$(new_tmp)"
  docs_status="$(http_get "$(join_url "$BACKEND_URL" "$docs_path")" "$docs_body" "$docs_headers")"
  assert_status "backend_docs_disabled $docs_path" "$docs_status" "404"
  assert_backend_security_headers "backend_docs_disabled $docs_path" "$docs_headers"
done
echo "backend_docs_disabled=ok"

frontend_body="$(new_tmp)"
frontend_headers="$(new_tmp)"
frontend_status="$(http_get "$FRONTEND_URL" "$frontend_body" "$frontend_headers")"
assert_status "frontend_security" "$frontend_status" "200"
assert_frontend_security_headers "frontend_security" "$frontend_headers"
echo "frontend_security=ok"
echo "status=ok"
