#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'USAGE'
Usage: systemd-unit-drift-check.sh [options]

Checks that applied CWWD systemd units match repository copies:
  --repo-dir PATH       default deploy/systemd under current working directory
  --system-dir PATH     default /etc/systemd/system
  --ignore-missing-units LIST
                        comma/space separated basenames to skip when missing
                        (e.g. units not deployed until external config ready)
  --allow-extra-units   do not fail on extra cwwd units in system-dir
USAGE
}

REPO_DIR="${CWWD_SYSTEMD_REPO_DIR:-deploy/systemd}"
SYSTEM_DIR="${CWWD_SYSTEMD_SYSTEM_DIR:-/etc/systemd/system}"
ALLOW_EXTRA_UNITS=false
IGNORE_MISSING=""

fail() {
  echo "$1" >&2
  exit "${2:-2}"
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --repo-dir)
      REPO_DIR="${2:?--repo-dir requires a path}"
      shift 2
      ;;
    --system-dir)
      SYSTEM_DIR="${2:?--system-dir requires a path}"
      shift 2
      ;;
    --allow-extra-units)
      ALLOW_EXTRA_UNITS=true
      shift
      ;;
    --ignore-missing-units)
      IGNORE_MISSING="${2:?--ignore-missing-units requires a list}"
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

[[ -d "$REPO_DIR" ]] || fail "repo systemd directory not found: $REPO_DIR" 2
[[ -d "$SYSTEM_DIR" ]] || fail "systemd directory not found: $SYSTEM_DIR" 2
command -v sha256sum >/dev/null 2>&1 || fail "sha256sum is required but was not found in PATH" 127

repo_units="$(
  find "$REPO_DIR" -maxdepth 1 -type f \( -name 'cwwd-*.service' -o -name 'cwwd-*.timer' \) \
    -printf '%f\n' | sort
)"

ignore_set="$(printf '%s' "$IGNORE_MISSING" | tr ',' ' ' | tr ' ' '\n' | sed '/^$/d' | sort -u)"
system_units="$(
  find "$SYSTEM_DIR" -maxdepth 1 -type f \( -name 'cwwd-*.service' -o -name 'cwwd-*.timer' \) \
    -printf '%f\n' | sort
)"

[[ -n "$repo_units" ]] || fail "No cwwd systemd units found in repo directory" 2

failed=false
checked=0
missing=0
changed=0
extra=0

while IFS= read -r unit; do
  [[ -n "$unit" ]] || continue
  repo_path="$REPO_DIR/$unit"
  system_path="$SYSTEM_DIR/$unit"
  if [[ ! -f "$system_path" ]]; then
    if grep -Fxq "$unit" <<< "$ignore_set"; then
      echo "skipped_missing_unit=$unit (ignore-list)" >&2
      continue
    fi
    echo "missing_system_unit=$unit" >&2
    missing=$((missing + 1))
    failed=true
    continue
  fi
  repo_hash="$(sha256sum "$repo_path" | awk '{ print $1 }')"
  system_hash="$(sha256sum "$system_path" | awk '{ print $1 }')"
  if [[ "$repo_hash" != "$system_hash" ]]; then
    echo "unit_drift=$unit" >&2
    changed=$((changed + 1))
    failed=true
  else
    checked=$((checked + 1))
  fi
done <<< "$repo_units"

if [[ "$ALLOW_EXTRA_UNITS" == false ]]; then
  while IFS= read -r unit; do
    [[ -n "$unit" ]] || continue
    if ! grep -Fxq "$unit" <<< "$repo_units"; then
      echo "extra_system_unit=$unit" >&2
      extra=$((extra + 1))
      failed=true
    fi
  done <<< "$system_units"
fi

echo "repo_units=$(wc -l <<< "$repo_units")"
echo "system_units=$(wc -l <<< "$system_units")"
echo "units_checked=$checked"
echo "missing_units=$missing"
echo "changed_units=$changed"
echo "extra_units=$extra"

if [[ "$failed" == true ]]; then
  fail "systemd unit drift detected" 3
fi

echo "status=ok"
