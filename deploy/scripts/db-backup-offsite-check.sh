#!/usr/bin/env bash
#
# オフサイト転送の鮮度・checksum監視（Issue #115）。
# 最新export（ファイル名のタイムスタンプ）が許容時間内かつローカルとchecksum一致を確認する。
#
# 使い方:
#   deploy/scripts/db-backup-offsite-check.sh
#   OFFSITE_MAX_AGE_HOURS=28 deploy/scripts/db-backup-offsite-check.sh
set -euo pipefail

EXPORT_DIR="${OFFSITE_EXPORT_DIR:-/var/backups/cwwd/exports}"
REMOTE="${OFFSITE_REMOTE:-}"
REMOTE_PATH="${OFFSITE_PATH:-cwwd/backups}"
MAX_AGE_HOURS="${OFFSITE_MAX_AGE_HOURS:-28}"

if [ -z "$REMOTE" ]; then
  echo "OFFSITE_REMOTE が未設定です" >&2
  exit 2
fi
if ! command -v rclone >/dev/null 2>&1; then
  echo "rclone が必要です" >&2
  exit 2
fi

remote_latest="$(
  rclone lsf "${REMOTE}:${REMOTE_PATH}/" --files-only --format tp \
    | grep -E '\.dump\.tar\.gpg$' | sort -r | head -1 | awk '{print $1, $2}'
)"
if [ -z "$remote_latest" ]; then
  echo "NG オフサイトに export がありません" >&2
  exit 1
fi

remote_name="$(echo "$remote_latest" | awk '{print $2}')"
remote_ts="$(echo "$remote_latest" | awk '{print $1}')"
now="$(date +%s)"
remote_epoch="$(date -d "$remote_ts" +%s 2>/dev/null || echo 0)"
age_hours="$(( (now - remote_epoch) / 3600 ))"

if [ "$remote_epoch" -eq 0 ] || [ "$age_hours" -gt "$MAX_AGE_HOURS" ]; then
  echo "NG オフサイト最新exportが ${age_hours}h 前（許容 ${MAX_AGE_HOURS}h）: ${remote_name}" >&2
  exit 1
fi

local_sha="$(cat "$EXPORT_DIR/$remote_name.sha256" 2>/dev/null || true)"
remote_sha="$(rclone cat "${REMOTE}:${REMOTE_PATH}/$remote_name.sha256" 2>/dev/null || true)"
if [ -z "$local_sha" ] || [ "$local_sha" != "$remote_sha" ]; then
  echo "NG checksum不一致: ${remote_name}.sha256" >&2
  exit 1
fi

echo "OK offsite latest=${remote_name} age=${age_hours}h checksum=OK"
