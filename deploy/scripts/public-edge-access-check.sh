#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'USAGE'
Usage: public-edge-access-check.sh [options]

Checks unauthenticated public Cloudflare Access coverage without credentials:
  --base-url URL          default https://cwwd.mirai-dx-platform.com
  --path PATH             path to check; can be repeated
  --expected-statuses CSV default 302
  --location-contains STR default cloudflareaccess.com/cdn-cgi/access/login
  --timeout-seconds N     default 10
USAGE
}

BASE_URL="${CWWD_PUBLIC_BASE_URL:-https://cwwd.mirai-dx-platform.com}"
EXPECTED_STATUSES="${CWWD_PUBLIC_EDGE_STATUSES:-302}"
LOCATION_CONTAINS="${CWWD_PUBLIC_EDGE_LOCATION_CONTAINS:-cloudflareaccess.com/cdn-cgi/access/login}"
TIMEOUT_SECONDS="${CWWD_PUBLIC_EDGE_TIMEOUT_SECONDS:-10}"
PATHS=()
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

new_tmp() {
  local path
  path="$(mktemp)"
  TMP_FILES+=("$path")
  printf '%s' "$path"
}

is_positive_int() {
  [[ "$1" =~ ^[0-9]+$ && "$1" != "0" ]]
}

join_url() {
  local base="${1%/}"
  local path="$2"
  [[ "$path" == /* ]] || path="/$path"
  printf '%s%s' "$base" "$path"
}

http_status_only() {
  local url="$1"
  local body="$2"
  local headers="$3"
  curl --silent --show-error --max-redirs 0 \
    --connect-timeout "$TIMEOUT_SECONDS" --max-time "$TIMEOUT_SECONDS" \
    --output "$body" --dump-header "$headers" --write-out '%{http_code}' "$url"
}

status_allowed() {
  local actual="$1"
  local csv="$2"
  local value
  IFS=',' read -ra values <<< "$csv"
  for value in "${values[@]}"; do
    value="${value//[[:space:]]/}"
    [[ -n "$value" ]] || continue
    [[ "$actual" == "$value" ]] && return 0
  done
  return 1
}

assert_header_contains() {
  local name="$1"
  local headers="$2"
  local pattern="$3"
  if ! tr -d '\r' < "$headers" | grep -Eiq "$pattern"; then
    fail "$name missing expected header pattern: $pattern" 3
  fi
}

assert_location_contains() {
  local name="$1"
  local headers="$2"
  local expected="$3"
  local location_lines
  local expected_lower
  [[ -n "$expected" ]] || return 0
  location_lines="$(tr -d '\r' < "$headers" | awk 'tolower($0) ~ /^location:/ { print tolower($0) }')"
  expected_lower="$(printf '%s' "$expected" | tr '[:upper:]' '[:lower:]')"
  if ! grep -Fq "$expected_lower" <<< "$location_lines"; then
    fail "$name location missing expected substring: $expected" 3
  fi
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --base-url)
      BASE_URL="${2:?--base-url requires a URL}"
      shift 2
      ;;
    --path)
      PATHS+=("${2:?--path requires a path}")
      shift 2
      ;;
    --expected-statuses)
      EXPECTED_STATUSES="${2:?--expected-statuses requires a CSV}"
      shift 2
      ;;
    --location-contains)
      LOCATION_CONTAINS="${2:?--location-contains requires a substring}"
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

if [[ "${#PATHS[@]}" -eq 0 ]]; then
  PATHS=(/ /api/sites /health /readyz /docs /openapi.json)
fi

is_positive_int "$TIMEOUT_SECONDS" || fail "--timeout-seconds must be a positive integer" 2
command -v curl >/dev/null 2>&1 || fail "curl is required but was not found in PATH" 127

checked=0
for path in "${PATHS[@]}"; do
  body="$(new_tmp)"
  headers="$(new_tmp)"
  status="$(http_status_only "$(join_url "$BASE_URL" "$path")" "$body" "$headers")"
  if ! status_allowed "$status" "$EXPECTED_STATUSES"; then
    fail "public_edge path=$path status mismatch: actual=$status expected_one_of=$EXPECTED_STATUSES" 3
  fi
  if [[ "$status" =~ ^30[12378]$ ]]; then
    assert_header_contains "public_edge path=$path" "$headers" '^location:'
    assert_location_contains "public_edge path=$path" "$headers" "$LOCATION_CONTAINS"
  fi
  echo "public_edge_path=$path status=$status access=ok"
  checked=$((checked + 1))
done

echo "paths_checked=$checked"
echo "status=ok"
