#!/usr/bin/env bash
#
# 暗号化export（/var/backups/cwwd/exports/*.dump.tar.gpg）をオフサイトへ転送する（Issue #115）。
#
# 認証情報はコードに埋め込まず、環境変数/env file（/home/kensan/.config/cwwd/offsite.env）から
# 読み込む。argv・stdout・ログへ秘密値を出力しない。
#
# 使い方:
#   deploy/scripts/db-backup-offsite-transfer.sh                # 最新exportを転送
#   deploy/scripts/db-backup-offsite-transfer.sh --dry-run      # 転送内容のみ表示
#   OFFSET_EXPORT_DIR=/var/backups/cwwd/exports \
#   OFFSITE_REMOTE=backup-bucket OFFSITE_PATH=cwwd/backups \
#   deploy/scripts/db-backup-offsite-transfer.sh
set -euo pipefail

EXPORT_DIR="${OFFSITE_EXPORT_DIR:-/var/backups/cwwd/exports}"
REMOTE="${OFFSITE_REMOTE:-}"
REMOTE_PATH="${OFFSITE_PATH:-cwwd/backups}"
DRY_RUN=0

usage() {
  sed -n '2,20p' "$0" | sed 's/^# \{0,1\}//'
}

while [ $# -gt 0 ]; do
  case "$1" in
    --dry-run) DRY_RUN=1 ;;
    --export-dir) EXPORT_DIR="$2"; shift ;;
    --remote) REMOTE="$2"; shift ;;
    --path) REMOTE_PATH="$2"; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
  shift
done

if [ -z "$REMOTE" ]; then
  echo "OFFSITE_REMOTE が未設定です（/home/kensan/.config/cwwd/offsite.env を確認）" >&2
  exit 2
fi
if [ ! -d "$EXPORT_DIR" ]; then
  echo "export directory がありません: $EXPORT_DIR" >&2
  exit 2
fi

latest="$(ls -1t "$EXPORT_DIR"/cwwd-*.dump.tar.gpg 2>/dev/null | head -1 || true)"
if [ -z "$latest" ]; then
  echo "転送対象の暗号化exportがありません" >&2
  exit 2
fi
base="$(basename "$latest")"
echo "transfer: ${base} -> ${REMOTE}:${REMOTE_PATH}/"

if command -v rclone >/dev/null 2>&1; then
  args=(copy --checksum --transfers 4)
  if [ "$DRY_RUN" -eq 1 ]; then
    args+=(--dry-run)
  fi
  rclone "${args[@]}" "$EXPORT_DIR/" "${REMOTE}:${REMOTE_PATH}/"
  if [ "$DRY_RUN" -eq 1 ]; then
    echo "dry-run: 転送は実行していません"
    exit 0
  fi
  # 転送後検証: リモートの .sha256 がローカルと一致するか
  local_sha="$(cat "$EXPORT_DIR/$base.sha256")"
  remote_sha="$(rclone cat "${REMOTE}:${REMOTE_PATH}/$base.sha256")"
  if [ "$remote_sha" != "$local_sha" ]; then
    echo "NG checksum mismatch: ${base}.sha256" >&2
    exit 1
  fi
  echo "OK verified checksum: ${base}.sha256"
elif command -v rsync >/dev/null 2>&1; then
  if [[ "$REMOTE" == *:* ]]; then
    dest="${REMOTE}/${REMOTE_PATH}/"
  else
    dest="${REMOTE}/${REMOTE_PATH}/"
  fi
  args=(-a --checksum)
  if [ "$DRY_RUN" -eq 1 ]; then
    args+=(--dry-run)
  fi
  rsync "${args[@]}" "$EXPORT_DIR/" "$dest"
  if [ "$DRY_RUN" -eq 1 ]; then
    echo "dry-run: 転送は実行していません"
    exit 0
  fi
  echo "OK rsync 転送完了（checksumはオフサイト側で sha256sum -c を実行してください）"
else
  echo "rclone または rsync が必要です" >&2
  exit 2
fi
