#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'USAGE'
Usage: ops-status-json-check.sh [options]

Checks the persisted secret-free CWWD operations JSON snapshot.

Options:
  --path PATH             default /var/lib/cwwd/ops-status.json
  --max-age-minutes N     default 60
  --owner USER            default kensan
  --group GROUP           default kensan
  --file-mode MODE        default 640
  --dir-mode MODE         default 750
USAGE
}

SNAPSHOT_PATH="${CWWD_OPS_STATUS_JSON_PATH:-/var/lib/cwwd/ops-status.json}"
MAX_AGE_MINUTES="${CWWD_OPS_STATUS_JSON_MAX_AGE_MINUTES:-60}"
OWNER="${CWWD_OPS_STATUS_JSON_OWNER:-kensan}"
GROUP="${CWWD_OPS_STATUS_JSON_GROUP:-kensan}"
FILE_MODE="${CWWD_OPS_STATUS_JSON_FILE_MODE:-640}"
DIR_MODE="${CWWD_OPS_STATUS_JSON_DIR_MODE:-750}"

fail() {
  echo "$1" >&2
  exit "${2:-2}"
}

is_positive_int() {
  [[ "$1" =~ ^[0-9]+$ && "$1" != "0" ]]
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --path)
      SNAPSHOT_PATH="${2:?--path requires a path}"
      shift 2
      ;;
    --max-age-minutes)
      MAX_AGE_MINUTES="${2:?--max-age-minutes requires a positive integer}"
      shift 2
      ;;
    --owner)
      OWNER="${2:?--owner requires a user name}"
      shift 2
      ;;
    --group)
      GROUP="${2:?--group requires a group name}"
      shift 2
      ;;
    --file-mode)
      FILE_MODE="${2:?--file-mode requires an octal mode}"
      shift 2
      ;;
    --dir-mode)
      DIR_MODE="${2:?--dir-mode requires an octal mode}"
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

[[ "$SNAPSHOT_PATH" == /* ]] || fail "--path must be an absolute path" 2
is_positive_int "$MAX_AGE_MINUTES" || fail "--max-age-minutes must be a positive integer" 2
command -v python3 >/dev/null 2>&1 || fail "python3 is required but was not found in PATH" 127

snapshot_dir="$(dirname -- "$SNAPSHOT_PATH")"
[[ -d "$snapshot_dir" ]] || fail "ops status JSON directory not found: $snapshot_dir" 2
[[ -f "$SNAPSHOT_PATH" ]] || fail "ops status JSON snapshot not found: $SNAPSHOT_PATH" 2

dir_owner="$(stat -c '%U' "$snapshot_dir")"
dir_group="$(stat -c '%G' "$snapshot_dir")"
dir_mode="$(stat -c '%a' "$snapshot_dir")"
file_owner="$(stat -c '%U' "$SNAPSHOT_PATH")"
file_group="$(stat -c '%G' "$SNAPSHOT_PATH")"
file_mode="$(stat -c '%a' "$SNAPSHOT_PATH")"

[[ "$dir_owner" == "$OWNER" ]] || fail "ops_status_json_dir_owner_mismatch=$snapshot_dir actual=$dir_owner expected=$OWNER" 3
[[ "$dir_group" == "$GROUP" ]] || fail "ops_status_json_dir_group_mismatch=$snapshot_dir actual=$dir_group expected=$GROUP" 3
[[ "$dir_mode" == "$DIR_MODE" ]] || fail "ops_status_json_dir_mode_mismatch=$snapshot_dir actual=$dir_mode expected=$DIR_MODE" 3
[[ "$file_owner" == "$OWNER" ]] || fail "ops_status_json_owner_mismatch=$SNAPSHOT_PATH actual=$file_owner expected=$OWNER" 3
[[ "$file_group" == "$GROUP" ]] || fail "ops_status_json_group_mismatch=$SNAPSHOT_PATH actual=$file_group expected=$GROUP" 3
[[ "$file_mode" == "$FILE_MODE" ]] || fail "ops_status_json_mode_mismatch=$SNAPSHOT_PATH actual=$file_mode expected=$FILE_MODE" 3

now="$(date +%s)"
mtime="$(stat -c '%Y' "$SNAPSHOT_PATH")"
age_seconds=$((now - mtime))
max_age_seconds=$((MAX_AGE_MINUTES * 60))
if (( age_seconds < -60 )); then
  fail "ops_status_json_mtime_in_future=$SNAPSHOT_PATH age_seconds=$age_seconds" 3
fi
if (( age_seconds > max_age_seconds )); then
  fail "ops_status_json_stale=$SNAPSHOT_PATH age_seconds=$age_seconds max_age_seconds=$max_age_seconds" 3
fi

python3 - "$SNAPSHOT_PATH" <<'PY'
import json
import sys

path = sys.argv[1]
try:
    with open(path, "r", encoding="utf-8") as f:
        payload = json.load(f)
except Exception as exc:
    print(f"ops_status_json_invalid={path} error={exc.__class__.__name__}", file=sys.stderr)
    sys.exit(3)

status = payload.get("status")
failed_units_count = payload.get("failed_units_count")
services = payload.get("services")
timers = payload.get("timers")

if status != "ok":
    print(f"ops_status_json_status_mismatch={path} actual={status!r} expected='ok'", file=sys.stderr)
    sys.exit(3)
if failed_units_count != 0:
    print(
        f"ops_status_json_failed_units_count_mismatch={path} actual={failed_units_count!r} expected=0",
        file=sys.stderr,
    )
    sys.exit(3)
if not isinstance(services, list) or not services:
    print(f"ops_status_json_services_missing={path}", file=sys.stderr)
    sys.exit(3)
if not isinstance(timers, list) or not timers:
    print(f"ops_status_json_timers_missing={path}", file=sys.stderr)
    sys.exit(3)

print("snapshot_status=ok")
print("failed_units_count=0")
print(f"services={len(services)}")
print(f"timers={len(timers)}")
PY

echo "snapshot_path=$SNAPSHOT_PATH"
echo "age_seconds=$age_seconds"
echo "max_age_seconds=$max_age_seconds"
echo "owner=$file_owner"
echo "group=$file_group"
echo "mode=$file_mode"
echo "status=ok"
