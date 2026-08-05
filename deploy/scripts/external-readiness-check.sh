#!/usr/bin/env bash
#
# 外部死活監視ヘルパー（Issue #116）
#
# 別ホスト/外部SaaSから以下を確認する（認証情報不要）:
#   1. 公開URLのHTTPステータス（Cloudflare Access 302）
#   2. TLS証明書の有効期限残日数
#   3. DNS名前解決
#
# 使い方:
#   ./external-readiness-check.sh
#   TARGET_URL=... TLS_MIN_DAYS=7 ./external-readiness-check.sh
#
# 失敗時は非0終了。cron/systemd から ops-alert.sh 等へ配管して通知する。
set -euo pipefail

TARGET_URL="${TARGET_URL:-https://cwwd.mirai-dx-platform.com/}"
TARGET_HOST="${TARGET_HOST:-cwwd.mirai-dx-platform.com}"
EXPECT_STATUS="${EXPECT_STATUS:-302}"
TLS_MIN_DAYS="${TLS_MIN_DAYS:-14}"

fail=0

echo "== external-readiness-check: ${TARGET_URL} =="

# 1) HTTPステータス
code="$(curl -sS -o /dev/null -w '%{http_code}' --max-time 30 "$TARGET_URL" || true)"
if [ "$code" = "$EXPECT_STATUS" ]; then
  echo "OK   HTTP status = ${code}"
else
  echo "NG   HTTP status = ${code} (expected ${EXPECT_STATUS})"
  fail=1
fi

# 2) TLS証明書の残日数
if command -v openssl >/dev/null 2>&1; then
  enddate="$(echo | timeout 20 openssl s_client -servername "$TARGET_HOST" \
    -connect "${TARGET_HOST}:443" 2>/dev/null | openssl x509 -noout -enddate 2>/dev/null \
    | sed 's/notAfter=//' || true)"
  if [ -n "$enddate" ]; then
    days="$(( ( $(date -d "$enddate" +%s) - $(date +%s) ) / 86400 ))"
    if [ "$days" -ge "$TLS_MIN_DAYS" ]; then
      echo "OK   TLS expires in ${days} days (${enddate})"
    else
      echo "NG   TLS expires in ${days} days (< ${TLS_MIN_DAYS})"
      fail=1
    fi
  else
    echo "NG   TLS enddate を取得できません"
    fail=1
  fi
else
  echo "WARN openssl が無いためTLSチェックをスキップ"
fi

# 3) DNS名前解決
if command -v getent >/dev/null 2>&1 && getent ahosts "$TARGET_HOST" >/dev/null 2>&1; then
  echo "OK   DNS resolves ${TARGET_HOST}"
else
  echo "NG   DNS does not resolve ${TARGET_HOST}"
  fail=1
fi

if [ "$fail" -ne 0 ]; then
  echo "RESULT: NG"
  exit 1
fi
echo "RESULT: OK"
