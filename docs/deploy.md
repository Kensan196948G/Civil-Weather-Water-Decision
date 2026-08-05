# デプロイ手順 / Deployment Guide

詳細設計 §15（システム構成方針）/ §16（セキュリティ）に基づく。

## CI（GitHub Actions）

`.github/workflows/ci.yml` が push / PR(main) で実行:
- **backend**: `ruff check`（重大ルール）＋ `pytest`（スケジューラ/外部XMLは無効・ネット非依存）
- **frontend**: `node logic-smoke.mjs` / `node adapter-contract.cjs` / `api-config-policy.cjs` /
  `cdn-policy.cjs` / `vendor-assets-policy.cjs` / `python serve-proxy-policy.py`
- **e2e-smoke**: Playwright Firefox で local backend/frontend を起動しログイン〜運用状態表示〜ログアウトを確認
- **docker-build**: `docker compose config` 検証＋ backend / frontend イメージのビルド確認

## ローカル / 検証（Docker Compose）

```bash
cp .env.example .env   # JWT_SECRET 等を設定
docker compose up -d   # postgres + backend(+migrate&seed) + frontend
# ブラウザ: http://localhost:3000/?api=http://localhost:8000
# ホスト5432が使用中なら PG_HOST_PORT=15432 docker compose up -d
```

## 本番候補（Azure 例, 設計§15.2）

| レイヤー | サービス |
|---|---|
| フロント | Azure Static Web Apps もしくは Container Apps（`frontend` イメージ） |
| バックエンド | Azure Container Apps（`backend` イメージ。起動時に `alembic upgrade head`） |
| DB | Azure Database for PostgreSQL（`DATABASE_URL` を接続文字列に） |
| Secret | Azure Key Vault（`JWT_SECRET` / `ADMIN_PASSWORD` / `DATABASE_URL`） |
| 監視 | Azure Monitor / Application Insights |

代替: Cloudflare Pages（フロント静的）＋ コンテナ基盤（backend）。

## Entra ID OIDC 切替（#118）

PoC はアプリ内ユーザー＋JWT（`ENABLE_AUTH=true` / `JWT_SECRET`）です。
600名規模のアカウント統合時は `AUTH_MODE=oidc` へ切替え、Entra ID の
Authorization Code + PKCE でログインします（設計: `docs/design/entra-id-oidc.md`）。

切替手順:

1. Entra 側でアプリ登録・リダイレクトURI（`https://{host}/api/auth/oidc/callback`）・
   グループクレームを設定（IT 部門と共同）
2. `.env` へ `AUTH_MODE=oidc` と `OIDC_*` を設定（シークレットは Secret 管理）
3. アプリ起動時ガード（本番で必須項目未設定なら起動失敗）を確認
4. `GET /api/auth/oidc/status` が `enabled=true` を返すことを確認
5. ログイン・コールバック・ログアウトを検証環境（テストテナント）で実施

切替前に既存ユーザーの email と Entra アカウントの整合を確認し、
全ユーザーへ切替日時を通知してください。OIDC モードではアプリ内パスワード
ログイン（`/api/auth/login`）は 403 で拒否されます。

## 公開ドメイン（Tunnel開通済み・常駐化と本番化は未了）

Mirai-DX-Project 系の兄弟プロジェクトは `mirai-dx-platform.com` を共通ドメインとし、
**プロジェクトごとの個別 Cloudflare Tunnel ＋ 専用サブドメイン ＋ systemd常駐サービス**という構成で
公開している（例: `arcsphere.mirai-dx-platform.com`, `riskchecker.mirai-dx-platform.com`。
実機 `cloudflared tunnel list` / Cloudflare API で全プロジェクト個別Tunnel運用と確認済み）。

本プロジェクトは **`https://cwwd.mirai-dx-platform.com`**（Tunnel: `cwwd-civil-weather-water` /
ID `07c9bda3-b4ad-46ae-8401-4b677de3c8a4`）。2026-07-12 に人間実行で開通し、フォアグラウンド起動で
3ルート疎通（front 200 / `/health` / `/api/sites` 401認証ガード）を確認済み。手順の正本は
`deploy/cloudflared-setup-steps.md`（`route dns` は必ず `--config` 明示 — デフォルト `config.yml` の
別プロジェクトTunnel指定が優先される罠あり）。

- [x] `deploy/cloudflared-config.yml.example`（tunnel設定テンプレ）作成
- [x] Cloudflare Tunnel 作成・DNS ルーティング（`cloudflared tunnel route dns` ― 人間実行、2026-07-12）
- [x] state.json への `deploy_plan.public_url` 反映
- [x] backend/frontend/tunnel の systemd 常駐化（2026-07-12 適用済み・Issue #77。
      `cwwd-backend` 127.0.0.1:55019 / `cwwd-frontend` 127.0.0.1:34979 / `cwwd-tunnel`。
      OS起動時に3点セット自動起動。unit 実体の正本は `deploy/systemd/`）
- [x] `CORS_ORIGINS` を `https://cwwd.mirai-dx-platform.com` に更新（backend/.env、2026-07-23 適用済み）
- [x] エッジ側アクセス制御: Cloudflare Access アプリ `cwwd` / ポリシー `CWWD`
      （allow: `mirai-const.co.jp` ドメイン or 管理者メール、2026-07-12 人間作成・302リダイレクト実測確認済み）
