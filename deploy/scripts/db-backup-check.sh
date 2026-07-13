#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'USAGE'
Usage: db-backup-check.sh [--backup-dir DIR] [--max-age-hours HOURS] [--warn-age-hours HOURS] [--env-file PATH]

Verifies the latest cwwd-*.dump exists, is fresh enough, and has a valid .sha256 manifest.
Also checks backup artifact permissions and common orphan/corruption cases.
USAGE
}

BACKUP_DIR="${BACKUP_DIR:-/var/backups/cwwd/postgres}"
MAX_AGE_HOURS="${BACKUP_MAX_AGE_HOURS:-26}"
WARN_AGE_HOURS="${BACKUP_WARN_AGE_HOURS:-24}"
ENV_FILE=""

fail() {
  echo "$1" >&2
  exit "${2:-2}"
}

is_positive_int() {
  [[ "$1" =~ ^[0-9]+$ && "$1" != "0" ]]
}

is_dump_name() {
  [[ "$(basename "$1")" =~ ^cwwd-[0-9]{8}T[0-9]{6}Z\.dump$ ]]
}

is_manifest_name() {
  [[ "$(basename "$1")" =~ ^cwwd-[0-9]{8}T[0-9]{6}Z\.dump\.sha256$ ]]
}

mode_value() {
  printf '%d' "$((8#$(stat -c '%a' "$1")))"
}

require_private_dir() {
  local path="$1"
  local mode
  mode="$(mode_value "$path")"
  if (( (mode & 0077) != 0 )); then
    fail "Unsafe directory permissions: $path mode=$(stat -c '%a' "$path") requires no group/other permissions" 2
  fi
}

require_private_file() {
  local path="$1"
  local mode
  mode="$(mode_value "$path")"
  if (( (mode & 0177) != 0 )); then
    fail "Unsafe file permissions: $path mode=$(stat -c '%a' "$path") requires no owner-exec/group/other permissions" 2
  fi
}

check_orphans() {
  local file dump manifest target
  while IFS= read -r file; do
    [[ -n "$file" ]] || continue
    if ! is_dump_name "$file"; then
      fail "Malformed dump filename: $file" 2
    fi
    [[ -s "$file" ]] || fail "Zero-byte dump: $file" 2
    [[ -f "${file}.sha256" ]] || fail "Dump is missing checksum manifest: $file" 2
  done < <(find "$BACKUP_DIR" -maxdepth 1 -type f -name 'cwwd-*.dump' -print)

  while IFS= read -r manifest; do
    [[ -n "$manifest" ]] || continue
    if ! is_manifest_name "$manifest"; then
      fail "Malformed checksum manifest filename: $manifest" 2
    fi
    dump="${manifest%.sha256}"
    [[ -f "$dump" ]] || fail "Checksum manifest is missing dump: $manifest" 2
    target="$(awk 'NR == 1 { print $2 }' "$manifest")"
    [[ "$target" == "$(basename "$dump")" ]] || fail "Checksum manifest target mismatch: $manifest target=$target expected=$(basename "$dump")" 2
  done < <(find "$BACKUP_DIR" -maxdepth 1 -type f -name 'cwwd-*.dump.sha256' -print)

  while IFS= read -r file; do
    [[ -n "$file" ]] || continue
    fail "Stale temporary backup file present: $file" 2
  done < <(find "$BACKUP_DIR" -maxdepth 1 -type f -name 'cwwd-*.dump.tmp' -mmin +60 -print)
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --backup-dir)
      BACKUP_DIR="${2:?--backup-dir requires a directory}"
      shift 2
      ;;
    --max-age-hours)
      MAX_AGE_HOURS="${2:?--max-age-hours requires a positive integer}"
      shift 2
      ;;
    --warn-age-hours)
      WARN_AGE_HOURS="${2:?--warn-age-hours requires a positive integer}"
      shift 2
      ;;
    --env-file)
      ENV_FILE="${2:?--env-file requires a path}"
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

is_positive_int "$MAX_AGE_HOURS" || fail "--max-age-hours must be a positive integer" 2
is_positive_int "$WARN_AGE_HOURS" || fail "--warn-age-hours must be a positive integer" 2

if (( WARN_AGE_HOURS >= MAX_AGE_HOURS )); then
  fail "--warn-age-hours must be lower than --max-age-hours" 2
fi

if [[ ! -d "$BACKUP_DIR" ]]; then
  fail "Backup directory not found: $BACKUP_DIR" 2
fi

require_private_dir "$BACKUP_DIR"

if [[ -n "$ENV_FILE" ]]; then
  [[ -f "$ENV_FILE" ]] || fail "Backup env file not found: $ENV_FILE" 2
  require_private_file "$ENV_FILE"
fi

check_orphans

LATEST="$(
  find "$BACKUP_DIR" -maxdepth 1 -type f -regextype posix-extended -regex '.*/cwwd-[0-9]{8}T[0-9]{6}Z\.dump' -printf '%T@ %p\n' |
    sort -nr |
    awk 'NR == 1 { $1=""; sub(/^ /, ""); print }'
)"

if [[ -z "$LATEST" ]]; then
  fail "No cwwd-YYYYMMDDTHHMMSSZ.dump files found in $BACKUP_DIR" 2
fi

MANIFEST="${LATEST}.sha256"
if [[ ! -f "$MANIFEST" ]]; then
  fail "Missing checksum manifest: $MANIFEST" 2
fi

require_private_file "$LATEST"
require_private_file "$MANIFEST"

(cd "$(dirname "$MANIFEST")" && sha256sum -c "$(basename "$MANIFEST")")

NOW="$(date +%s)"
MTIME="$(stat -c '%Y' "$LATEST")"
AGE_SECONDS="$((NOW - MTIME))"
MAX_AGE_SECONDS="$((MAX_AGE_HOURS * 3600))"

if (( AGE_SECONDS > MAX_AGE_SECONDS )); then
  echo "Latest backup is stale: latest=$LATEST age_seconds=$AGE_SECONDS max_age_seconds=$MAX_AGE_SECONDS" >&2
  exit 3
fi

echo "latest_dump=$LATEST"
echo "age_seconds=$AGE_SECONDS"
if (( AGE_SECONDS > WARN_AGE_HOURS * 3600 )); then
  echo "status=warning"
else
  echo "status=ok"
fi
echo "max_age_seconds=$MAX_AGE_SECONDS"
echo "checksum=ok"
