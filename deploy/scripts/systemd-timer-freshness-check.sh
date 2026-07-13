#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'USAGE'
Usage: systemd-timer-freshness-check.sh [options]

Checks that required CWWD systemd timers are active, enabled, scheduled, and
not stale. Timer specs use UNIT:MAX_AGE_SECONDS. When a timer has not fired yet,
it is accepted only when the next elapse is scheduled within its max age.

Options:
  --timer UNIT:MAX_AGE_SECONDS  override defaults; repeatable
  --now-epoch SECONDS           test hook; default current epoch
USAGE
}

DEFAULT_TIMERS=(
  cwwd-app-health-check.timer:900
  cwwd-public-edge-access-check.timer:1800
  cwwd-security-surface-check.timer:1800
  cwwd-network-exposure-check.timer:1800
  cwwd-cloudflared-config-check.timer:3600
  cwwd-systemd-unit-drift-check.timer:3600
  cwwd-secret-file-permission-check.timer:3600
  cwwd-ops-status.timer:3600
  cwwd-ops-status-json-export.timer:3600
  cwwd-ops-status-json-check.timer:3600
  cwwd-disk-space-check.timer:7200
  cwwd-db-backup-check.timer:7200
  cwwd-db-backup-export-check.timer:7200
  cwwd-db-backup.timer:172800
  cwwd-db-backup-export.timer:172800
  cwwd-db-backup-restore-drill.timer:172800
)

TIMERS=()
NOW_EPOCH="$(date +%s)"

fail() {
  echo "$1" >&2
  exit "${2:-2}"
}

is_positive_integer() {
  [[ "$1" =~ ^[1-9][0-9]*$ ]]
}

unit_property() {
  local unit="$1"
  local property="$2"
  systemctl show "$unit" --property="$property" --value 2>/dev/null || true
}

parse_time_epoch() {
  local value="$1"
  if [[ -z "$value" || "$value" == "n/a" ]]; then
    return 1
  fi
  date -d "$value" +%s 2>/dev/null || return 1
}

require_property() {
  local unit="$1"
  local property="$2"
  local expected="$3"
  local actual
  actual="$(unit_property "$unit" "$property")"
  [[ "$actual" == "$expected" ]] || fail "$unit $property mismatch: actual=${actual:-missing} expected=$expected" 3
}

parse_timer_spec() {
  local spec="$1"
  local timer="${spec%%:*}"
  local max_age="${spec##*:}"
  [[ "$timer" == *.timer && "$timer" != "$spec" ]] || fail "Invalid timer spec: $spec" 2
  is_positive_integer "$max_age" || fail "Invalid max age in timer spec: $spec" 2
  printf '%s:%s\n' "$timer" "$max_age"
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --timer)
      TIMERS+=("$(parse_timer_spec "${2:?--timer requires UNIT:MAX_AGE_SECONDS}")")
      shift 2
      ;;
    --now-epoch)
      NOW_EPOCH="${2:?--now-epoch requires seconds}"
      is_positive_integer "$NOW_EPOCH" || fail "--now-epoch must be a positive integer" 2
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

command -v systemctl >/dev/null 2>&1 || fail "systemctl is required but was not found in PATH" 127
command -v date >/dev/null 2>&1 || fail "date is required but was not found in PATH" 127

if [[ ${#TIMERS[@]} -eq 0 ]]; then
  TIMERS=("${DEFAULT_TIMERS[@]}")
fi

failed=false
checked=0

for spec in "${TIMERS[@]}"; do
  timer="${spec%%:*}"
  max_age="${spec##*:}"
  require_property "$timer" ActiveState active
  require_property "$timer" UnitFileState enabled

  last_value="$(unit_property "$timer" LastTriggerUSec)"
  next_value="$(unit_property "$timer" NextElapseUSecRealtime)"

  next_in="unknown"
  if next_epoch="$(parse_time_epoch "$next_value")"; then
    next_in=$((next_epoch - NOW_EPOCH))
    if (( next_in > max_age )); then
      echo "timer_next_elapse_too_far=$timer next_in_seconds=$next_in max_age_seconds=$max_age" >&2
      failed=true
      continue
    fi
  fi

  if last_epoch="$(parse_time_epoch "$last_value")"; then
    age=$((NOW_EPOCH - last_epoch))
    if (( age < -60 )); then
      echo "timer_last_trigger_in_future=$timer age_seconds=$age" >&2
      failed=true
      continue
    fi
    if (( age > max_age )); then
      echo "timer_stale=$timer age_seconds=$age max_age_seconds=$max_age" >&2
      failed=true
      continue
    fi
    echo "timer=$timer age_seconds=$age next_in_seconds=$next_in freshness=ok"
  else
    if [[ "$next_in" == "unknown" ]]; then
      echo "timer_missing_next_elapse=$timer" >&2
      failed=true
      continue
    fi
    if (( next_in < 0 )); then
      echo "timer_untriggered_and_overdue=$timer next_in_seconds=$next_in" >&2
      failed=true
      continue
    fi
    echo "timer=$timer last_trigger=not-yet next_in_seconds=$next_in freshness=scheduled"
  fi
  checked=$((checked + 1))
done

echo "timers_checked=$checked"

if [[ "$failed" == true ]]; then
  fail "systemd timer freshness check failed" 3
fi

echo "status=ok"
