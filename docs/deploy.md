# デプロイ手順 / Deployment Guide

詳細設計 §15（システム構成方針）/ §16（セキュリティ）に基づく。

## CI（GitHub Actions）

`.github/workflows/ci.yml` が push / PR(main) で実行:
- **backend**: `ruff check`（重大ルール）＋ `pytest`（スケジューラ/外部XMLは無効・ネット非依存）
- **security**: `pip-audit` ＋ deploy shell scripts の `bash -n` ＋ systemd unit/timer の `systemd-analyze verify`
- **frontend**: `node logic-smoke.mjs` / `node adapter-contract.cjs` / `node api-config-policy.cjs` / `python3 serve-proxy-policy.py` / `node cdn-policy.cjs` / `node vendor-assets-policy.cjs`
- **e2e-smoke**: Playwright Firefox で local backend/frontend を起動し、frontend 同一オリジン `/api` proxy 経由でログイン→運用状態表示→ログアウトを検証
- **docker-build**: `docker compose config --quiet` / backend・frontend イメージのビルド確認

## ローカル / 検証（Docker Compose）

```bash
cp .env.example .env
POSTGRES_PASSWORD="$(openssl rand -base64 24)"
perl -0pi -e 's/^POSTGRES_PASSWORD=.*/POSTGRES_PASSWORD=$ENV{POSTGRES_PASSWORD}/m' .env
docker compose up -d   # postgres + backend(+migrate&seed) + frontend
# ブラウザ: http://localhost:3000/ （frontend が /api を backend:8000 へ proxy）
# ホスト5432が使用中なら PG_HOST_PORT=15432 docker compose up -d
```

Docker Compose は local 検証専用です。Postgres / backend / frontend のホスト公開は
`127.0.0.1` に限定し、`POSTGRES_PASSWORD` 未設定では起動しません。frontend は backend `/health`
healthcheck の `service_healthy` を待ってから起動します。

## 本番候補（Azure 例, 設計§15.2）

| レイヤー | サービス |
|---|---|
| フロント | Azure Static Web Apps もしくは Container Apps（`frontend` イメージ） |
| バックエンド | Azure Container Apps（`backend` イメージ。起動時に `alembic upgrade head`） |
| DB | Azure Database for PostgreSQL（`DATABASE_URL` を接続文字列に） |
| Secret | Azure Key Vault（`JWT_SECRET` / `SETTINGS_ENCRYPTION_KEY` / `SETTINGS_ENCRYPTION_PREVIOUS_KEYS`（ローテーション中のみ） / `ADMIN_PASSWORD` / `DATABASE_URL`） |
| 監視 | Azure Monitor / Application Insights |

代替: Cloudflare Pages（フロント静的）＋ コンテナ基盤（backend）。

## 公開ドメイン（Tunnel開通済み・常駐化と本番化は未了）

Mirai-DX-Project 系の兄弟プロジェクトは `mirai-dx-platform.com` を共通ドメインとし、
**プロジェクトごとの個別 Cloudflare Tunnel ＋ 専用サブドメイン ＋ systemd常駐サービス**という構成で
公開している（例: `arcsphere.mirai-dx-platform.com`, `riskchecker.mirai-dx-platform.com`。
実機 `cloudflared tunnel list` / Cloudflare API で全プロジェクト個別Tunnel運用と確認済み）。

本プロジェクトは **`https://cwwd.mirai-dx-platform.com`**（Tunnel: `cwwd-civil-weather-water` /
ID `07c9bda3-b4ad-46ae-8401-4b677de3c8a4`）。2026-07-12 に人間実行で開通し、フォアグラウンド起動で
3ルート疎通（front 200 / `/health` / `/api/sites` 401認証ガード）を確認済み。現在のTunnel ingressは
`^/api(/.*)?$`、`^/health$`、`^/readyz$` を backend へ先着ルーティングし、無効化済みdocs/OpenAPI
（`/docs`, `/redoc`, `/openapi.json`）は edge 404、それ以外をfrontendへ流す。手順の正本は
`deploy/cloudflared-setup-steps.md`（`route dns` は必ず `--config` 明示 — デフォルト `config.yml` の
別プロジェクトTunnel指定が優先される罠あり）。

