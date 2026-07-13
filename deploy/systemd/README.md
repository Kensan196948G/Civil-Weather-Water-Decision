# 🔧 systemd 常駐化（適用済みユニットの正本）

📌 このディレクトリの `*.service` は **2026-07-12 に実機 `/etc/systemd/system/` へ適用済み**の実体コピー（Issue #77）。
汎用テンプレートは `deploy/*.service.example` を参照。実機とこのディレクトリの内容がズレたら本ディレクトリを更新すること。

## 📋 構成

| ユニット | 役割 | ポート/接続先 |
|---|---|---|
| `cwwd-backend.service` | FastAPI/uvicorn（Neon PostgreSQL 接続） | `127.0.0.1:55019` |
| `cwwd-frontend.service` | ClaudeDesign 静的サーバ（`serve.py`、`PORT`/`HOST` で固定） | `127.0.0.1:34979` |
| `cwwd-tunnel.service` | cloudflared（`~/.cloudflared/config-cwwd.yml`） | `https://cwwd.mirai-dx-platform.com` |
| `cwwd-cloudflared-config-check.service` | Cloudflare Tunnel config monitor（ingress drift） | `config-cwwd.yml` |
| `cwwd-cloudflared-config-check.timer` | 30分周期 Cloudflare config check | `timers.target` |
| `cwwd-app-health-check.service` | app health monitor（backend/frontend/Cloudflare Access） | loopback + public URL |
| `cwwd-app-health-check.timer` | 5分周期 app health check | `timers.target` |
| `cwwd-public-edge-access-check.service` | public Cloudflare Access coverage monitor（302 + Access login Location） | public URL paths |
| `cwwd-public-edge-access-check.timer` | 15分周期 public edge access check | `timers.target` |
| `cwwd-security-surface-check.service` | security surface monitor（headers/docs/auth/CSP） | loopback origin |
| `cwwd-security-surface-check.timer` | 15分周期 security surface check | `timers.target` |
| `cwwd-network-exposure-check.service` | network exposure monitor（55019/34979 loopback-only） | `/proc/net/tcp*` |
| `cwwd-network-exposure-check.timer` | 15分周期 network exposure check | `timers.target` |
| `cwwd-systemd-unit-drift-check.service` | systemd unit drift monitor（repo正本 vs `/etc/systemd/system`） | unit files |
| `cwwd-systemd-unit-drift-check.timer` | 30分周期 unit drift check | `timers.target` |
| `cwwd-systemd-timer-freshness-check.service` | systemd timer freshness monitor（active/enabled/last trigger/next elapse） | systemd state |
| `cwwd-systemd-timer-freshness-check.timer` | 30分周期 timer freshness check | `timers.target` |
| `cwwd-secret-file-permission-check.service` | secret/config file permission monitor | secret paths metadata |
| `cwwd-secret-file-permission-check.timer` | 30分周期 secret permission check | `timers.target` |
| `cwwd-ops-status.service` | operations status snapshot（主要service/timer/failed units、JSON出力対応） | systemd state |
| `cwwd-ops-status.timer` | 30分周期 ops snapshot | `timers.target` |
| `cwwd-ops-status-json-export.service` | operations status JSON snapshot export | `/var/lib/cwwd/ops-status.json` |
| `cwwd-ops-status-json-export.timer` | 30分周期 JSON snapshot export | `timers.target` |
| `cwwd-ops-status-json-check.service` | operations status JSON snapshot freshness/integrity check | `/var/lib/cwwd/ops-status.json` |
| `cwwd-ops-status-json-check.timer` | 30分周期 JSON snapshot check | `timers.target` |
| `cwwd-disk-space-check.service` | disk space / inode monitor | `/`, backup/export dirs |
| `cwwd-disk-space-check.timer` | hourly disk space check | `timers.target` |
| `cwwd-db-backup.service` | Neon PostgreSQL 論理ダンプ（oneshot） | `/var/backups/cwwd/postgres` |
| `cwwd-db-backup.timer` | 日次バックアップ起動（02:10 + jitter） | `timers.target` |
| `cwwd-db-backup-export.service` | 暗号化 export（dump+sha256 を tar→gpg） | `/var/backups/cwwd/exports` |
| `cwwd-db-backup-export.timer` | 日次 export 起動（03:10 + jitter） | `timers.target` |
| `cwwd-db-backup-export-check.service` | encrypted export freshness / integrity monitor（oneshot） | `/var/backups/cwwd/exports` |
| `cwwd-db-backup-export-check.timer` | hourly export freshness check（毎時47分 + jitter） | `timers.target` |
| `cwwd-db-backup-check.service` | backup freshness / integrity monitor（oneshot） | `/var/backups/cwwd/postgres` |
| `cwwd-db-backup-check.timer` | hourly freshness check（毎時17分 + jitter） | `timers.target` |
| `cwwd-db-backup-restore-drill.service` | DB-free restore drill（`pg_restore --list`） | `/var/backups/cwwd/postgres` |
| `cwwd-db-backup-restore-drill.timer` | daily restore drill（04:20 + jitter） | `timers.target` |
| `cwwd-db-backup-failure@.service` | backup/check failure alert（journald + optional Slack/Teams） | `ops-alert.env` |
| `cwwd-ops-failure@.service` | generic ops failure alert（journald + optional Slack/Teams） | `ops-alert.env` |

