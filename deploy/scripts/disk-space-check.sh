#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'USAGE'
Usage: disk-space-check.sh [--path PATH ...] [--min-free-mib MIB] [--root-min-free-mib MIB] [--data-min-free-mib MIB] [--min-free-percent PCT] [--min-inode-free-percent PCT] [--dump-size-dir DIR] [--require-private-dirs]

Checks filesystem free space and inode headroom for production CWWD paths.
No secrets or environment files are read.
USAGE
}

PATHS=()
ROOT_MIN_FREE_MIB="${DISK_ROOT_MIN_FREE_MIB:-4096}"
DATA_MIN_FREE_MIB="${DISK_DATA_MIN_FREE_MIB:-10240}"
MIN_FREE_PERCENT="${DISK_MIN_FREE_PERCENT:-10}"
MIN_INODE_FREE_PERCENT="${DISK_MIN_INODE_FREE_PERCENT:-5}"
REQUIRE_PRIVATE_DIRS="${DISK_REQUIRE_PRIVATE_DIRS:-false}"
DUMP_SIZE_DIR="${DISK_DUMP_SIZE_DIR:-/var/backups/cwwd/postgres}"

fail() {
  echo "$1" >&2
  exit "${2:-2}"
}

is_nonnegative_int() {
  [[ "$1" =~ ^[0-9]+$ ]]
}

mode_value() {
  printf '%d' "$((8#$(stat -c '%a' "$1")))"
}

require_private_dir_if_backup_path() {
  local path="$1"
  local mode
  case "$path" in
    /var/backups/cwwd|/var/backups/cwwd/*)
      REQUIRE_PRIVATE_DIRS=true
      ;;
  esac
  if [[ "$REQUIRE_PRIVATE_DIRS" == true ]]; then
      mode="$(mode_value "$path")"
      if (( (mode & 0077) != 0 )); then
        fail "Unsafe monitored directory permissions: $path mode=$(stat -c '%a' "$path") requires no group/other permissions" 2
      fi
  fi
}

percent_free() {
  local free="$1"
  local total="$2"
  if (( total <= 0 )); then
    printf '0'
  else
    printf '%d' "$((free * 100 / total))"
  fi
}

latest_dump_size_bytes() {
  local latest
  if [[ ! -d "$DUMP_SIZE_DIR" ]]; then
    printf '0'
    return
  fi
  latest="$(
    find "$DUMP_SIZE_DIR" -maxdepth 1 -type f -regextype posix-extended -regex '.*/cwwd-[0-9]{8}T[0-9]{6}Z\.dump' -printf '%T@ %p\n' |
      sort -nr |
      awk 'NR == 1 { $1=""; sub(/^ /, ""); print }'
  )"
  if [[ -n "$latest" ]]; then
    stat -c '%s' "$latest"
  else
    printf '0'
  fi
}

