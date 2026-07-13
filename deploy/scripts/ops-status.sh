#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'USAGE'
Usage: ops-status.sh [--allow-failed-units] [--format text|json] [--json]

Prints a secret-free production operations snapshot for CWWD systemd services
and timers. The command fails when a required service/timer is not healthy or
when failed cwwd-* units are present, unless --allow-failed-units is set.
USAGE
}

ALLOW_FAILED_UNITS=false
FORMAT=text
SERVICES=(
  cwwd-backend.service
  cwwd-frontend.service
  cwwd-tunnel.service
)
TIMERS=(
  cwwd-cloudflared-config-check.timer
  cwwd-app-health-check.timer
  cwwd-public-edge-access-check.timer
  cwwd-security-surface-check.timer
  cwwd-network-exposure-check.timer
  cwwd-systemd-unit-drift-check.timer
  cwwd-systemd-timer-freshness-check.timer
  cwwd-secret-file-permission-check.timer
  cwwd-ops-status.timer
  cwwd-ops-status-json-export.timer
  cwwd-ops-status-json-check.timer
  cwwd-disk-space-check.timer
  cwwd-db-backup.timer
  cwwd-db-backup-check.timer
  cwwd-db-backup-export.timer
  cwwd-db-backup-export-check.timer
  cwwd-db-backup-restore-drill.timer
)
declare -A SERVICE_ACTIVE=()
declare -A SERVICE_RESULT=()
declare -A SERVICE_RESTARTS=()
declare -A TIMER_ACTIVE=()
declare -A TIMER_ENABLED=()
declare -A TIMER_LAST_TRIGGER=()
declare -A TIMER_NEXT_ELAPSE=()
declare -A TIMER_SERVICE_RESULT=()
FAILED_UNIT_NAMES=()
SNAPSHOT_UTC=""
ERROR_UNIT=""
ERROR_PROPERTY=""
ERROR_ACTUAL=""
ERROR_EXPECTED=""
ERROR_MESSAGE=""
REQUIRED_VALUE=""

fail() {
  echo "$1" >&2
  exit "${2:-2}"
}

json_escape() {
  local value="$1"
  value="${value//\\/\\\\}"
  value="${value//\"/\\\"}"
  value="${value//$'\n'/\\n}"
  value="${value//$'\r'/}"
  printf '%s' "$value"
}

json_string() {
  printf '"%s"' "$(json_escape "$1")"
}

emit_property() {
  [[ "$FORMAT" == "text" ]] && echo "$1=$2"
  true
}

unit_property() {
  local unit="$1"
  local property="$2"
  systemctl show "$unit" --property="$property" --value 2>/dev/null || true
}

require_property_value() {
  local unit="$1"
  local property="$2"
  local expected="$3"
  local actual
  actual="$(unit_property "$unit" "$property")"
  if [[ "$actual" != "$expected" ]]; then
    ERROR_UNIT="$unit"
    ERROR_PROPERTY="$property"
    ERROR_ACTUAL="${actual:-missing}"
    ERROR_EXPECTED="$expected"
    ERROR_MESSAGE="$unit $property mismatch"
    [[ "$FORMAT" == "json" ]] && print_json "failed"
    fail "$unit $property mismatch: actual=${actual:-missing} expected=$expected" 3
  fi
  REQUIRED_VALUE="$actual"
}

print_optional_property() {
  local unit="$1"
  local property="$2"
  local actual
  actual="$(unit_property "$unit" "$property")"
  [[ -n "$actual" ]] && emit_property "$unit.$property" "$actual"
}

