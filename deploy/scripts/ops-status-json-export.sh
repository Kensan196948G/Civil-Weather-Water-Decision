#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'USAGE'
Usage: ops-status-json-export.sh [options]

Writes a secret-free machine-readable CWWD operations snapshot atomically.

Options:
  --output PATH         default /var/lib/cwwd/ops-status.json
  --status-script PATH  default deploy/scripts/ops-status.sh beside this script
USAGE
}

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
STATUS_SCRIPT="$SCRIPT_DIR/ops-status.sh"
OUTPUT_PATH="${CWWD_OPS_STATUS_JSON_PATH:-/var/lib/cwwd/ops-status.json}"
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
  local dir="$1"
  local path
  path="$(mktemp "$dir/.ops-status.json.tmp.XXXXXX")"
  TMP_FILES+=("$path")
  printf '%s' "$path"
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --output)
      OUTPUT_PATH="${2:?--output requires a path}"
      shift 2
      ;;
    --status-script)
      STATUS_SCRIPT="${2:?--status-script requires a path}"
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

[[ "$OUTPUT_PATH" == /* ]] || fail "--output must be an absolute path" 2
[[ -x "$STATUS_SCRIPT" ]] || fail "status script is not executable: $STATUS_SCRIPT" 2
command -v python3 >/dev/null 2>&1 || fail "python3 is required but was not found in PATH" 127

output_dir="$(dirname -- "$OUTPUT_PATH")"
mkdir -p "$output_dir"
tmp="$(new_tmp "$output_dir")"

set +e
"$STATUS_SCRIPT" --json > "$tmp"
status_rc=$?
set -e

python3 -m json.tool "$tmp" >/dev/null || fail "ops status JSON is invalid" 3
chmod 0640 "$tmp"
mv -f "$tmp" "$OUTPUT_PATH"
TMP_FILES=()

bytes="$(wc -c < "$OUTPUT_PATH" | tr -d '[:space:]')"
echo "ops_status_json=$OUTPUT_PATH"
echo "bytes=$bytes"
echo "ops_status_exit_code=$status_rc"

if [[ "$status_rc" -ne 0 ]]; then
  fail "ops status reported failure; JSON snapshot was still written" "$status_rc"
fi

echo "status=ok"