- 依存順序: `cwwd-tunnel` は `cwwd-backend` / `cwwd-frontend` に `After=` / `Wants=`（OS 起動時に3点セットで自動起動）
- 本番は backend/frontend を loopback に限定し、Cloudflare Tunnel + Access を唯一の公開入口にする。
- アプリ既定はタイルなし。本番frontendは `CW_TILE_URL=none` で外部タイル通信を明示抑止する。内部タイルサービスを用意したら
  `CW_TILE_URL=https://.../{z}/{x}/{y}.png` へ差し替える。
  帰属表示が必要なタイルサービスでは `CW_TILE_ATTRIBUTION=...` も追加する。
  LAN 直アクセス検証が必要な場合のみ、開発環境で `HOST=0.0.0.0` を明示して起動する
- 3ユニット共通で systemd sandbox を適用する:
  `NoNewPrivileges`, `PrivateTmp`, `PrivateDevices`, `ProtectSystem=full`, `ProtectHome=read-only`,
  kernel/control-group/clock/hostname 保護, `RestrictAddressFamilies=AF_INET AF_INET6 AF_UNIX`,
  capability 空化, `UMask=0077`。
- `cwwd-app-health-check.service` は 5分周期で local `/health`、local `/readyz`、frontend 200、
  public URL の Cloudflare Access 302 を検査する。public probe は Access token/cookie を使わず、
  edge protection の存在だけを確認する。失敗時は `cwwd-ops-failure@%n.service`。
- `cwwd-public-edge-access-check.service` は 15分周期で public `/`、`/api/sites`、`/health`、`/readyz`、
  `/docs`、`/openapi.json` が未認証で Cloudflare Access 302 + Location を返すことを検査する。
  origin の 200/401/404 が直接見えたら `cwwd-ops-failure@%n.service`。
- `cwwd-cloudflared-config-check.service` は 30分周期で `~/.cloudflared/config-cwwd.yml` の
  tunnel id、credential file 存在、backend `/api`/`/health`/`/readyz`、docs/OpenAPI edge 404、
  frontend fallback、catch-all 404 の構造と順序を検査する。credential JSON は読まない。
- `cwwd-security-surface-check.service` は 15分周期で loopback origin の security headers、
  未認証 `/api/sites` 401、production `/docs` / `/redoc` / `/openapi.json` 404、
  frontend `Content-Security-Policy-Report-Only` を検査する。public unauthenticated `/docs` は
  Cloudflare Access 302 になるため、docs無効化の判定には使わない。失敗時は `cwwd-ops-failure@%n.service`。
- `cwwd-network-exposure-check.service` は 15分周期で `/proc/net/tcp` / `/proc/net/tcp6` を読み、
  backend 55019 / frontend 34979 が loopback address のみに LISTEN しているかを検査する。
  `ss` を使わないため netlink 不要で、`RestrictAddressFamilies=AF_UNIX` の読み取り専用 monitor として動く。
  `0.0.0.0` / `::` / LAN IP を検出したら `cwwd-ops-failure@%n.service`。
- `cwwd-systemd-unit-drift-check.service` は 30分周期で `deploy/systemd/cwwd-*.service|timer` と
  `/etc/systemd/system` の適用済み unit を sha256 比較する。unit 内容や env file はログに出さず、
  missing/changed/extra の unit 名だけを出す。ネットワーク不要のため `RestrictAddressFamilies=AF_UNIX`。