min_free_bytes_for_path() {
  local path="$1"
  local dump_size="$2"
  local floor_mib multiplier floor_bytes dump_floor
  case "$path" in
    /var/backups/cwwd|/var/backups/cwwd/*)
      floor_mib="$DATA_MIN_FREE_MIB"
      multiplier=3
      ;;
    *)
      floor_mib="$ROOT_MIN_FREE_MIB"
      multiplier=2
      ;;
  esac
  floor_bytes="$((floor_mib * 1024 * 1024))"
  dump_floor="$((dump_size * multiplier))"
  if (( dump_floor > floor_bytes )); then
    printf '%s' "$dump_floor"
  else
    printf '%s' "$floor_bytes"
  fi
}

check_path() {
  local path="$1"
  local dump_size="$2"
  local filesystem size_bytes free_bytes free_percent inode_total inode_free inode_free_percent min_free_bytes

  [[ -d "$path" ]] || fail "Disk check path not found or not a directory: $path" 2
  require_private_dir_if_backup_path "$path"

  read -r filesystem size_bytes free_bytes < <(df -P -B1 "$path" | awk 'NR == 2 { print $1, $2, $4 }')
  read -r inode_total inode_free < <(df -Pi "$path" | awk 'NR == 2 { print $2, $4 }')
  [[ -n "${size_bytes:-}" && -n "${free_bytes:-}" ]] || fail "Unable to read disk bytes for $path" 2
  [[ -n "${inode_total:-}" && -n "${inode_free:-}" ]] || fail "Unable to read inode data for $path" 2

  free_percent="$(percent_free "$free_bytes" "$size_bytes")"
  inode_free_percent="$(percent_free "$inode_free" "$inode_total")"
  min_free_bytes="$(min_free_bytes_for_path "$path" "$dump_size")"

  echo "path=$path"
  echo "filesystem=$filesystem"
  echo "free_bytes=$free_bytes"
  echo "free_percent=$free_percent"
  echo "inode_free_percent=$inode_free_percent"
  echo "min_free_bytes=$min_free_bytes"

  if (( free_bytes < min_free_bytes )); then
    fail "Low disk free bytes: path=$path free_bytes=$free_bytes min_free_bytes=$min_free_bytes" 3
  fi
  if (( free_percent < MIN_FREE_PERCENT )); then
    fail "Low disk free percent: path=$path free_percent=$free_percent min_free_percent=$MIN_FREE_PERCENT" 3
  fi
  if (( inode_free_percent < MIN_INODE_FREE_PERCENT )); then
    fail "Low inode free percent: path=$path inode_free_percent=$inode_free_percent min_inode_free_percent=$MIN_INODE_FREE_PERCENT" 3
  fi
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --path)
      PATHS+=("${2:?--path requires a path}")
      shift 2
      ;;
    --min-free-mib)
      ROOT_MIN_FREE_MIB="${2:?--min-free-mib requires an integer}"
      DATA_MIN_FREE_MIB="$ROOT_MIN_FREE_MIB"
      shift 2
      ;;
    --root-min-free-mib)
      ROOT_MIN_FREE_MIB="${2:?--root-min-free-mib requires an integer}"
      shift 2
      ;;
    --data-min-free-mib)
      DATA_MIN_FREE_MIB="${2:?--data-min-free-mib requires an integer}"
      shift 2
      ;;
    --min-free-percent)
      MIN_FREE_PERCENT="${2:?--min-free-percent requires an integer}"
      shift 2
      ;;
    --min-inode-free-percent)
      MIN_INODE_FREE_PERCENT="${2:?--min-inode-free-percent requires an integer}"
      shift 2
      ;;
    --require-private-dirs)
      REQUIRE_PRIVATE_DIRS=true
      shift
      ;;
    --dump-size-dir)
      DUMP_SIZE_DIR="${2:?--dump-size-dir requires a directory}"
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

if [[ ${#PATHS[@]} -eq 0 ]]; then
  PATHS=(/ /var/backups/cwwd/postgres /var/backups/cwwd/exports)
fi

is_nonnegative_int "$ROOT_MIN_FREE_MIB" || fail "--root-min-free-mib must be a non-negative integer" 2
is_nonnegative_int "$DATA_MIN_FREE_MIB" || fail "--data-min-free-mib must be a non-negative integer" 2
is_nonnegative_int "$MIN_FREE_PERCENT" || fail "--min-free-percent must be a non-negative integer" 2
is_nonnegative_int "$MIN_INODE_FREE_PERCENT" || fail "--min-inode-free-percent must be a non-negative integer" 2
(( MIN_FREE_PERCENT <= 100 )) || fail "--min-free-percent must be <= 100" 2
(( MIN_INODE_FREE_PERCENT <= 100 )) || fail "--min-inode-free-percent must be <= 100" 2
command -v df >/dev/null 2>&1 || fail "df is required but was not found in PATH" 127

DUMP_SIZE_BYTES="$(latest_dump_size_bytes)"
echo "latest_dump_size_bytes=$DUMP_SIZE_BYTES"
for path in "${PATHS[@]}"; do
  check_path "$path" "$DUMP_SIZE_BYTES"
done

echo "status=ok"