- [x] `deploy/cloudflared-config.yml.example`（tunnel設定テンプレ）作成
- [x] Cloudflare Tunnel 作成・DNS ルーティング（`cloudflared tunnel route dns` ― 人間実行、2026-07-12）
- [x] state.json への `deploy_plan.public_url` 反映
- [x] backend/frontend/tunnel の systemd 常駐化（2026-07-12 適用済み・Issue #77。
      `cwwd-backend` 127.0.0.1:55019 / `cwwd-frontend` 127.0.0.1:34979 / `cwwd-tunnel`。
      OS起動時に3点セット自動起動。unit 実体の正本は `deploy/systemd/`。
      2026-07-13 に正本を loopback bind へ更新し、実機へ反映済み）
      `cwwd-frontend` は `CW_BACKEND_PROXY_BASE=http://127.0.0.1:55019` を持ち、loopback 直アクセス時だけ
      `/api`、`/health`、`/readyz` を同一オリジンで backend へ proxy する。
      Cloudflare Tunnel の public `/api` は引き続き frontend より先に backend へ直接ルーティングする。
- [x] systemd sandbox baseline（NoNewPrivileges / PrivateTmp / PrivateDevices / ProtectSystem=full /
      ProtectHome=read-only / kernel・cgroup・clock・hostname保護 / capability空化 / UMask=0077）。
      frontend/tunnel は `MemoryDenyWriteExecute=true`、backend は native extension canary 後の追加候補。
      syscall filter は Python/cloudflared 互換性確認後の追加候補。
- [x] 本番起動ガードで `CORS_ORIGINS=*` を拒否（`https://cwwd.mirai-dx-platform.com` 等の HTTPS オリジン指定必須）
- [x] エッジ側アクセス制御: Cloudflare Access アプリ `cwwd` / ポリシー `CWWD`
      （allow: `mirai-const.co.jp` ドメイン or 管理者メール、2026-07-12 人間作成・302リダイレクト実測確認済み）
- [x] 本番チェックリストの必須環境変数を実機 `backend/.env` へ適用（2026-07-13）。
      `APP_ENV=production` / HTTPS限定CORS / 強JWT / 設定値暗号化専用鍵 / 初期adminパスワード。
      初期adminパスワードは `/home/kensan/.cwwd-admin-password-20260713`（0600）に保存

## 本番デプロイ前チェックリスト（必須）

セキュリティ・ハードニングで起動ガードを実装済み。**以下を満たさないと本番(APP_ENV≠local)では起動失敗する**。

- [x] `APP_ENV=production`
- [x] `JWT_SECRET` を 32バイト以上のランダム値で設定（未設定/既定値は起動失敗）
- [x] `SETTINGS_ENCRYPTION_KEY` を 32バイト以上のランダム値で設定（本番は未設定/短すぎる値で起動失敗）。
      AI APIキー等の設定値暗号化を `JWT_SECRET` から分離し、認証トークンと独立してローテーション可能にする。
      local/test では専用鍵が無い場合のみ強い `JWT_SECRET` へフォールバックするが、本番では専用鍵必須。
- [x] `SETTINGS_ENCRYPTION_PREVIOUS_KEYS` は復号専用の旧鍵リスト（カンマ区切り）。
      設定時は各値32バイト以上を起動時検証し、暗号化は常に現行 `SETTINGS_ENCRYPTION_KEY` で行う。
      ローテーション期間中のみ設定し、保存済みAI APIキーを再設定/再暗号化した後は空に戻す。
      保存済みAI APIキーを再入力せず再暗号化する場合は:
      `cd backend && python -m app.tools.reencrypt_settings --dry-run` →
      `python -m app.tools.reencrypt_settings --apply --actor ops` →
      `SETTINGS_ENCRYPTION_PREVIOUS_KEYS` を空に戻してbackend再起動。
