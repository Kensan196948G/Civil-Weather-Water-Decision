#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'USAGE'
Usage: db-backup-restore-drill.sh [--backup-dir DIR] [--max-age-hours HOURS] [--warn-age-hours HOURS]

Runs a non-destructive restore drill against the latest cwwd-*.dump by validating
its checksum and asking pg_restore to list the archive contents. This never
connects to a database and never performs a restore.
USAGE
}

BACKUP_DIR="${BACKUP_DIR:-/var/backups/cwwd/postgres}"
MAX_AGE_HOURS="${BACKUP_RESTORE_DRILL_MAX_AGE_HOURS:-30}"
WARN_AGE_HOURS="${BACKUP_RESTORE_DRILL_WARN_AGE_HOURS:-26}"

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

redact_pg_stderr() {
  sed -E 's#postgresql(\\+[^:]+)?://[^@[:space:]]+@#postgresql://***:***@#g' >&2
}

default_pg_restore_bin() {
  local candidates=()
  local path
  for path in /usr/lib/postgresql/*/bin/pg_restore; do
    [[ -x "$path" ]] && candidates+=("$path")
  done
  if [[ ${#candidates[@]} -gt 0 ]]; then
    printf '%s\n' "${candidates[@]}" | sort -V | tail -n 1
  else
    printf '%s\n' pg_restore
  fi
}

check_orphans() {
  local file manifest dump target
  while IFS= read -r file; do
    [[ -n "$file" ]] || continue
    is_dump_name "$file" || fail "Malformed dump filename: $file" 2
    [[ -s "$file" ]] || fail "Zero-byte dump: $file" 2
    require_private_file "$file"
    [[ -f "${file}.sha256" ]] || fail "Dump is missing checksum manifest: $file" 2
  done < <(find "$BACKUP_DIR" -maxdepth 1 -type f -name 'cwwd-*.dump' -print)

  while IFS= read -r manifest; do
    [[ -n "$manifest" ]] || continue
    is_manifest_name "$manifest" || fail "Malformed checksum manifest filename: $manifest" 2
    require_private_file "$manifest"
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

[[ -d "$BACKUP_DIR" ]] || fail "Backup directory not found: $BACKUP_DIR" 2
require_private_dir "$BACKUP_DIR"
check_orphans

LATEST="$(
  find "$BACKUP_DIR" -maxdepth 1 -type f -regextype posix-extended -regex '.*/cwwd-[0-9]{8}T[0-9]{6}Z\.dump' -printf '%T@ %p\n' |
    sort -nr |
    awk 'NR == 1 { $1=""; sub(/^ /, ""); print }'
)"

[[ -n "$LATEST" ]] || fail "No cwwd-YYYYMMDDTHHMMSSZ.dump files found in $BACKUP_DIR" 2
is_dump_name "$LATEST" || fail "Malformed dump filename: $LATEST" 2
[[ -s "$LATEST" ]] || fail "Zero-byte dump: $LATEST" 2
require_private_file "$LATEST"

MANIFEST="${LATEST}.sha256"
[[ -f "$MANIFEST" ]] || fail "Missing checksum manifest: $MANIFEST" 2
require_private_file "$MANIFEST"
TARGET="$(awk 'NR == 1 { print $2 }' "$MANIFEST")"
[[ "$TARGET" == "$(basename "$LATEST")" ]] || fail "Checksum manifest target mismatch: $MANIFEST target=$TARGET expected=$(basename "$LATEST")" 2
(cd "$(dirname "$MANIFEST")" && sha256sum -c "$(basename "$MANIFEST")")

PG_RESTORE_BIN="${PG_RESTORE_BIN:-$(default_pg_restore_bin)}"
command -v "$PG_RESTORE_BIN" >/dev/null 2>&1 || fail "pg_restore is required but was not found in PATH" 127

unset DATABASE_URL DATABASE_URL_DIRECT PGPASSWORD PGPASSFILE PGHOST PGPORT PGDATABASE PGUSER PGSSLMODE PGCHANNELBINDING
LIST_OUTPUT="$("$PG_RESTORE_BIN" --list "$LATEST" 2> >(redact_pg_stderr))"
ENTRY_COUNT="$(printf '%s\n' "$LIST_OUTPUT" | awk 'NF { count += 1 } END { print count + 0 }')"
if (( ENTRY_COUNT == 0 )); then
  fail "pg_restore --list returned no entries for $LATEST" 2
fi

NOW="$(date +%s)"
MTIME="$(stat -c '%Y' "$LATEST")"
AGE_SECONDS="$((NOW - MTIME))"
MAX_AGE_SECONDS="$((MAX_AGE_HOURS * 3600))"

if (( AGE_SECONDS > MAX_AGE_SECONDS )); then
  echo "Latest backup restore drill target is stale: latest=$LATEST age_seconds=$AGE_SECONDS max_age_seconds=$MAX_AGE_SECONDS" >&2
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
echo "pg_restore_list=ok"
echo "restore_entries=$ENTRY_COUNT"