- `cwwd-secret-file-permission-check.service` は 30分周期で `backend/.env`、DB backup env、
  backup export passphrase、初期admin password file、Cloudflare Tunnel config/credential、optional ops alert env の
  owner/group/mode を `stat` だけで検査する。ファイル内容は読まず、group/other 権限や owner-exec を検出する。
- `cwwd-ops-status.service` は 30分周期で主要 service/timer と failed cwwd units を snapshot する。
  手動の `deploy/scripts/ops-status.sh --json` は同じ whitelist 情報を secret-free JSON で出力する。
  secret/env file は読まず、systemd state のみを参照する。異常時は `cwwd-ops-failure@%n.service`。
  ネットワーク不要のため `RestrictAddressFamilies=AF_UNIX` に絞る。failed cwwd unit が残っている間は
  復旧または `systemctl reset-failed` まで 30分ごとに alert する。
- `cwwd-ops-status-json-export.service` は 30分周期で `/var/lib/cwwd/ops-status.json` を atomic 更新する。
  `StateDirectory=cwwd` により `/var/lib/cwwd` を `kensan:kensan` / `0750` で管理し、snapshot は `0640`。
  failed 状態でも parse 可能な JSON を保存した上で service を失敗させ、既存 alert に乗せる。
- `cwwd-ops-status-json-check.service` は 30分周期で snapshot の freshness、JSON parse、`status=ok`、
  `failed_units_count=0`、owner/group/mode を検査する。JSON 本文はログに出さない。
- `deploy/scripts/ops-failed-units-report.sh` は failed cwwd unit の手動診断用。限定 `systemctl show`
  プロパティと直近 journald を secret-redacted で出力し、unit 内容や env file は読まない。
- `cwwd-disk-space-check.service` は 1時間周期で `/`、`/var/backups/cwwd/postgres`、
  `/var/backups/cwwd/exports` の free bytes/free percent/inode free percent を検査する。
  root は 4GiB、backup/export は 10GiB を最低床にし、最新 dump size の 2x/3x も下回らないよう見る。
  `cwwd-db-backup.service` と `cwwd-db-backup-export.service` は書き込み前 `ExecStartPre` でも同じ gate を通す。
- frontend / tunnel には `MemoryDenyWriteExecute=true` を適用。backend は `uvloop` / `httptools` /
  `psycopg2` / `bcrypt` / `cryptography` 等の native extension 互換性を canary 確認してから適用する。
- `SystemCallFilter` は Python native libraries / cloudflared 互換性確認が必要なため未適用。
- `cwwd-db-backup.service` も同じ sandbox baseline を適用する。backup directory だけ
  `ReadWritePaths=/var/backups/cwwd/postgres` で書き込み許可し、`--retention-days 14` で古い論理ダンプを pruning する。
  接続情報は app full env ではなく `/home/kensan/.config/cwwd/db-backup.env`（DB-only, `0600`）から読む。
- `cwwd-db-backup.service` / `cwwd-db-backup-check.service` は
  `OnFailure=cwwd-db-backup-failure@%n.service` で失敗元 unit 名を alert に渡す。
- `cwwd-db-backup-export-check.service` も同じ failure service を使い、読み取り専用 monitor として
  最新 encrypted export age（warning 26h / critical 28h）、checksum、orphan、stale tmp、zero-byte、権限不備、
  gpg 復号後 tar の2 entry一覧を検出する。
- `cwwd-db-backup-export.service` は backup directory を read-only 入力、`/var/backups/cwwd/exports` だけを
  `ReadWritePaths` にして暗号化済み搬送物を作る。passphrase は
  `/home/kensan/.config/cwwd/backup-export.passphrase`（archive-only, `0600`）からのみ読む。
- `cwwd-db-backup-check.service` は読み取り専用 monitor。最新 dump age（warning 24h / critical 26h）、
  checksum、orphan、stale tmp、zero-byte、権限不備を検出し、失敗時は同じ failure service を起動する。
- `cwwd-db-backup-restore-drill.service` は読み取り専用・DB 接続なしで最新 dump の checksum と
  `pg_restore --list` parseability を検査する。DB/libpq 環境変数は `UnsetEnvironment` と script 側の `unset` で scrub する。
  ネットワーク不要のため `RestrictAddressFamilies=AF_UNIX` に絞る。
