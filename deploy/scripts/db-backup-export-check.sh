#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'USAGE'
Usage: db-backup-export-check.sh [--export-dir DIR] [--max-age-hours HOURS] [--warn-age-hours HOURS] [--passphrase-file PATH]

Verifies the latest encrypted cwwd-*.dump.tar.gpg export exists, is fresh enough,
has a valid .sha256 manifest, and can be decrypted to a tar containing the dump
and checksum manifest when --passphrase-file is provided.
USAGE
}

EXPORT_DIR="${BACKUP_EXPORT_DIR:-/var/backups/cwwd/exports}"
MAX_AGE_HOURS="${BACKUP_EXPORT_MAX_AGE_HOURS:-28}"
WARN_AGE_HOURS="${BACKUP_EXPORT_WARN_AGE_HOURS:-26}"
PASSPHRASE_FILE="${BACKUP_EXPORT_PASSPHRASE_FILE:-}"
TMP_TAR=""

fail() {
  echo "$1" >&2
  exit "${2:-2}"
}

is_positive_int() {
  [[ "$1" =~ ^[0-9]+$ && "$1" != "0" ]]
}

is_export_name() {
  [[ "$(basename "$1")" =~ ^cwwd-[0-9]{8}T[0-9]{6}Z\.dump\.tar\.gpg$ ]]
}

is_manifest_name() {
  [[ "$(basename "$1")" =~ ^cwwd-[0-9]{8}T[0-9]{6}Z\.dump\.tar\.gpg\.sha256$ ]]
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
  local file manifest archive target
  while IFS= read -r file; do
    [[ -n "$file" ]] || continue
    if ! is_export_name "$file"; then
      fail "Malformed encrypted export filename: $file" 2
    fi
    [[ -s "$file" ]] || fail "Zero-byte encrypted export: $file" 2
    require_private_file "$file"
    [[ -f "${file}.sha256" ]] || fail "Encrypted export is missing checksum manifest: $file" 2
  done < <(find "$EXPORT_DIR" -maxdepth 1 -type f -name 'cwwd-*.dump.tar.gpg' -print)

  while IFS= read -r manifest; do
    [[ -n "$manifest" ]] || continue
    if ! is_manifest_name "$manifest"; then
      fail "Malformed encrypted export checksum filename: $manifest" 2
    fi
    require_private_file "$manifest"
    archive="${manifest%.sha256}"
    [[ -f "$archive" ]] || fail "Checksum manifest is missing encrypted export: $manifest" 2
    target="$(awk 'NR == 1 { print $2 }' "$manifest")"
    [[ "$target" == "$(basename "$archive")" ]] || fail "Checksum manifest target mismatch: $manifest target=$target expected=$(basename "$archive")" 2
  done < <(find "$EXPORT_DIR" -maxdepth 1 -type f -name 'cwwd-*.dump.tar.gpg.sha256' -print)

  while IFS= read -r file; do
    [[ -n "$file" ]] || continue
    fail "Stale temporary encrypted export file present: $file" 2
  done < <(find "$EXPORT_DIR" -maxdepth 1 -type f -name 'cwwd-*.dump.tar.gpg.tmp' -mmin +60 -print)
}

verify_decrypt_list() {
  local archive="$1"
  local archive_base dump_base manifest_base list_file entry count
  command -v gpg >/dev/null 2>&1 || fail "gpg is required but was not found in PATH" 127
  command -v tar >/dev/null 2>&1 || fail "tar is required but was not found in PATH" 127

  TMP_TAR="$(mktemp)"
  list_file="$(mktemp)"
  trap 'rm -f "$TMP_TAR" "$list_file"; true' RETURN

  gpg --batch --yes --quiet --pinentry-mode loopback --no-random-seed-file \
    --passphrase-file "$PASSPHRASE_FILE" --output "$TMP_TAR" --decrypt "$archive"

  tar -tf "$TMP_TAR" > "$list_file"
  archive_base="$(basename "$archive")"
  dump_base="${archive_base%.tar.gpg}"
  manifest_base="${dump_base}.sha256"
  while IFS= read -r entry; do
    [[ -n "$entry" ]] || continue
    case "$entry" in
      /*|*../*|../*|.)
        fail "Decrypted export tar contains unsafe entry: $entry" 2
        ;;
    esac
  done < "$list_file"
  count="$(wc -l < "$list_file" | tr -d ' ')"
  [[ "$count" == "2" ]] || fail "Decrypted export tar has unexpected entry count: $count expected=2" 2
  grep -Fx -- "$dump_base" "$list_file" >/dev/null || fail "Decrypted export tar is missing dump entry: $dump_base" 2
  grep -Fx -- "$manifest_base" "$list_file" >/dev/null || fail "Decrypted export tar is missing checksum entry: $manifest_base" 2
  rm -f "$TMP_TAR" "$list_file"
  TMP_TAR=""
  trap - RETURN
}

cleanup() {
  [[ -n "$TMP_TAR" ]] && rm -f "$TMP_TAR"
  true
}
trap cleanup EXIT

while [[ $# -gt 0 ]]; do
  case "$1" in
    --export-dir)
      EXPORT_DIR="${2:?--export-dir requires a directory}"
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
    --passphrase-file)
      PASSPHRASE_FILE="${2:?--passphrase-file requires a path}"
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

[[ -d "$EXPORT_DIR" ]] || fail "Encrypted export directory not found: $EXPORT_DIR" 2
require_private_dir "$EXPORT_DIR"

if [[ -n "$PASSPHRASE_FILE" ]]; then
  [[ -f "$PASSPHRASE_FILE" ]] || fail "Passphrase file not found: $PASSPHRASE_FILE" 2
  require_private_file "$PASSPHRASE_FILE"
fi

check_orphans

LATEST="$(
  find "$EXPORT_DIR" -maxdepth 1 -type f -regextype posix-extended -regex '.*/cwwd-[0-9]{8}T[0-9]{6}Z\.dump\.tar\.gpg' -printf '%T@ %p\n' |
    sort -nr |
    awk 'NR == 1 { $1=""; sub(/^ /, ""); print }'
)"

if [[ -z "$LATEST" ]]; then
  fail "No cwwd-YYYYMMDDTHHMMSSZ.dump.tar.gpg files found in $EXPORT_DIR" 2
fi

MANIFEST="${LATEST}.sha256"
[[ -f "$MANIFEST" ]] || fail "Missing checksum manifest: $MANIFEST" 2

(cd "$(dirname "$MANIFEST")" && sha256sum -c "$(basename "$MANIFEST")")

if [[ -n "$PASSPHRASE_FILE" ]]; then
  verify_decrypt_list "$LATEST"
fi

NOW="$(date +%s)"
MTIME="$(stat -c '%Y' "$LATEST")"
AGE_SECONDS="$((NOW - MTIME))"
MAX_AGE_SECONDS="$((MAX_AGE_HOURS * 3600))"

if (( AGE_SECONDS > MAX_AGE_SECONDS )); then
  echo "Latest encrypted export is stale: latest=$LATEST age_seconds=$AGE_SECONDS max_age_seconds=$MAX_AGE_SECONDS" >&2
  exit 3
fi

echo "latest_export=$LATEST"
echo "age_seconds=$AGE_SECONDS"
if (( AGE_SECONDS > WARN_AGE_HOURS * 3600 )); then
  echo "status=warning"
else
  echo "status=ok"
fi
echo "max_age_seconds=$MAX_AGE_SECONDS"
echo "checksum=ok"
if [[ -n "$PASSPHRASE_FILE" ]]; then
  echo "decrypt_list=ok"
fi
