#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'USAGE'
Usage: ops-failed-units-report.sh [options]

Prints a secret-redacted diagnostic snapshot for failed cwwd-* systemd units.
  --lines N                 journal lines per failed unit (default: 20)
  --max-line-chars N        max chars per journal line after redaction (default: 500)
  --allow-failed-units      return 0 after reporting failed units
USAGE
}

LINES="${CWWD_FAILED_UNITS_REPORT_LINES:-20}"
MAX_LINE_CHARS="${CWWD_FAILED_UNITS_REPORT_MAX_LINE_CHARS:-500}"
ALLOW_FAILED_UNITS=false

fail() {
  echo "$1" >&2
  exit "${2:-2}"
}

is_positive_int() {
  [[ "$1" =~ ^[0-9]+$ && "$1" != "0" ]]
}

unit_property() {
  local unit="$1"
  local property="$2"
  systemctl show "$unit" --property="$property" --value 2>/dev/null || true
}

redact_and_prefix_logs() {
  local unit="$1"
  python3 -c '
from __future__ import annotations

import re
import sys

unit = sys.argv[1]
max_chars = int(sys.argv[2])


def redact(value: str) -> str:
    value = re.sub(r"https?://[^\s]+", "https://***", value)
    value = re.sub(r"postgres(?:ql)?(?:\+[A-Za-z0-9_]+)?://[^\s]+", "postgresql://***", value)
    value = re.sub(r"\bBearer\s+[A-Za-z0-9._~+/=-]+", "Bearer ***", value, flags=re.IGNORECASE)
    value = re.sub(r"\bBasic\s+[A-Za-z0-9._~+/=-]+", "Basic ***", value, flags=re.IGNORECASE)
    value = re.sub(
        r"([A-Za-z0-9_]*(?:WEBHOOK|DATABASE_URL|PASSWORD|TOKEN|SECRET|API_KEY|PASSPHRASE|PRIVATE_KEY)[A-Za-z0-9_]*=)[^\s]+",
        r"\1***",
        value,
        flags=re.IGNORECASE,
    )
    return value


for raw in sys.stdin:
    line = redact(raw.rstrip("\n"))
    if len(line) > max_chars:
        line = line[: max_chars - 15] + "...[truncated]"
    print(f"journal[{unit}]={line}")
' "$unit" "$MAX_LINE_CHARS"
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --lines)
      LINES="${2:?--lines requires a positive integer}"
      shift 2
      ;;
    --max-line-chars)
      MAX_LINE_CHARS="${2:?--max-line-chars requires a positive integer}"
      shift 2
      ;;
    --allow-failed-units)
      ALLOW_FAILED_UNITS=true
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

is_positive_int "$LINES" || fail "--lines must be a positive integer" 2
is_positive_int "$MAX_LINE_CHARS" || fail "--max-line-chars must be a positive integer" 2
command -v systemctl >/dev/null 2>&1 || fail "systemctl is required but was not found in PATH" 127
command -v journalctl >/dev/null 2>&1 || fail "journalctl is required but was not found in PATH" 127
command -v python3 >/dev/null 2>&1 || fail "python3 is required but was not found in PATH" 127

FAILED_UNITS="$(systemctl list-units --failed --plain --no-legend 'cwwd-*' 2>/dev/null || true)"
failed_count="$(printf '%s\n' "$FAILED_UNITS" | awk 'NF { count += 1 } END { print count + 0 }')"
echo "failed_units=$failed_count"

if [[ "$failed_count" == "0" ]]; then
  echo "status=ok"
  exit 0
fi

printf '%s\n' "$FAILED_UNITS" | awk '{ print $1 }' | while IFS= read -r unit; do
  [[ -n "$unit" ]] || continue
  echo "failed_unit=$unit"
  for property in LoadState ActiveState SubState Result ExecMainStatus NRestarts FragmentPath; do
    value="$(unit_property "$unit" "$property")"
    [[ -n "$value" ]] && echo "$unit.$property=$value"
  done
  journalctl -u "$unit" -n "$LINES" --no-pager --output=short-iso 2>/dev/null | redact_and_prefix_logs "$unit"
done

if [[ "$ALLOW_FAILED_UNITS" == true ]]; then
  echo "status=reported"
  exit 0
fi

fail "Failed cwwd systemd units are present" 3