- [x] `ENABLE_AUTH=true`（false は起動失敗）
- [x] `ADMIN_PASSWORD` を設定（未設定は起動失敗。本番は初期管理者のみ作成。デモユーザーは local 限定）
- [x] `DATABASE_URL` を PostgreSQL に（SQLite は起動失敗。`alembic upgrade head` は起動時自動）
- [x] `/readyz` readiness（DB接続 / Alembic head一致 / 主要テーブル存在。NG時503・秘密値非表示）
- [x] 認証付きops診断 `/api/admin/ops/readiness-detail`（admin/tech_manager限定。
      公開`/readyz`では伏せるrevision等の詳細を確認）
- [x] 本番 app health monitor（`cwwd-app-health-check.timer`）。
      5分周期で local `/health`、local `/readyz`、frontend 200、frontend `/api/auth/me` proxy 401、
      public Cloudflare Access 302 を検査。
      public probe は Access token/cookie を使わず、edge protection の存在だけを確認する。
      失敗時は `cwwd-ops-failure@%n.service` で journald alert。
- [x] 本番 public edge access coverage monitor（`cwwd-public-edge-access-check.timer`）。
      15分周期で public `/`、`/api/sites`、`/health`、`/readyz`、`/docs`、`/openapi.json` が
      未認証で Cloudflare Access 302 + Access login Location を返すことを検査する。
      origin の 200/401/404 や Access 以外の 302 が直接見えたら alert。
- [x] 本番 Cloudflare Tunnel config monitor（`cwwd-cloudflared-config-check.timer`）。
      30分周期で `~/.cloudflared/config-cwwd.yml` の tunnel id、credential file 存在、backend `/api`/`/health`/`/readyz`、
      docs/OpenAPI edge 404、frontend fallback、catch-all 404 の構造と順序を検査する。
      credential JSON は読まず、設定構造だけを検査する。
- [x] 本番 security surface monitor（`cwwd-security-surface-check.timer`）。
      15分周期で loopback origin の `/health` security headers、未認証 `/api/sites` 401、
      production `/docs` / `/redoc` / `/openapi.json` 404、frontend `Content-Security-Policy-Report-Only`
      を検査する。public unauthenticated `/docs` は Cloudflare Access 302 になるため、docs無効化の判定には使わない。
      失敗時は `cwwd-ops-failure@%n.service` で journald alert。
- [x] 本番 network exposure monitor（`cwwd-network-exposure-check.timer`）。
      15分周期で `/proc/net/tcp` / `/proc/net/tcp6` を読み、backend 55019 / frontend 34979 の
      LISTEN が loopback address のみに限定されているかを検査する。`0.0.0.0` / `::` / LAN IP を検出したら
      `cwwd-ops-failure@%n.service` で journald alert。
- [x] 本番 systemd unit drift monitor（`cwwd-systemd-unit-drift-check.timer`）。
      30分周期で `deploy/systemd/cwwd-*.service|timer` と `/etc/systemd/system` の適用済み unit を sha256 比較する。
      unit 内容や env file はログに出さず、missing/changed/extra の unit 名だけを記録する。
- [x] 本番 systemd timer freshness monitor（`cwwd-systemd-timer-freshness-check.timer`）。
      30分周期で主要 timer の `ActiveState=active`、`UnitFileState=enabled`、次回予定、最終発火 age を検査する。
      短周期監視は 15〜60分、hourly監視は2時間、日次backup/export/restore drillは48時間の許容幅で発火遅延を検出する。
- [x] 本番 secret/config file permission monitor（`cwwd-secret-file-permission-check.timer`）。
      30分周期で `backend/.env`、DB backup env、backup export passphrase、初期admin password file、
      Cloudflare Tunnel config/credential、optional ops alert env の owner/group/mode を `stat` だけで検査する。
      内容は読まず、group/other権限やowner-execを検出する。