- failure service は `deploy/scripts/ops-alert.sh` を呼ぶ。`/home/kensan/.config/cwwd/ops-alert.env` があれば
  Slack/Teams webhook へ送信し、未設定なら journald alert のみ。webhook URL は `ExecStart` / ログに出さない。

## 🚀 適用手順（更新時）

```bash
sudo cp deploy/systemd/cwwd-*.service /etc/systemd/system/
sudo cp deploy/systemd/cwwd-*.timer /etc/systemd/system/
sudo cp deploy/systemd/cwwd-db-backup-failure@.service /etc/systemd/system/
sudo install -d -m 700 -o kensan -g kensan /var/backups/cwwd/postgres
sudo install -d -m 700 -o kensan -g kensan /var/backups/cwwd/exports
sudo install -d -m 700 -o kensan -g kensan /home/kensan/.config/cwwd
# /home/kensan/.config/cwwd/db-backup.env は deploy/systemd/cwwd-db-backup.env.example を参考にDB-onlyで作成し、chmod 600
# /home/kensan/.config/cwwd/backup-export.passphrase は deploy/systemd/cwwd-backup-export.passphrase.example を参考に作成し、chmod 600
# /home/kensan/.config/cwwd/ops-alert.env は deploy/systemd/cwwd-ops-alert.env.example を参考にalert-onlyで作成し、chmod 600（任意）
sudo systemctl daemon-reload
sudo systemctl enable --now cwwd-backend cwwd-frontend cwwd-tunnel
sudo systemctl enable --now cwwd-cloudflared-config-check.timer
sudo systemctl enable --now cwwd-app-health-check.timer
sudo systemctl enable --now cwwd-public-edge-access-check.timer
sudo systemctl enable --now cwwd-security-surface-check.timer
sudo systemctl enable --now cwwd-network-exposure-check.timer
sudo systemctl enable --now cwwd-systemd-unit-drift-check.timer
sudo systemctl enable --now cwwd-systemd-timer-freshness-check.timer
sudo systemctl enable --now cwwd-secret-file-permission-check.timer
sudo systemctl enable --now cwwd-ops-status.timer
sudo systemctl enable --now cwwd-ops-status-json-export.timer
sudo systemctl enable --now cwwd-ops-status-json-check.timer
sudo systemctl enable --now cwwd-disk-space-check.timer
sudo systemctl enable --now cwwd-db-backup.timer
sudo systemctl enable --now cwwd-db-backup-export.timer
sudo systemctl enable --now cwwd-db-backup-export-check.timer
sudo systemctl enable --now cwwd-db-backup-check.timer
sudo systemctl enable --now cwwd-db-backup-restore-drill.timer
```

## ✅ 検証（2026-07-13 実測）

