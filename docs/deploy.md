# デプロイ手順 / Deployment Guide

詳細設計 §15（システム構成方針）/ §16（セキュリティ）に基づく。

## CI（GitHub Actions）

`.github/workflows/ci.yml` が push / PR(main) で実行:
- **backend**: `ruff check`（重大ルール）＋ `pytest`（スケジューラ/外部XMLは無効・ネット非依存）
- **frontend**: `node logic-smoke.mjs` / `node adapter-contract.cjs`
- **docker-build**: backend / frontend イメージのビルド確認

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

## 本番デプロイ前チェックリスト（必須）

セキュリティ・ハードニングで起動ガードを実装済み。**以下を満たさないと本番(APP_ENV≠local)では起動失敗する**。

- [ ] `APP_ENV=production`
- [ ] `JWT_SECRET` を 32バイト以上のランダム値で設定（未設定/既定値は起動失敗）
- [ ] `ENABLE_AUTH=true`（false は起動失敗）
- [ ] `ADMIN_PASSWORD` を設定（本番は初期管理者のみ作成。デモユーザーは local 限定）
- [ ] `DATABASE_URL` を PostgreSQL に（`alembic upgrade head` は起動時自動）
- [ ] `CORS_ORIGINS` を本番フロントのオリジンに限定（`*` をやめる）
- [ ] HTTPS 終端（リバースプロキシ / マネージド）
- [ ] `SLACK_WEBHOOK_URL` / `TEAMS_WEBHOOK_URL`（通知を外部送信する場合）
- [ ] Codex 対抗レビュー（認証/認可・DBスキーマ。CLAUDE.md 必須）

## 残課題（本番化の継続項目）

- Entra ID OIDC（簡易認証からの差し替え, §12）
- 通知の定期ディスパッチ（スケジューラから `notifications.dispatch`）
- トークン失効リスト、ログイン試行のRedis集約
- バックアップ/リストア手順（DBダンプ, §17）、E2Eテスト
- CDN（React/Leaflet/Fonts）の self-host（オフライン/プロキシ環境）