- [x] 本番 ops status snapshot（`cwwd-ops-status.timer`）。
      30分周期で backend/frontend/tunnel、Cloudflare config、public edge access、app health、security surface、network exposure、
      unit drift、timer freshness、secret file permissions、backup/export/restore drill timers、failed cwwd units を一覧化。
      secret/env file は読まず、failed unit が残る間は復旧または `systemctl reset-failed` まで
      `cwwd-ops-failure@%n.service` で journald alert。
      `deploy/scripts/ops-status.sh --json` は同じ whitelist 情報を JSON で出力し、dashboard/通知/Project更新に再利用できる。
- [x] 本番 ops status JSON snapshot export（`cwwd-ops-status-json-export.timer`）。
      30分周期で `ops-status.sh --json` を `/var/lib/cwwd/ops-status.json` へ atomic write する。
      snapshot は `kensan:kensan` / `0640`、JSON parse 成功後のみ置換し、異常 snapshot も保存して alert する。
- [x] 本番 ops status JSON snapshot check（`cwwd-ops-status-json-check.timer`）。
      30分周期で `/var/lib/cwwd/ops-status.json` の存在、mtime 60分以内、JSON parse、`status=ok`、
      `failed_units_count=0`、owner/group/mode を検査する。本文はログに出さない。
- [x] 認証付き ops status snapshot API（`GET /api/admin/ops/status-snapshot`）。
      admin/tech_manager 限定で `/var/lib/cwwd/ops-status.json` を返す。
      API側でも固定path・最大サイズ・mtime・JSON shapeを検査し、allowlist key のみ返す。
      missing/invalid/stale は 503、認証/認可境界は 401/403。
      frontend 管理メニューの「運用状態」画面が `opsStatusSnapshot()` から Bearer token 付きで取得し、
      service/timer/failed unit snapshot を表示する。E2E smoke は local 一時 snapshot で画面描画まで検証する。
- [x] failed unit 診断スナップショット（`ops-failed-units-report.sh`）。
      failed cwwd unit の限定 `systemctl show` プロパティと直近 journald を secret-redacted で出力する。
      `DATABASE_URL` / token / password / webhook URL らしき値は伏せ、診断後の復旧・`systemctl reset-failed` 判断に使う。
- [x] 本番 disk space monitor（`cwwd-disk-space-check.timer`）。
      1時間周期で `/`、`/var/backups/cwwd/postgres`、`/var/backups/cwwd/exports` の空き容量・inodeを検査。
      backup/export dirs は private 権限を要求し、閾値未満なら `cwwd-ops-failure@%n.service` で journald alert。
- [x] `CORS_ORIGINS` を本番フロントの HTTPS オリジンに限定（`*` / `http://` は起動失敗）
- [x] 基本HTTPセキュリティヘッダ（nosniff / frame deny / no-referrer / permissions policy / HSTS /
      COOP / CORP / legacy download hardening。
      backend API応答は `Cache-Control: no-store`）
- [x] frontend CSP は `Content-Security-Policy-Report-Only` で監視（runtime Babel / inline style 依存のため
      enforce は precompile 化後。開発時 `?api=` と production loopback proxy 検証のため loopback `connect-src` を許可）
- [x] backend API CSP は `default-src 'none'` ベース。本番では FastAPI `/docs` / `/redoc` /
      `/openapi.json` を無効化（local のみ有効）
- [x] HTTPS 終端（Cloudflare Tunnel + Access）
- [ ] `SLACK_WEBHOOK_URL` / `TEAMS_WEBHOOK_URL`（通知を外部送信する場合）
- [x] 通知の定期ディスパッチ（スケジューラから `notifications.dispatch`、DB配送台帳で重複抑止）
- [x] トークン失効台帳（`POST /api/auth/logout` でJWT `jti` を永続失効）
- [x] ログイン試行制限のDB永続化（`login_attempts`。CloudflareプロキシIPヘッダは信頼済みpeer経由のみ採用）
- [x] E2E smoke（Playwright Firefox。local SQLite + Open-Meteo stub、Secrets/Cloudflare非依存）
- [x] JS/CSS/font CDN assets self-host（React/ReactDOM/Babel/Leaflet を `frontend/design/vendor/` に固定。
      `vendor-assets-policy.cjs` で参照先・Leaflet画像・SRI・ライセンス同梱をCI検査）
