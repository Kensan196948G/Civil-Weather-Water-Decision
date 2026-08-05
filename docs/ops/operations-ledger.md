# 運用台帳（日次・週次・月次・四半期）

> 各項目は「実行した事実」だけを記録し、未実施を実施済みとしない。
> 証跡: journald / `/var/lib/cwwd/ops-status.json` / 本リポジトリ docs / 監査ログ。

## 1. 日次

| # | 項目 | 自動/手動 | 担当 | 確認内容 | 証跡 | 次回予定 |
| --- | --- | --- | --- | --- | --- | --- |
| D1 | アプリヘルス | 自動 | — | health/readyz 200 | `cwwd-app-health-check` journald | 5分毎（timer） |
| D2 | エッジ到達性 | 自動 | — | 公開URL 302 | `cwwd-public-edge-access-check` | 15分毎（timer） |
| D3 | バックアップ | 自動 | — | dump/export作成・鮮度 | `cwwd-db-backup*` journald | 日次（02:10/03:10） |
| D4 | 運用状態スナップショット | 自動 | — | systemd active/success | `/var/lib/cwwd/ops-status.json` | 5分毎（timer） |
| D5 | ダッシュボード・エラー目視 | 手動 | IT・DX担当 | 5xx・遅延・通知未確認の有無 | 運用日誌 | 翌営業日 09:00 |

## 2. 週次

| # | 項目 | 自動/手動 | 担当 | 確認内容 | 証跡 | 次回予定 |
| --- | --- | --- | --- | --- | --- | --- |
| W1 | 監視ログレビュー | 手動 | IT・DX担当 | failed unit・alert履歴 | 運用日誌 | 毎週月曜 |
| W2 | バックアップ実績確認 | 手動 | IT・DX担当 | 直近7日分の成功・サイズ | journald/backup dir | 毎週月曜 |
| W3 | CI/依存監査結果 | 自動＋手動 | 開発 | main CI・pip-audit結果 | GitHub Actions | 毎週確認 |
| W4 | 当番表・連絡先更新 | 手動 | IT・DX担当 | 第1〜3次担当の有効性 | 運用台帳 | 毎週月曜 |

## 3. 月次

| # | 項目 | 自動/手動 | 担当 | 確認内容 | 証跡 | 次回予定 |
| --- | --- | --- | --- | --- | --- | --- |
| M1 | 復元ドリル | 自動 | — | checksum・`pg_restore --list` 成功 | `cwwd-db-backup-restore-drill` | 毎日4:30（timer）・月次で内容レビュー |
| M2 | 通知試験 | 手動 | IT・DX担当 | ops-alert（journald必須、webhook設定時は実送信） | 運用日誌 | 2026-09-05 |
| M3 | 証明書・ドメイン期限 | 手動 | IT・DX担当 | Cloudflare証明書・カスタムドメイン | 運用日誌 | 2026-09-05 |
| M4 | 容量・課金確認 | 手動 | IT・DX担当 | Cloudflare/Neon 使用量・予算アラート | 運用日誌 | 2026-09-05 |
| M5 | 依存脆弱性レビュー | 自動＋手動 | 開発 | pip-audit・GitHub advisories | CI/運用日誌 | 2026-09-05 |
| M6 | ログ保存量・マスキング | 手動 | IT・DX担当 | journald容量・個人情報混入なし | 運用日誌 | 2026-09-05 |

## 4. 四半期

| # | 項目 | 自動/手動 | 担当 | 確認内容 | 証跡 | 次回予定 |
| --- | --- | --- | --- | --- | --- | --- |
| Q1 | 権限棚卸 | 手動 | IT・DX＋現場システム管理者 | ユーザー・`user_site_access`・DBロール | 棚卸表 | 2026-11-05 |
| Q2 | Secretsローテーション | 手動 | IT・DX | JWT_SECRET・ADMIN_PASSWORD・OIDC/Cloudflareトークン | ローテーション記録 | 2026-11-05 |
| Q3 | DR・復旧訓練 | 手動 | IT・DX＋開発 | 別筐体復元・RTO 4〜8h | 訓練記録 | 2026-11-05 |
| Q4 | ライセンス・EOL確認 | 手動 | 開発 | vendorライセンス・Python/Node EOL | 棚卸表 | 2026-11-05 |
| Q5 | SLOレビュー | 手動 | IT・DX＋開発 | SLI実績とSLO目標の比較・更新 | 本ドキュメント | 2026-11-05 |

## 5. 運用日誌（記入例）

```text
日時: YYYY-MM-DD HH:MM
項目: D5 / W1 等
担当: ______
確認結果: OK / NG（詳細: ____）
アクション: ____
```

## 6. 自動化状況

- 自動化済み: D1〜D4 / M1 / CI依存監査（systemd timer・GitHub Actions）
- 手動（自動化不可・要判断）: D5 / W1〜W4 / M2〜M6 / Q1〜Q5
