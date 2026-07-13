#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'USAGE'
Usage: ops-alert.sh --title TEXT --message TEXT [--severity info|warning|alert] [--env-file PATH] [--dry-run]

Sends an operational alert to journald and, when configured, Slack/Teams webhooks.
Webhook URLs are read only from environment or --env-file and are never printed.
USAGE
}

TITLE=""
MESSAGE=""
SEVERITY="alert"
ENV_FILE=""
DRY_RUN=false
MAX_CHARS="${OPS_ALERT_MAX_CHARS:-3500}"

load_env_file() {
  local file="$1"
  local line key value
  while IFS= read -r line || [[ -n "$line" ]]; do
    line="${line%$'\r'}"
    [[ -z "$line" || "$line" == \#* || "$line" != *=* ]] && continue
    key="${line%%=*}"
    value="${line#*=}"
    [[ "$key" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]] || continue
    case "$key" in
      SLACK_WEBHOOK_URL|TEAMS_WEBHOOK_URL|OPS_ALERT_TIMEOUT_SECONDS)
        printf -v "$key" '%s' "$value"
        export "$key"
        ;;
    esac
  done < "$file"
}

mode_value() {
  printf '%d' "$((8#$(stat -c '%a' "$1")))"
}

require_private_file() {
  local path="$1"
  local mode
  mode="$(mode_value "$path")"
  if (( (mode & 0177) != 0 )); then
    echo "Unsafe alert env file permissions: $path mode=$(stat -c '%a' "$path") requires no owner-exec/group/other permissions" >&2
    exit 2
  fi
}

redact_text() {
  sed -E \
    -e 's#https?://[^[:space:]]+#https://***#g' \
    -e 's#([A-Za-z0-9_]*(WEBHOOK|DATABASE_URL|PASSWORD|TOKEN|SECRET)[A-Za-z0-9_]*=)[^[:space:]]+#\1***#g'
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --title)
      TITLE="${2:?--title requires text}"
      shift 2
      ;;
    --message)
      MESSAGE="${2:?--message requires text}"
      shift 2
      ;;
    --severity)
      SEVERITY="${2:?--severity requires info, warning, or alert}"
      shift 2
      ;;
    --env-file)
      ENV_FILE="${2:?--env-file requires a path}"
      shift 2
      ;;
    --dry-run)
      DRY_RUN=true
      shift
      ;;
    --max-chars)
      MAX_CHARS="${2:?--max-chars requires a positive integer}"
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

[[ -n "$TITLE" ]] || { echo "--title is required" >&2; exit 2; }
[[ -n "$MESSAGE" ]] || { echo "--message is required" >&2; exit 2; }
[[ "$MAX_CHARS" =~ ^[0-9]+$ && "$MAX_CHARS" != "0" ]] || { echo "--max-chars must be a positive integer" >&2; exit 2; }
case "$SEVERITY" in
  info|warning|alert) ;;
  *) echo "--severity must be info, warning, or alert" >&2; exit 2 ;;
esac

if [[ -n "$ENV_FILE" ]]; then
  [[ -f "$ENV_FILE" ]] || { echo "Alert env file not found: $ENV_FILE" >&2; exit 2; }
  require_private_file "$ENV_FILE"
  load_env_file "$ENV_FILE"
fi

TITLE="$(printf '%s' "$TITLE" | redact_text)"
MESSAGE="$(printf '%s' "$MESSAGE" | redact_text)"

if [[ "$DRY_RUN" == false ]]; then
  if command -v systemd-cat >/dev/null 2>&1; then
    printf '%s\n%s\n' "$TITLE" "$MESSAGE" | systemd-cat -p "$SEVERITY" -t cwwd-ops-alert
  else
    printf '%s: %s\n%s\n' "$SEVERITY" "$TITLE" "$MESSAGE" >&2
  fi
fi

OPS_ALERT_TITLE="$TITLE" \
OPS_ALERT_MESSAGE="$MESSAGE" \
OPS_ALERT_SEVERITY="$SEVERITY" \
OPS_ALERT_DRY_RUN="$DRY_RUN" \
OPS_ALERT_MAX_CHARS="$MAX_CHARS" \
python3 - <<'PY'
from __future__ import annotations

import json
import os
import re
import sys
import urllib.error
import urllib.request

title = os.environ["OPS_ALERT_TITLE"]
message = os.environ["OPS_ALERT_MESSAGE"]
severity = os.environ["OPS_ALERT_SEVERITY"]
dry_run = os.environ["OPS_ALERT_DRY_RUN"] == "true"
max_chars = int(os.environ["OPS_ALERT_MAX_CHARS"])
timeout = float(os.environ.get("OPS_ALERT_TIMEOUT_SECONDS") or "10")
targets = [
    ("slack", os.environ.get("SLACK_WEBHOOK_URL") or ""),
    ("teams", os.environ.get("TEAMS_WEBHOOK_URL") or ""),
]

def redact(value: str) -> str:
    value = re.sub(r"https?://[^\s]+", "https://***", value)
    value = re.sub(
        r"([A-Za-z0-9_]*(?:WEBHOOK|DATABASE_URL|PASSWORD|TOKEN|SECRET)[A-Za-z0-9_]*=)[^\s]+",
        r"\1***",
        value,
        flags=re.IGNORECASE,
    )
    return value

text = redact(f"[{severity.upper()}] {title}\n{message}")
if len(text) > max_chars:
    text = text[: max_chars - 20] + "\n...[truncated]"
payload = json.dumps({"text": text}, ensure_ascii=False).encode("utf-8")

sent = 0
failed = 0
configured = 0
for name, url in targets:
    if not url:
        continue
    configured += 1
    if dry_run:
        continue
    req = urllib.request.Request(
        url,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            if response.status >= 400:
                failed += 1
            else:
                sent += 1
    except (OSError, urllib.error.URLError, urllib.error.HTTPError):
        failed += 1

print(f"journald={'skipped' if dry_run else 'logged'}")
print(f"webhook_targets={configured}")
if dry_run:
    print("webhook_send=skipped")
elif configured:
    print(f"webhook_sent={sent}")
    print(f"webhook_failed={failed}")
if failed:
    sys.exit(1)
PY
