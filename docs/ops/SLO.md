# SLI / SLO とアラート基準（運用定義）

> 対象: 本番 `https://cwwd.mirai-dx-platform.com`（Cloudflare Access 前面）。
> 本文書は初期値であり、実測データ（外部監視導入後）に基づき四半期ごとに見直す。

## 1. SLI（計測指標）

| SLI | 定義 | 計測方法 |
| --- | --- | --- |
| 可用性（エッジ） | 公開URLが302（Access）を返す割合 | `cwwd-public-edge-access-check` / 外部監視（未設定） |
| 可用性（アプリ） | `/health`・`/readyz` が200を返す割合 | `cwwd-app-health-check` |
| 応答時間 | 主要API（login・sites・dashboard）の p95 | ログ/外部監視（現状は目視） |
| エラー率 | 5xx応答の割合 | backendログ（journald） |
| バックアップ鮮度 | 最新dump/exportの経過時間 | `cwwd-db-backup-check` / `-export-check` |
| 復元可能性 | 月次復元ドリルの成功 | `cwwd-db-backup-restore-drill` |

## 2. SLO（初期目標）

| 指標 | 目標 |
| --- | --- |
| 営業時間可用性（7:00〜19:00 JST） | >= 99.0% / 月 |
| 主要API p95 応答 | <= 3秒（外部データ取得失敗時は欠測表示を優先） |
| 5xxエラー率 | < 1% / 5分 |
| RPO | 24時間以内（日次バックアップ） |
| RTO | 4〜8時間（手順書に沿った復旧） |
| 復元ドリル | 月1回成功 |

## 3. アラート閾値（初期値）

| 種別 | 閾値 | 重大度 | 通知 |
| --- | --- | --- | --- |
| アプリ/エッジ停止 | 連続3回チェック失敗（約15分） | alert | journald + Slack/Teams（設定時） |
| 5xxエラー率 | 5分間で1%超 | warning | journald + Slack/Teams（設定時） |
| バックアップ鮮度 | 最新dump 26h超（warning）/ 28h超（critical） | warning/alert | `cwwd-db-backup-failure@` |
| TLS証明書 | 残日数14日未満 | alert | 外部監視（未設定） |
| ディスク | 使用率85%超（warning）/ 90%超（alert） | warning/alert | `cwwd-disk-space-check` |
| systemd異常 | 監視対象unitがfailed | alert | `cwwd-ops-failure@` |

## 4. 通知・エスカレーション

| レベル | 担当 | 対応 | 目標 |
| --- | --- | --- | --- |
| 第1次 | IT・DX運用担当 | アラート受信・切分け・復旧 | 15分以内に認識 |
| 第2次 | 開発（backend/frontend） | アプリ起因の修正 | 1時間以内 |
| 第3次 | 現場システム管理者 | DB・Access・設定確認 | 必要に応じ即時 |

当番表は `docs/ops/operations-ledger.md` の週次項目で更新する。

## 5. 通知試験手順

```bash
# journald への送信確認（常時）
deploy/scripts/ops-alert.sh --title "通知試験" --message "SLO定義に基づくテスト" \
  --severity info --dry-run

# Slack/Teams を設定している場合（--dry-run を外して実送信、月1回実施）
deploy/scripts/ops-alert.sh --title "通知試験" --message "月次テスト" --severity info \
  --env-file /home/kensan/.config/cwwd/ops-alert.env
```

> 現状: 実webhook（Slack/Teams）は未設定のため、実送信は **NOT RUN**。
> 設定後に本手順を実施し、結果を運用台帳へ記録する。

## 6. 既知のギャップ

- 外部死活監視SaaS未設定（#116、手順は `docs/external-monitoring.md`）
- メトリクス集約（Prometheus等）なし。journald＋systemdチェックによる運用
- 実績データ不足のためSLOは初期目標（四半期レビューで更新）