```bash
systemctl is-active cwwd-backend cwwd-frontend cwwd-tunnel      # → active ×3
systemctl is-enabled cwwd-cloudflared-config-check.timer         # → enabled
systemctl is-enabled cwwd-app-health-check.timer                # → enabled
systemctl is-enabled cwwd-public-edge-access-check.timer         # → enabled
systemctl is-enabled cwwd-security-surface-check.timer           # → enabled
systemctl is-enabled cwwd-network-exposure-check.timer           # → enabled
systemctl is-enabled cwwd-systemd-unit-drift-check.timer         # → enabled
systemctl is-enabled cwwd-systemd-timer-freshness-check.timer    # → enabled
systemctl is-enabled cwwd-secret-file-permission-check.timer      # → enabled
systemctl is-enabled cwwd-ops-status.timer                      # → enabled
systemctl is-enabled cwwd-ops-status-json-export.timer           # → enabled
systemctl is-enabled cwwd-ops-status-json-check.timer            # → enabled
systemctl is-enabled cwwd-disk-space-check.timer                # → enabled
systemctl is-enabled cwwd-db-backup.timer                       # → enabled
systemctl is-enabled cwwd-db-backup-export.timer                # → enabled
systemctl is-enabled cwwd-db-backup-export-check.timer          # → enabled
systemctl is-enabled cwwd-db-backup-check.timer                 # → enabled
systemctl is-enabled cwwd-db-backup-restore-drill.timer         # → enabled
systemctl list-timers cwwd-cloudflared-config-check.timer        # → next 30min Cloudflare config check scheduled
systemctl list-timers cwwd-app-health-check.timer               # → next 5min app health check scheduled
systemctl list-timers cwwd-public-edge-access-check.timer        # → next 15min public edge access check scheduled
systemctl list-timers cwwd-security-surface-check.timer          # → next 15min security surface check scheduled
systemctl list-timers cwwd-network-exposure-check.timer          # → next 15min network exposure check scheduled
systemctl list-timers cwwd-systemd-unit-drift-check.timer        # → next 30min unit drift check scheduled
systemctl list-timers cwwd-systemd-timer-freshness-check.timer   # → next 30min timer freshness check scheduled
systemctl list-timers cwwd-secret-file-permission-check.timer     # → next 30min secret permission check scheduled
systemctl list-timers cwwd-ops-status.timer                     # → next 30min ops snapshot scheduled
systemctl list-timers cwwd-ops-status-json-export.timer          # → next 30min JSON snapshot export scheduled
systemctl list-timers cwwd-ops-status-json-check.timer           # → next 30min JSON snapshot check scheduled
systemctl list-timers cwwd-disk-space-check.timer               # → next hourly disk check scheduled
systemctl list-timers cwwd-db-backup.timer                      # → next run scheduled
systemctl list-timers cwwd-db-backup-export.timer               # → next export scheduled
systemctl list-timers cwwd-db-backup-export-check.timer         # → next hourly export check scheduled
systemctl list-timers cwwd-db-backup-check.timer                # → next hourly check scheduled
systemctl list-timers cwwd-db-backup-restore-drill.timer        # → next daily restore drill scheduled
systemd-analyze verify deploy/systemd/cwwd-*.service             # → no output
systemd-analyze verify deploy/systemd/cwwd-*.timer               # → no output
sudo systemctl start cwwd-cloudflared-config-check.service        # → ingress_rules=9, status=ok
sudo systemctl start cwwd-app-health-check.service               # → backend_health=ok, backend_readyz=ok, frontend=ok, public_edge=ok
sudo systemctl start cwwd-public-edge-access-check.service        # → paths_checked=6, status=ok
sudo systemctl start cwwd-security-surface-check.service          # → backend_health_security=ok, backend_auth_guard=ok, backend_docs_disabled=ok, frontend_security=ok
sudo systemctl start cwwd-network-exposure-check.service          # → port_55019_exposure=ok, port_34979_exposure=ok
sudo systemctl start cwwd-systemd-unit-drift-check.service        # → units_checked=..., status=ok
sudo systemctl start cwwd-systemd-timer-freshness-check.service   # → timers_checked=..., status=ok
sudo systemctl start cwwd-secret-file-permission-check.service     # → files_checked=7, status=ok
sudo systemctl start cwwd-ops-status.service                     # → status=ok, failed_units=0
sudo systemctl start cwwd-ops-status-json-export.service          # → ops_status_json=/var/lib/cwwd/ops-status.json, status=ok
sudo systemctl start cwwd-ops-status-json-check.service           # → snapshot_status=ok, mode=640, status=ok
deploy/scripts/ops-failed-units-report.sh                        # → failed_units=0, status=ok
sudo systemctl start cwwd-disk-space-check.service               # → status=ok, free_bytes/free_percent/inode_free_percent
sudo systemctl start cwwd-db-backup.service                      # → one-shot backup succeeds
sudo systemctl start cwwd-db-backup-export.service               # → encrypted export succeeds
sudo systemctl start cwwd-db-backup-export-check.service         # → latest_export=..., checksum=ok, decrypt_list=ok
sudo systemctl start cwwd-db-backup-check.service                # → latest_dump=..., checksum=ok
sudo systemctl start cwwd-db-backup-restore-drill.service        # → latest_dump=..., pg_restore_list=ok
journalctl -t cwwd-ops-alert -n 20 --no-pager                    # → failure alert の確認
journalctl -u cwwd-db-backup.service -n 100 --no-pager           # → backup=..., sha256=..., retention_deleted=...
ss -ltn '( sport = :55019 or sport = :34979 )'                  # → 127.0.0.1 のみ
curl -s http://127.0.0.1:55019/health                           # → {"status":"ok","env":"production",...}
curl -s http://127.0.0.1:55019/readyz                            # → DB/Alembic/主要テーブル ok
curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:34979/   # → 200
curl -s -o /dev/null -w "%{http_code}" https://cwwd.mirai-dx-platform.com/  # → 302 (Cloudflare Access ログインへ)
```