print_json() {
  local status="$1"
  local first
  printf '{\n'
  printf '  "snapshot_utc": '
  json_string "$SNAPSHOT_UTC"
  printf ',\n'
  printf '  "services": [\n'
  first=true
  for service in "${SERVICES[@]}"; do
    [[ "$first" == true ]] || printf ',\n'
    first=false
    printf '    {"unit": '
    json_string "$service"
    printf ', "active_state": '
    json_string "${SERVICE_ACTIVE[$service]:-}"
    printf ', "result": '
    json_string "${SERVICE_RESULT[$service]:-}"
    printf ', "n_restarts": '
    json_string "${SERVICE_RESTARTS[$service]:-}"
    printf '}'
  done
  printf '\n  ],\n'
  printf '  "timers": [\n'
  first=true
  for timer in "${TIMERS[@]}"; do
    [[ "$first" == true ]] || printf ',\n'
    first=false
    printf '    {"unit": '
    json_string "$timer"
    printf ', "active_state": '
    json_string "${TIMER_ACTIVE[$timer]:-}"
    printf ', "unit_file_state": '
    json_string "${TIMER_ENABLED[$timer]:-}"
    printf ', "last_trigger": '
    json_string "${TIMER_LAST_TRIGGER[$timer]:-}"
    printf ', "next_elapse": '
    json_string "${TIMER_NEXT_ELAPSE[$timer]:-}"
    printf ', "service_result": '
    json_string "${TIMER_SERVICE_RESULT[$timer]:-}"
    printf '}'
  done
  printf '\n  ],\n'
  printf '  "failed_units": ['
  first=true
  for failed_unit in "${FAILED_UNIT_NAMES[@]}"; do
    [[ "$first" == true ]] || printf ', '
    first=false
    json_string "$failed_unit"
  done
  printf '],\n'
  printf '  "failed_units_count": %d,\n' "${#FAILED_UNIT_NAMES[@]}"
  if [[ -n "$ERROR_MESSAGE" ]]; then
    printf '  "error": {"message": '
    json_string "$ERROR_MESSAGE"
    printf ', "unit": '
    json_string "$ERROR_UNIT"
    printf ', "property": '
    json_string "$ERROR_PROPERTY"
    printf ', "actual": '
    json_string "$ERROR_ACTUAL"
    printf ', "expected": '
    json_string "$ERROR_EXPECTED"
    printf '},\n'
  fi
  printf '  "status": '
  json_string "$status"
  printf '\n}\n'
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --allow-failed-units)
      ALLOW_FAILED_UNITS=true
      shift
      ;;
    --format)
      FORMAT="${2:?--format requires text or json}"
      case "$FORMAT" in
        text|json) ;;
        *) fail "--format must be text or json" 2 ;;
      esac
      shift 2
      ;;
    --json)
      FORMAT=json
      shift
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

SNAPSHOT_UTC="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
emit_property "snapshot_utc" "$SNAPSHOT_UTC"

for service in "${SERVICES[@]}"; do
  require_property_value "$service" ActiveState active
  SERVICE_ACTIVE[$service]="$REQUIRED_VALUE"
  emit_property "$service.ActiveState" "${SERVICE_ACTIVE[$service]}"
  SERVICE_RESULT[$service]="$(unit_property "$service" Result)"
  [[ -n "${SERVICE_RESULT[$service]}" ]] && emit_property "$service.Result" "${SERVICE_RESULT[$service]}"
  SERVICE_RESTARTS[$service]="$(unit_property "$service" NRestarts)"
  [[ -n "${SERVICE_RESTARTS[$service]}" ]] && emit_property "$service.NRestarts" "${SERVICE_RESTARTS[$service]}"
done

for timer in "${TIMERS[@]}"; do
  require_property_value "$timer" ActiveState active
  TIMER_ACTIVE[$timer]="$REQUIRED_VALUE"
  emit_property "$timer.ActiveState" "${TIMER_ACTIVE[$timer]}"
  require_property_value "$timer" UnitFileState enabled
  TIMER_ENABLED[$timer]="$REQUIRED_VALUE"
  emit_property "$timer.UnitFileState" "${TIMER_ENABLED[$timer]}"
  TIMER_LAST_TRIGGER[$timer]="$(unit_property "$timer" LastTriggerUSec)"
  [[ -n "${TIMER_LAST_TRIGGER[$timer]}" ]] && emit_property "$timer.LastTriggerUSec" "${TIMER_LAST_TRIGGER[$timer]}"
  TIMER_NEXT_ELAPSE[$timer]="$(unit_property "$timer" NextElapseUSecRealtime)"
  [[ -n "${TIMER_NEXT_ELAPSE[$timer]}" ]] && emit_property "$timer.NextElapseUSecRealtime" "${TIMER_NEXT_ELAPSE[$timer]}"
  TIMER_SERVICE_RESULT[$timer]="$(unit_property "${timer%.timer}.service" Result)"
  [[ -n "${TIMER_SERVICE_RESULT[$timer]}" ]] && emit_property "${timer%.timer}.service.Result" "${TIMER_SERVICE_RESULT[$timer]}"
done

FAILED_UNITS="$(systemctl list-units --failed --plain --no-legend 'cwwd-*' 2>/dev/null || true)"
if [[ -n "$FAILED_UNITS" ]]; then
  while IFS= read -r failed_line; do
    [[ -n "$failed_line" ]] || continue
    FAILED_UNIT_NAMES+=("$(awk '{ print $1 }' <<< "$failed_line")")
  done <<< "$FAILED_UNITS"
  emit_property "failed_units" "${#FAILED_UNIT_NAMES[@]}"
  for failed_unit in "${FAILED_UNIT_NAMES[@]}"; do
    emit_property "failed_unit" "$failed_unit"
  done
  if [[ "$ALLOW_FAILED_UNITS" == false ]]; then
    [[ "$FORMAT" == "json" ]] && print_json "failed"
    fail "Failed cwwd systemd units are present" 3
  fi
else
  emit_property "failed_units" "0"
fi

if [[ "$FORMAT" == "json" ]]; then
  print_json "ok"
else
  echo "status=ok"
fi
