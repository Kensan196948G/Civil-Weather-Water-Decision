# MVP/Prototype 環境ガイド（2026-08-13）

> 本番（`cwwd.mirai-dx-platform.com`）と完全分離した、関係者レビュー用の MVP 環境です。
> ダミーデータのみで運用し、本番デプロイ・本番DB・本番Secrets・実データは対象外です。

## 1. アクセス

| 種別 | URL | 認証 |
|---|---|---|
| MVP/Prototype | `https://cwwd-mvp.mirai-dx-platform.com/` | Cloudflare Access（許可メールのみ・24hセッション） |
| 本番 | `https://cwwd.mirai-dx-platform.com/` | Cloudflare Access（既存・変更なし） |

MVP では API ドキュメント（`/docs` / `/redoc` / `/openapi.json`）も公開しており、
Access ログイン後に API をその場で確認できます（本番は無効化）。

## 2. ログイン（ロール別デモアカウント）

| ユーザー名 | ロール | パスワード |
|---|---|---|
| admin | 管理者 | `ADMIN_PASSWORD`（`backend/.env.mvp` に保持・毎回ランダム） |
| tanaka | 技術管理者 | pass1234 |
| yamada | 現場管理者 | pass1234 |
| takahashi | 安全担当 | pass1234 |
| viewer | 閲覧 | pass1234 |

デモアカウントは共有パスワード・架空ユーザーです。MVP 専用 URL は Cloudflare Access
で許可メールのみに限定されているため、一般公開されません。デモデータは全て架空値です
（実在の現場名・個人情報なし）。

## 3. 構成（本番と分離）

```text
Cloudflare Access(cwwd-mvp) → Tunnel(cwwd-mvp-civil-weather-water)
  → backend 127.0.0.1:55119（APP_ENV=mvp）
      └→ PostgreSQL16 コンテナ cwwd-mvp-pg（127.0.0.1:15433 / volume cwwd_mvp_pgdata）
  → frontend 127.0.0.1:35179（serve.py、同一オリジン /api proxy）
```

systemd: `cwwd-mvp-backend` / `cwwd-mvp-frontend` / `cwwd-mvp-tunnel`
（unit 正本は `deploy/systemd/`）。本番は `cwwd-backend`(55019)/`cwwd-frontend`(34979)/
`cwwd-tunnel` で別ポート・別トンネル・別DB（Neon）です。

## 4. 設定とシークレット

- 環境ファイル: `backend/.env.mvp`（git管理外。`.gitignore` の `.env.*` に該当）
  - `APP_ENV=mvp` のため本番ガード（PG必須・JWT_SECRET 32B+・暗号鍵・ADMIN_PASSWORD・
    CORS 限定）が有効。`docs` は `production` 以外のため有効。
  - `SEED_DEMO_DATA=true` / `SEED_DEMO_USERS=true` でダミーデータとロール別デモユーザーを投入。
- トンネル設定: `~/.cloudflared/config-cwwd-mvp.yml`（正本テンプレート:
  `deploy/cloudflared-mvp-config.yml.example`）
- DB パスワード・JWT・暗号鍵・管理者パスワードはランダム生成で `.env.mvp` のみに保持
  （リポジトリ・ログ・PR に出力しない）。

## 5. 操作

```bash
# 状態確認
systemctl status cwwd-mvp-backend cwwd-mvp-frontend cwwd-mvp-tunnel
curl -s http://127.0.0.1:55119/health
curl -s http://127.0.0.1:55119/readyz

# ダミーデータ再投入（DB初期化。現データは破棄される）
sudo systemctl stop cwwd-mvp-backend cwwd-mvp-frontend
docker stop cwwd-mvp-pg && docker rm cwwd-mvp-pg
docker volume rm cwwd_mvp_pgdata
docker run -d --name cwwd-mvp-pg --restart unless-stopped \
  -e POSTGRES_DB=cwwd_mvp -e POSTGRES_USER=cwwd_mvp \
  -e POSTGRES_PASSWORD="$(grep -oP 'postgresql\+psycopg2://cwwd_mvp:\K[^@]+' backend/.env.mvp)" \
  -p 127.0.0.1:15433:5432 -v cwwd_mvp_pgdata:/var/lib/postgresql/data postgres:16
sudo systemctl start cwwd-mvp-backend cwwd-mvp-frontend cwwd-mvp-tunnel
```

## 6. 廃止（レビュー終了後）

```bash
sudo systemctl disable --now cwwd-mvp-tunnel cwwd-mvp-frontend cwwd-mvp-backend
sudo rm /etc/systemd/system/cwwd-mvp-{backend,frontend,tunnel}.service
sudo systemctl daemon-reload
docker rm -f cwwd-mvp-pg && docker volume rm cwwd_mvp_pgdata
cloudflared tunnel cleanup cwwd-mvp-civil-weather-water   # 必要時
# Cloudflare ダッシュボード/API で DNS cwwd-mvp と Access アプリ cwwd-mvp を削除
```

本番（cwwd-* サービス・Neon・cwwd.mirai-dx-platform.com）には一切影響しません。
