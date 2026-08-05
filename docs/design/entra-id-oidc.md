# Entra ID OIDC 連携 基本設計（外部評価 P0 / 詳細設計 §12）

## 1. 現状

- 認証はアプリ内ユーザー＋JWT（`users` / `revoked_tokens` / `login_attempts`）。
- 600名規模の社員・協力会社を想定すると、アカウントライフサイクル（入退社・異動・権限変更）の
  二重管理になるため、本番候補では Entra ID OIDC へ差し替える。
- 既存の HENNGE ONE 方針との整合確認が必要（要件 §9.3、詳細設計 §12）。

## 2. 目標

- 社員は Entra ID でログインし、パスワードはアプリ側で持たない
- 無効化・退職は Entra 側で即時反映（アプリ側はセッション失効・トークン検証で追従）
- Entra グループ→アプリロール（admin/tech_manager/site_manager/safety/viewer）のマッピング
- 既存の監査・JWT失効・試行制限と同等以上の防御を維持

## 3. 認証フロー（Authorization Code + PKCE）

```text
ブラウザ → /api/auth/oidc/authorize → Entra ID ログイン
  → Entra ID コールバック (/api/auth/oidc/callback)
  → アプリが id_token/access_token を検証（issuer/audience/exp/signature）
  → 既存ユーザーの email と突合（初回は auto-provision）
  → アプリJWT発行（既存の get_current_user / RBAC をそのまま使用）
```

## 4. 設定（環境変数）

```text
OIDC_ISSUER_URL=https://login.microsoftonline.com/{tenant}/v2.0
OIDC_CLIENT_ID=...
OIDC_CLIENT_SECRET=...          # Secret管理（.env / 本番Secret）
OIDC_SCOPES=openid profile email
OIDC_GROUP_ROLE_ADMIN=...
OIDC_GROUP_ROLE_TECH=...
OIDC_GROUP_ROLE_SITE=...
OIDC_GROUP_ROLE_SAFETY=...
OIDC_GROUP_ROLE_VIEWER=...
```

未設定時は現行のアプリ内認証にフォールバック（本番では両モード併用は禁止し、
`AUTH_MODE=oidc` を明示した場合のみ OIDC へ切替）。

## 5. ロールマッピング

Entra グループ名をコード化（`group:xxxx` 等）し、アプリのロールへ写像する。
ロールの一意性は `(user, site)` 単位の権限設計（`site-level-permissions.md`）と組み合わせ、
「所属現場が空なら閲覧不可」などを実現する。

## 6. セキュリティ要件

- `nonce` 検証・`state` 検証（CSRF防止）
- id_token の `iss` / `aud` / `exp` / 署名検証（公開鍵は OIDC discovery から取得、キャッシュ）
- JWT秘密鍵はアプリJWT用に引き続き必須（OIDCトークンとは別物）
- ログアウト時はアプリJWT失効＋Entra ログアウトURLへ誘導
- 監査: `oidc_login_success` / `oidc_login_failed` / `oidc_provision` を記録
- 強制MFAは Entra 側の条件付きアクセスで担保（アプリでは行わない）

## 7. 移行手順（案）

1. 開発環境で OIDC を有効化し、テストテナントでログイン確認
2. 本番は dual-auth 期間を設けず、`AUTH_MODE=oidc` へ切替（切替前に全ユーザーへ通知）
3. 既存ユーザーの email と新規 provision の整合確認
4. 監査・失効・試行制限の回帰テスト（CIに OIDC モックを追加）
5. Entra 側のアプリ登録・証明書/シークレット・条件付きアクセス設定は IT 部門と共同実施

## 8. 完了条件

- [ ] OIDC ログイン/ログアウトが CI（モック）と検証環境で成功
- [ ] Entra 側のアカウント無効化が5分以内にアプリへ反映
- [ ] グループ→ロールの同期が監査可能
- [ ] 既存の JWT失効・試行制限・RBAC テストが全パス
- [ ] HENNGE ONE 方針との整合確認完了