⚠️ 公開 URL の 302 は Cloudflare Access（アプリ `cwwd` / ポリシー `CWWD`）による正常なエッジ防御。
許可メンバーのみログイン後にアプリへ到達できる。

## 🔁 運用メモ

- ログ確認: `journalctl -u cwwd-backend -f`（frontend / tunnel も同様）
- app health確認: `systemctl list-timers cwwd-app-health-check.timer` /
  `journalctl -u cwwd-app-health-check.service -n 100 --no-pager`
- Cloudflare config確認: `systemctl list-timers cwwd-cloudflared-config-check.timer` /
  `journalctl -u cwwd-cloudflared-config-check.service -n 100 --no-pager`
- public edge access確認: `systemctl list-timers cwwd-public-edge-access-check.timer` /
  `journalctl -u cwwd-public-edge-access-check.service -n 100 --no-pager`
- security surface確認: `systemctl list-timers cwwd-security-surface-check.timer` /
  `journalctl -u cwwd-security-surface-check.service -n 100 --no-pager`
- network exposure確認: `systemctl list-timers cwwd-network-exposure-check.timer` /
  `journalctl -u cwwd-network-exposure-check.service -n 100 --no-pager`
- unit drift確認: `systemctl list-timers cwwd-systemd-unit-drift-check.timer` /
  `journalctl -u cwwd-systemd-unit-drift-check.service -n 100 --no-pager`
- timer freshness確認: `systemctl list-timers cwwd-systemd-timer-freshness-check.timer` /
  `journalctl -u cwwd-systemd-timer-freshness-check.service -n 100 --no-pager`
- secret permission確認: `systemctl list-timers cwwd-secret-file-permission-check.timer` /
  `journalctl -u cwwd-secret-file-permission-check.service -n 100 --no-pager`
- ops snapshot確認: `systemctl list-timers cwwd-ops-status.timer` /
  `journalctl -u cwwd-ops-status.service -n 100 --no-pager`
  JSON確認: `deploy/scripts/ops-status.sh --json | python3 -m json.tool`
- ops JSON export確認: `systemctl list-timers cwwd-ops-status-json-export.timer` /
  `journalctl -u cwwd-ops-status-json-export.service -n 100 --no-pager` /
  `python3 -m json.tool /var/lib/cwwd/ops-status.json`
- ops JSON check確認: `systemctl list-timers cwwd-ops-status-json-check.timer` /
  `journalctl -u cwwd-ops-status-json-check.service -n 100 --no-pager`
- failed unit診断: `deploy/scripts/ops-failed-units-report.sh --allow-failed-units --lines 30`
- disk space確認: `systemctl list-timers cwwd-disk-space-check.timer` /
  `journalctl -u cwwd-disk-space-check.service -n 100 --no-pager`
- backup timer確認: `systemctl list-timers cwwd-db-backup.timer` /
  `journalctl -u cwwd-db-backup.service -n 100 --no-pager`
- encrypted export確認: `systemctl list-timers cwwd-db-backup-export.timer` /
  `journalctl -u cwwd-db-backup-export.service -n 100 --no-pager`
- encrypted export freshness確認: `systemctl list-timers cwwd-db-backup-export-check.timer` /
  `journalctl -u cwwd-db-backup-export-check.service -n 100 --no-pager`
- backup freshness確認: `systemctl list-timers cwwd-db-backup-check.timer` /
  `journalctl -u cwwd-db-backup-check.service -n 100 --no-pager`
- restore drill確認: `systemctl list-timers cwwd-db-backup-restore-drill.timer` /
  `journalctl -u cwwd-db-backup-restore-drill.service -n 100 --no-pager`
- ops alert確認: `deploy/scripts/ops-alert.sh --env-file /home/kensan/.config/cwwd/ops-alert.env --title "test" --message "dry-run" --dry-run`
- ポート変更時: unit と `~/.cloudflared/config-cwwd.yml` の **両方** を更新（`--config` 明示必須の罠は `deploy/cloudflared-setup-steps.md` 参照）
- backend は `backend/.env` を `EnvironmentFile` として読む（`DATABASE_URL`=Neon。本番ハードニングは `docs/deploy.md` チェックリスト参照）