- [x] 本番チェックリスト適用（下記。`APP_ENV=production` / `JWT_SECRET` / `SETTINGS_ENCRYPTION_KEY` /
      `ADMIN_PASSWORD` / `CORS_ORIGINS` を backend/.env に設定済み。秘密値は表示しない）
- [x] フロントエンド静的配信のハードニング（2026-08-01: セキュリティヘッダ / CSP report-only /
      CDN→vendor self-host / 同一オリジン /api proxy / loopback bind。PR #104 相当を包含）
- [x] backend API のセキュリティヘッダ付与と本番 docs 無効化（2026-08-01）
- [x] DBバックアップ自動化の障害復旧（2026-08-01: db-backup.env の認証情報を現行へ同期し、日次ダンプ再開）

## 本番デプロイ前チェックリスト（必須）

セキュリティ・ハードニングで起動ガードを実装済み。**以下を満たさないと本番(APP_ENV≠local)では起動失敗する**。

- [x] `APP_ENV=production`
- [x] `JWT_SECRET` を 32バイト以上のランダム値で設定（未設定/既定値は起動失敗）
- [x] `SETTINGS_ENCRYPTION_KEY`（AI APIキー等の設定値暗号化専用鍵、32バイト以上）を推奨設定。
      未設定でも `JWT_SECRET`（32バイト以上）から鍵導出するが、専用鍵の方が鍵ローテーションを
      認証トークンと分離できる。**専用鍵も `JWT_SECRET` も実用強度に満たない場合、設定画面からの
      AI APIキー保存は 422 で拒否される**（`crypto.encryption_is_strong` / #80 high-2）
- [x] `ENABLE_AUTH=true`（false は起動失敗）
- [x] `ADMIN_PASSWORD` を設定（本番は初期管理者のみ作成。デモユーザーは local 限定）
- [x] `DATABASE_URL` を PostgreSQL（Neon）に（`alembic upgrade head` は起動時自動）
- [x] `CORS_ORIGINS` を本番フロントのオリジンに限定（`*` をやめる）
- [x] HTTPS 終端（Cloudflare Tunnel / Access）
- [ ] `SLACK_WEBHOOK_URL` / `TEAMS_WEBHOOK_URL`（通知を外部送信する場合）
- [ ] Codex 対抗レビュー（認証/認可・DBスキーマ。CLAUDE.md 必須）

## 残課題（本番化の継続項目）

実装済み（旧残課題の解消）:

- 通知の定期ディスパッチ: 実装済み（#98。スケジューラから `notifications.dispatch`、
  `notification_deliveries` 台帳＋重複抑止。2026-08-01 本番で配送確認）
- トークン失効・ログイン試行制限: 実装済み（`revoked_tokens` / `login_attempts` のDB台帳。
  Redis ではなく PostgreSQL で複数プロセス・再起動をまたいで共有）
- GitHub branch protection: 適用済み（main は必須チェック5件＋ enforce_admins。PR経由のみマージ）
- Entra ID OIDC: **未実装**（要件・詳細設計上は本番候補。`.env.example` に OIDC 変数あり）

## 外部死活監視（#116）

- 監視対象: `https://cwwd.mirai-dx-platform.com/` の HTTP 302（Cloudflare Access）、
  TLS証明書残日数、DNS名前解決（詳細: `docs/external-monitoring.md`）
- ヘルパー: `deploy/scripts/external-readiness-check.sh`（別ホスト/外部SaaS用・認証情報不要）
- 設定手順・当番・復旧手順: `docs/external-monitoring.md`
- 現状: **外部SaaSアカウントと通知先は未設定**（IT・DX部門の設定作業が必要）

未実装・継続項目（外部評価 2026-08 に基づく優先順位）:

- 河川観測所マスタ・実測値の**自動取得**（本PRでマスタ/紐付け/手動実測の基盤を追加。
  水防災オープンデータ提供サービス等の接続は未着手）
- 現場別 WBGT 地点の自動選定（現状は `WBGT_STATION_CODE` 単一地点。2026年度 環境省 Web API 更新も併せて対応）
- 現場単位権限（協力会社向け role×site×action。設計は `docs/design/site-level-permissions.md`）
- オフサイトバックアップ転送（暗号化exportは実装済み。別筐体への転送・復元は未完了。
  転送スクリプト/systemd unitは整備済み。実転送・復元訓練は未完了。
  設計・手順は [backup-restore.md](./backup-restore.md#オフサイトバックアップ転送と復元検証issue-115)）
- 外部死活監視の実設定（手順・ヘルパーは `docs/external-monitoring.md` /
  `deploy/scripts/external-readiness-check.sh` に整備済み。外部SaaSのアカウント・通知先は要設定）
- Open-Meteo 商用利用条件の確定（法務・IT で契約要否・帰属表示・SLA を承認するまで要確認）
- 6工種の標準閾値の安全・技術部門レビューと版管理（`docs/threshold-safety-review.md`）
- 本番UI受入試験（Access認証後の主要シナリオ。基準は `docs/acceptance/poc-acceptance.md`）
