#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'USAGE'
Usage: db-backup-export.sh [--backup-dir DIR] [--output-dir DIR] --passphrase-file PATH [--retention-days DAYS]

Creates an encrypted transport archive from the latest valid cwwd-*.dump + .sha256 pair.
The passphrase is read only from --passphrase-file and is never printed or passed as argv.
USAGE
}

BACKUP_DIR="${BACKUP_DIR:-/var/backups/cwwd/postgres}"
OUTPUT_DIR="${BACKUP_EXPORT_DIR:-/var/backups/cwwd/exports}"
PASSPHRASE_FILE="${BACKUP_EXPORT_PASSPHRASE_FILE:-}"
RETENTION_DAYS="${BACKUP_EXPORT_RETENTION_DAYS:-}"
TMP_TAR=""
TMP_ENC=""

fail() {
  echo "$1" >&2
  exit "${2:-2}"
}

is_positive_int() {
  [[ "$1" =~ ^[0-9]+$ && "$1" != "0" ]]
}

mode_value() {
  printf '%d' "$((8#$(stat -c '%a' "$1")))"
}

require_private_file() {
  local path="$1"
  local mode
  mode="$(mode_value "$path")"
  if (( (mode & 0177) != 0 )); then
    fail "Unsafe file permissions: $path mode=$(stat -c '%a' "$path") requires no owner-exec/group/other permissions" 2
  fi
}

require_private_dir() {
  local path="$1"
  local mode
  mode="$(mode_value "$path")"
  if (( (mode & 0077) != 0 )); then
    fail "Unsafe directory permissions: $path mode=$(stat -c '%a' "$path") requires no group/other permissions" 2
  fi
}

latest_dump() {
  find "$BACKUP_DIR" -maxdepth 1 -type f -regextype posix-extended -regex '.*/cwwd-[0-9]{8}T[0-9]{6}Z\.dump' -printf '%T@ %p\n' |
    sort -nr |
    awk 'NR == 1 { $1=""; sub(/^ /, ""); print }'
}

prune_old_exports() {
  local dir="$1"
  local days="$2"
  local files=()
  local file
  mapfile -t files < <(
    find "$dir" -maxdepth 1 -type f \( -name 'cwwd-*.dump.tar.gpg' -o -name 'cwwd-*.dump.tar.gpg.sha256' \) -mtime +"$days" -print
  )
  for file in "${files[@]}"; do
    rm -f -- "$file"
  done
  echo "export_retention_deleted=${#files[@]}"
}

cleanup() {
  [[ -n "$TMP_TAR" ]] && rm -f "$TMP_TAR"
  [[ -n "$TMP_ENC" ]] && rm -f "$TMP_ENC"
  true
}
trap cleanup EXIT

while [[ $# -gt 0 ]]; do
  case "$1" in
    --backup-dir)
      BACKUP_DIR="${2:?--backup-dir requires a directory}"
      shift 2
      ;;
    --output-dir)
      OUTPUT_DIR="${2:?--output-dir requires a directory}"
      shift 2
      ;;
    --passphrase-file)
      PASSPHRASE_FILE="${2:?--passphrase-file requires a path}"
      shift 2
      ;;
    --retention-days)
      RETENTION_DAYS="${2:?--retention-days requires a positive integer}"
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

[[ -d "$BACKUP_DIR" ]] || fail "Backup directory not found: $BACKUP_DIR" 2
require_private_dir "$BACKUP_DIR"
[[ -n "$PASSPHRASE_FILE" ]] || fail "--passphrase-file is required" 2
[[ -f "$PASSPHRASE_FILE" ]] || fail "Passphrase file not found: $PASSPHRASE_FILE" 2
require_private_file "$PASSPHRASE_FILE"
if [[ -n "$RETENTION_DAYS" ]]; then
  is_positive_int "$RETENTION_DAYS" || fail "--retention-days must be a positive integer" 2
fi

command -v gpg >/dev/null 2>&1 || fail "gpg is required but was not found in PATH" 127
command -v tar >/dev/null 2>&1 || fail "tar is required but was not found in PATH" 127

mkdir -p "$OUTPUT_DIR"
chmod 700 "$OUTPUT_DIR"
require_private_dir "$OUTPUT_DIR"

DUMP="$(latest_dump)"
[[ -n "$DUMP" ]] || fail "No cwwd-YYYYMMDDTHHMMSSZ.dump files found in $BACKUP_DIR" 2
MANIFEST="${DUMP}.sha256"
[[ -f "$MANIFEST" ]] || fail "Missing checksum manifest: $MANIFEST" 2
require_private_file "$DUMP"
require_private_file "$MANIFEST"
(cd "$(dirname "$MANIFEST")" && sha256sum -c "$(basename "$MANIFEST")")

BASE="$(basename "$DUMP")"
OUT="${OUTPUT_DIR}/${BASE}.tar.gpg"
OUT_MANIFEST="${OUT}.sha256"
TMP_TAR="$(mktemp)"
TMP_ENC="${OUT}.tmp"

rm -f "$TMP_ENC"
tar -C "$(dirname "$DUMP")" -cf "$TMP_TAR" "$BASE" "$(basename "$MANIFEST")"
chmod 600 "$TMP_TAR"
gpg --batch --yes --pinentry-mode loopback --no-random-seed-file --passphrase-file "$PASSPHRASE_FILE" \
  --symmetric --cipher-algo AES256 --output "$TMP_ENC" "$TMP_TAR"
chmod 600 "$TMP_ENC"
mv "$TMP_ENC" "$OUT"
(cd "$OUTPUT_DIR" && sha256sum "$(basename "$OUT")" > "$(basename "$OUT_MANIFEST")")
chmod 600 "$OUT" "$OUT_MANIFEST"

echo "export=$OUT"
echo "sha256=$OUT_MANIFEST"
echo "source=$DUMP"
if [[ -n "$RETENTION_DAYS" ]]; then
  prune_old_exports "$OUTPUT_DIR" "$RETENTION_DAYS"
fi
