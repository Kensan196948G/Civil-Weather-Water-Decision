#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'USAGE'
Usage: secret-file-permission-check.sh [options]

Checks secret/config file ownership and permissions without reading contents.
  --required-file PATH   required private file; can be repeated
  --optional-file PATH   optional private file; can be repeated
  --owner USER           expected owner (default: kensan)
  --group GROUP          expected group (default: kensan)
USAGE
}

OWNER="${CWWD_SECRET_FILE_OWNER:-kensan}"
GROUP="${CWWD_SECRET_FILE_GROUP:-kensan}"
REQUIRED_FILES=()
OPTIONAL_FILES=()

fail() {
  echo "$1" >&2
  exit "${2:-2}"
}

mode_value() {
  printf '%d' "$((8#$(stat -c '%a' "$1")))"
}

check_private_file() {
  local path="$1"
  local required="$2"
  local owner group mode mode_dec

  if [[ ! -e "$path" ]]; then
    if [[ "$required" == true ]]; then
      echo "missing_required_file=$path" >&2
      return 1
    fi
    echo "optional_file_missing=$path"
    return 0
  fi
  if [[ ! -f "$path" ]]; then
    echo "not_regular_file=$path" >&2
    return 1
  fi

  owner="$(stat -c '%U' "$path")"
  group="$(stat -c '%G' "$path")"
  mode="$(stat -c '%a' "$path")"
  if [[ "$owner" != "$OWNER" ]]; then
    echo "owner_mismatch=$path actual=$owner expected=$OWNER" >&2
    return 1
  fi
  if [[ "$group" != "$GROUP" ]]; then
    echo "group_mismatch=$path actual=$group expected=$GROUP" >&2
    return 1
  fi
  mode_dec="$(mode_value "$path")"
  if (( (mode_dec & 0177) != 0 )); then
    echo "unsafe_file_permissions=$path mode=$mode requires no owner-exec/group/other permissions" >&2
    return 1
  fi

  echo "file_ok=$path owner=$owner group=$group mode=$mode"
  return 0
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --required-file)
      REQUIRED_FILES+=("${2:?--required-file requires a path}")
      shift 2
      ;;
    --optional-file)
      OPTIONAL_FILES+=("${2:?--optional-file requires a path}")
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

if [[ "${#REQUIRED_FILES[@]}" -eq 0 && "${#OPTIONAL_FILES[@]}" -eq 0 ]]; then
  REQUIRED_FILES=(
    "/home/kensan/Projects/Mirai-DX-Project/Civil-Weather-Water-Decision/backend/.env"
    "/home/kensan/.config/cwwd/db-backup.env"
    "/home/kensan/.config/cwwd/backup-export.passphrase"
    "/home/kensan/.cwwd-admin-password-20260713"
    "/home/kensan/.cloudflared/config-cwwd.yml"
    "/home/kensan/.cloudflared/07c9bda3-b4ad-46ae-8401-4b677de3c8a4.json"
  )
  OPTIONAL_FILES=(
    "/home/kensan/.config/cwwd/ops-alert.env"
  )
fi

failed=false
checked=0
missing_optional=0

for path in "${REQUIRED_FILES[@]}"; do
  if check_private_file "$path" true; then
    checked=$((checked + 1))
  else
    failed=true
  fi
done

for path in "${OPTIONAL_FILES[@]}"; do
  before="$checked"
  if output="$(check_private_file "$path" false)"; then
    printf '%s\n' "$output"
    if [[ "$output" == optional_file_missing=* ]]; then
      missing_optional=$((missing_optional + 1))
    else
      checked=$((checked + 1))
    fi
  else
    printf '%s\n' "$output" >&2
    failed=true
  fi
  : "$before"
done

echo "files_checked=$checked"
echo "optional_missing=$missing_optional"

if [[ "$failed" == true ]]; then
  fail "Secret/config file permission check failed" 3
fi

echo "status=ok"
