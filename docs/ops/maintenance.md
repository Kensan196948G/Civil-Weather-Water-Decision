# 定期保守・更新・ライフサイクル管理

## 1. 対象と周期

| 領域 | 項目 | 周期 | 担当 | 自動化 |
| --- | --- | --- | --- | --- |
| 依存関係 | backend pip-audit | 毎PR・月次レビュー | 開発 | CI（自動） |
| 依存関係 | frontend/node・npm系 | 四半期 | 開発 | 手動 |
| OS/ランタイム | Ubuntu・Python・Node EOL | 四半期 | IT・DX | 手動 |
| ライセンス | vendorライセンス・MIT等 | 四半期 | 開発 | 手動 |
| 証明書 | Cloudflare TLS・ドメイン | 月次 | IT・DX | 手動＋外部監視（未設定） |
| Secrets | JWT_SECRET・ADMIN_PASSWORD・OIDC_CLIENT_SECRET・Cloudflare/Neonトークン | 四半期（インシデント時は即時） | IT・DX | 手動 |
| 権限 | ユーザー・user_site_access・DBロール | 四半期 | IT・DX＋現場管理者 | 手動 |
| 容量 | Cloudflare/Neon使用量・課金 | 月次 | IT・DX | 手動 |
| バックアップ | 保持期間（dump14日/export30日）・暗号化・復元 | 月次 | IT・DX | 一部自動 |

## 2. Secrets ローテーション手順（概要）

```text
1. JWT_SECRET: 新値を32バイト以上で生成 → backend/.env更新 → サービス再起動
   → 全ユーザーの再ログインが必要（既存JWTは失効）
2. ADMIN_PASSWORD: 新値へ更新（本番管理者のみ。秘密値は画面・ログへ出さない）
3. OIDC_CLIENT_SECRET: Entra管理画面で再発行 → backend/.env更新（切替時）
4. Cloudflare API Token / Neonパスワード: 各コンソールで再発行 → env file更新 → 確認
```

ローテーション後はログイン・API・バックアップ・監視が正常なことを確認し、運用台帳に記録する。

## 3. EOL・脆弱性ポリシー

- critical/high脆弱性は判明後 **7日以内** に対応方針決定（更新・回避・許容理由の記録）
- Python/NodeのEOL到達6ヶ月前に対象更新を計画
- 更新は互換性維持を優先し、CI全成功後に本番反映

## 4. ライセンス確認

- 主要vendor（React/Leaflet/Babel等）のライセンスは `frontend/vendor/*/LICENSE.*` に保管
- 新規依存追加時はライセンス・保守状況をPR本文へ明記

## 5. 使用量・予算

- Cloudflare: 無料/有料プランと使用量、Access利用者数
- Neon: ストレージ・コンピュート使用量（月次でダッシュボード確認）
- 課金アラート: 各コンソールで閾値設定（予算超過時はIT・DX担当へ通知）

> 現状: 月次確認の初回実施は 2026-09-05（運用台帳 M4 に記録）。