- [x] 本番外部タイル通信の抑止（アプリ既定は no-tile、`cwwd-frontend.service` は `CW_TILE_URL=none`。
      `cdn-policy.cjs` で公開OSMタイルURLの再混入も検出。内部タイルサービス作成後に差し替え）
- [x] バックアップ/リストア手順（DBダンプ, §17）: [backup-restore.md](./backup-restore.md)
      `db-backup.sh` は basename checksum manifest を生成し、`db-restore.sh` は `.dump.sha256` 検証を既定必須化。
      restore は `--single-transaction --exit-on-error` で実行し、env-file 指定時は inherited DB URL を採用しない。
- [x] 日次DB論理バックアップ timer（`cwwd-db-backup.timer`）。
      毎日 02:10 + jitter で `/var/backups/cwwd/postgres` へ作成し、`--retention-days 14` で local dump を pruning。
      service は DB-only env file `/home/kensan/.config/cwwd/db-backup.env` を読み、systemd sandbox baseline +
      backup directory への `ReadWritePaths` のみ許可。失敗時は `cwwd-db-backup-failure@%n.service` を起動。
- [x] DBバックアップ freshness monitor（`cwwd-db-backup-check.timer`）。
      毎時17分 + jitter で最新 dump の age/checksum/orphan/tmp/zero-byte/権限を検査。
      warning 24h、critical 26h。失敗時は `cwwd-db-backup-failure@%n.service` で journald alert。
- [x] backup/check failure ops alert（`cwwd-db-backup-failure@.service` + `ops-alert.sh`）。
      journald alert を必ず残し、`/home/kensan/.config/cwwd/ops-alert.env` に webhook がある場合のみ Slack/Teams へ送信。
      backend / DB / UI 設定に依存しない。webhook URL は argv/log に出さない。
- [x] 論理ダンプの暗号化 export（`cwwd-db-backup-export.timer`）。
      最新 dump + `.sha256` を tar 化し、archive-only passphrase file で AES256 暗号化。
      `/var/backups/cwwd/exports` に `.dump.tar.gpg` + `.sha256` を作成し、30日保持。
- [x] 暗号化 export freshness monitor（`cwwd-db-backup-export-check.timer`）。
      毎時47分 + jitter で最新 encrypted export の age/checksum/orphan/tmp/zero-byte/権限/復号tar一覧を検査。
      warning 26h、critical 28h。失敗時は `cwwd-db-backup-failure@%n.service` で journald alert。
- [x] DBバックアップ restore drill monitor（`cwwd-db-backup-restore-drill.timer`）。
      毎日 04:20 + jitter で最新 dump の checksum と `pg_restore --list` parseability を DB 接続なしで検査。
      DB/libpq 環境変数は scrub し、失敗時は `cwwd-db-backup-failure@%n.service` で journald alert。
- [ ] 暗号化 export の off-host 転送、外部通知先の本番 webhook 設定
- [ ] Codex 対抗レビュー（認証/認可・DBスキーマ。CLAUDE.md 必須）

## 残課題（本番化の継続項目）

- Entra ID OIDC（簡易認証からの差し替え, §12）
- E2Eシナリオ拡充（権限別、設定画面、通知、CSV）
- 必要に応じた内部タイルサービス化（アプリ既定は no-tile。`CW_TILE_URL` / `window.__CW_TILE_URL__`、
  帰属表示用の `CW_TILE_ATTRIBUTION` / `window.__CW_TILE_ATTRIBUTION__` で差し替え口は実装済み）
- Docker Compose は local bind + `.env` のDB資格情報必須に変更済み。共有検証環境へ流用する場合は
  Compose ではなく本番と同じ Cloudflare Access / Secret 管理 / PostgreSQL 管理DBを使う
