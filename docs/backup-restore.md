# バックアップ・リストア手順 / Backup & Restore

設計 §17 / T3-09 に基づく PostgreSQL（Neon DB）向けの運用手順です。

## 対象

| 対象 | 方法 | 頻度 | 保管 |
|---|---|---|---|
| 🗄️ Neon PostgreSQL | Neon の PITR / マネージドバックアップ | 継続 | Neon 管理面 |
| 📦 論理ダンプ | `deploy/scripts/db-backup.sh` (`pg_dump --format=custom`) | 日次・リリース前 | local `/var/backups/cwwd/postgres` |
| 🔐 暗号化export | `deploy/scripts/db-backup-export.sh` (`tar` → `gpg AES256`) | 日次・off-host転送前 | local `/var/backups/cwwd/exports` + off-host storage |
| ⚙️ 設定ファイル | `backend/.env` / systemd unit / cloudflared config | 変更時 | 秘密管理領域 |

`backend/.env` とダンプファイルは Git 管理禁止です。`.gitignore` で `.env*` と `backups/` を除外しています。

## 事前条件

```bash
pg_dump --version
pg_restore --version
```

`DATABASE_URL_DIRECT` または `DATABASE_URL` は PostgreSQL 接続文字列を指定します。
`DATABASE_URL_DIRECT` が設定されている場合は、スクリプトがそちらを優先します。
`--env-file` を指定した場合、スクリプトは inherited な `DATABASE_URL` / `DATABASE_URL_DIRECT` を一度破棄し、
env file 内の値だけを採用します。古い shell 環境変数で別DBへ接続する事故を防ぐためです。
backup timer は app の full `backend/.env` を読みません。`deploy/systemd/cwwd-db-backup.env.example` を参考に、
`/home/kensan/.config/cwwd/db-backup.env` へ DB-only の `DATABASE_URL_DIRECT` だけを配置します。
コマンド出力・チケット・チャットに接続文字列を貼らないでください。
Neon では `pg_dump` / `pg_restore` に pooled connection string を使わず、Connect 画面で Connection pooling を外した
direct/unpooled connection string を使います（Neon Docs: https://neon.com/docs/manage/backup-pg-dump）。

`pg_dump` / `pg_restore` はサーバーの PostgreSQL major version 以上を使います。別バージョンを明示する場合:

```bash
PG_DUMP_BIN=/usr/lib/postgresql/17/bin/pg_dump \
deploy/scripts/db-backup.sh --env-file backend/.env

PG_RESTORE_BIN=/usr/lib/postgresql/17/bin/pg_restore \
deploy/scripts/db-restore.sh --env-file backend/.env --dump backups/postgres/<file>.dump --dry-run
```

## 目標

| 指標 | 初期目標 |
|---|---|
| RPO | 24時間以内 |
| RTO | 4〜8時間 |
| 復元先 | まず Neon branch / 一時DB。production 直復元は禁止 |

## バックアップ

```bash
deploy/scripts/db-backup.sh \
  --env-file backend/.env \
  --output-dir backups/postgres \
  --retention-days 14
```

出力:

```text
backup=backups/postgres/cwwd-YYYYMMDDTHHMMSSZ.dump
sha256=backups/postgres/cwwd-YYYYMMDDTHHMMSSZ.dump.sha256
```

運用:

| チェック | コマンド |
|---|---|
| ハッシュ確認 | `sha256sum -c backups/postgres/<file>.dump.sha256` |
| 中身一覧 | `pg_restore --list backups/postgres/<file>.dump \| head` |
| 権限 | `ls -l backups/postgres` が `600` 相当 |

### 日次自動バックアップ

実機では `cwwd-db-backup.timer` が日次で論理ダンプを作成します。

| 項目 | 値 |
|---|---|
| unit | `deploy/systemd/cwwd-db-backup.service` |
| timer | `deploy/systemd/cwwd-db-backup.timer` |
| 実行時刻 | 毎日 02:10 + `RandomizedDelaySec=30min` |
| env file | `/home/kensan/.config/cwwd/db-backup.env`（DB-only, `0600`） |
| 出力先 | `/var/backups/cwwd/postgres` |
| 保持期間 | 14日（`--retention-days 14`） |
| 権限 | directory `700`, dump/manifest `600` |
| failure | `OnFailure=cwwd-db-backup-failure@%n.service` で journald alert priority に記録。任意でSlack/Teamsへ送信 |

適用・確認:

```bash
sudo cp deploy/systemd/cwwd-db-backup.service deploy/systemd/cwwd-db-backup.timer /etc/systemd/system/
sudo cp deploy/systemd/cwwd-db-backup-export.service deploy/systemd/cwwd-db-backup-export.timer /etc/systemd/system/
sudo cp deploy/systemd/cwwd-db-backup-export-check.service deploy/systemd/cwwd-db-backup-export-check.timer /etc/systemd/system/
sudo cp deploy/systemd/cwwd-db-backup-check.service deploy/systemd/cwwd-db-backup-check.timer /etc/systemd/system/
sudo cp deploy/systemd/cwwd-db-backup-restore-drill.service deploy/systemd/cwwd-db-backup-restore-drill.timer /etc/systemd/system/
sudo cp deploy/systemd/cwwd-disk-space-check.service deploy/systemd/cwwd-disk-space-check.timer /etc/systemd/system/
sudo cp deploy/systemd/cwwd-db-backup-failure@.service /etc/systemd/system/
sudo install -d -m 700 -o kensan -g kensan /var/backups/cwwd/postgres
sudo install -d -m 700 -o kensan -g kensan /var/backups/cwwd/exports
sudo install -d -m 700 -o kensan -g kensan /home/kensan/.config/cwwd
# /home/kensan/.config/cwwd/db-backup.env を DB-only で作成し、chmod 600
# /home/kensan/.config/cwwd/backup-export.passphrase を高entropy値で作成し、chmod 600
# Slack/Teams ops alert を使う場合は /home/kensan/.config/cwwd/ops-alert.env を alert-only で作成し、chmod 600
sudo systemctl daemon-reload
sudo systemctl enable --now cwwd-db-backup.timer
sudo systemctl enable --now cwwd-db-backup-export.timer
sudo systemctl enable --now cwwd-db-backup-export-check.timer
sudo systemctl enable --now cwwd-db-backup-check.timer
sudo systemctl enable --now cwwd-db-backup-restore-drill.timer
sudo systemctl enable --now cwwd-disk-space-check.timer
systemctl list-timers cwwd-db-backup.timer
systemctl list-timers cwwd-db-backup-export.timer
systemctl list-timers cwwd-db-backup-export-check.timer
systemctl list-timers cwwd-db-backup-check.timer
systemctl list-timers cwwd-db-backup-restore-drill.timer
systemctl list-timers cwwd-disk-space-check.timer
journalctl -u cwwd-db-backup.service -n 100 --no-pager
```

即時実行する場合:

```bash
sudo systemctl start cwwd-db-backup.service
```

#### 障害例: `password authentication failed` で日次ダンプが失敗し続ける

`db-backup.env` の `DATABASE_URL_DIRECT` に古いパスワードが残っていると、バックエンド（`backend/.env`）は
正常でもバックアップだけ失敗し続けます（2026-07-23〜08-01 に実機で発生。7/13 以降 19 日間ダンプなし）。

対処（秘密値は画面やログへ出力しないこと）:

```bash
# 1) backend/.env の現行 DATABASE_URL_DIRECT を同期（同一ホスト・ユーザー・DB であることを確認してから）
#    python3 等でパスワード文字列を表示せずに db-backup.env へ書き換える
chmod 600 /home/kensan/.config/cwwd/db-backup.env
# 2) 即時実行して確認
sudo systemctl start cwwd-db-backup.service
sudo systemctl start cwwd-db-backup-export.service
sudo systemctl start cwwd-db-backup-check.service
sudo systemctl start cwwd-db-backup-restore-drill.service
systemctl show cwwd-db-backup.service -p Result
```

`Result=success` になり、`/var/backups/cwwd/postgres/cwwd-*.dump` のタイムスタンプが更新されれば復旧完了です。

### 暗号化 Export

`cwwd-db-backup-export.timer` が日次 backup window 後に最新 valid dump と `.sha256` を 1つの tar にまとめ、
`gpg --symmetric --cipher-algo AES256` で暗号化します。これは off-host storage へ転送する前の搬送物です。

| 項目 | 値 |
|---|---|
| script | `deploy/scripts/db-backup-export.sh` |
| unit | `deploy/systemd/cwwd-db-backup-export.service` |
| timer | `deploy/systemd/cwwd-db-backup-export.timer` |
| 実行時刻 | 毎日 03:10 + `RandomizedDelaySec=30min` |
| source | `/var/backups/cwwd/postgres/cwwd-YYYYMMDDTHHMMSSZ.dump` + `.sha256` |
| output | `/var/backups/cwwd/exports/cwwd-YYYYMMDDTHHMMSSZ.dump.tar.gpg` + `.sha256` |
| passphrase | `/home/kensan/.config/cwwd/backup-export.passphrase`（archive-only, `0600`） |
| 保持期間 | 30日（`--retention-days 30`） |
| secret policy | passphrase は file からのみ読み、argv/stdout/stderr へ出さない |

passphrase 作成:

```bash
openssl rand -base64 48 | sudo install -m 600 -o kensan -g kensan /dev/stdin /home/kensan/.config/cwwd/backup-export.passphrase
```

手動 export:

```bash
deploy/scripts/db-backup-export.sh \
  --backup-dir /var/backups/cwwd/postgres \
  --output-dir /var/backups/cwwd/exports \
  --passphrase-file /home/kensan/.config/cwwd/backup-export.passphrase \
  --retention-days 30
```

復号して中身だけ確認する場合:

```bash
tmp_tar="$(mktemp)"
gpg --batch --yes --pinentry-mode loopback \
  --passphrase-file /home/kensan/.config/cwwd/backup-export.passphrase \
  --output "$tmp_tar" \
  --decrypt /var/backups/cwwd/exports/cwwd-YYYYMMDDTHHMMSSZ.dump.tar.gpg
tar -tf "$tmp_tar"
rm -f "$tmp_tar"
```

### 暗号化 Export Monitor

`cwwd-db-backup-export-check.timer` が hourly で暗号化 export を検査します。
検査は archive-only passphrase file を `--passphrase-file` で gpg に渡し、復号した tar の一覧だけを確認します。
passphrase の値と平文 dump は stdout/stderr/argv に出しません。

| 項目 | 値 |
|---|---|
| script | `deploy/scripts/db-backup-export-check.sh` |
| unit | `deploy/systemd/cwwd-db-backup-export-check.service` |
| timer | `deploy/systemd/cwwd-db-backup-export-check.timer` |
| 実行時刻 | 毎時47分 + `RandomizedDelaySec=10min` |
| warning | 最新 valid export が 26h 超過 |
| critical | 最新 valid export が 28h 超過、checksum不一致、復号/tar一覧不正、orphan、権限不備 |
| 対象名 | `cwwd-YYYYMMDDTHHMMSSZ.dump.tar.gpg` のみ |
| failure | `OnFailure=cwwd-db-backup-failure@%n.service` |

検査内容:

| チェック | 条件 |
|---|---|
| 最新 export | strict timestamp 名の最新 `.dump.tar.gpg` が存在 |
| checksum | `<archive>.sha256` が存在し、export directory 起点で `sha256sum -c` 成功 |
| decrypt list | gpg 復号後の tar が同一 timestamp の `.dump` と `.dump.sha256` の2 entry だけを含む |
| orphan | archive without manifest / manifest without archive / manifest target mismatch を拒否 |
| tmp | 60分超の `cwwd-*.dump.tar.gpg.tmp` を拒否 |
| サイズ | zero-byte archive を拒否 |
| 権限 | export dir は group/other 権限なし、archive/manifest/passphrase は `0600` 相当 |

手動検証:

```bash
deploy/scripts/db-backup-export-check.sh \
  --export-dir /var/backups/cwwd/exports \
  --warn-age-hours 26 \
  --max-age-hours 28 \
  --passphrase-file /home/kensan/.config/cwwd/backup-export.passphrase

sudo systemctl start cwwd-db-backup-export-check.service
journalctl -u cwwd-db-backup-export-check.service -n 100 --no-pager
```

### Freshness Monitor

`cwwd-db-backup-check.timer` が hourly で最新バックアップを検査します。
検査は DB や secret を読み込まず、backup directory と DB-only env file の権限だけを確認します。

| 項目 | 値 |
|---|---|
| script | `deploy/scripts/db-backup-check.sh` |
| unit | `deploy/systemd/cwwd-db-backup-check.service` |
| timer | `deploy/systemd/cwwd-db-backup-check.timer` |
| 実行時刻 | 毎時17分 + `RandomizedDelaySec=10min` |
| warning | 最新 valid dump が 24h 超過 |
| critical | 最新 valid dump が 26h 超過、checksum不一致、orphan、権限不備 |
| 対象名 | `cwwd-YYYYMMDDTHHMMSSZ.dump` のみ |
| failure | `OnFailure=cwwd-db-backup-failure@%n.service` |

検査内容:

| チェック | 条件 |
|---|---|
| 最新 dump | strict timestamp 名の最新 `cwwd-*.dump` が存在 |
| checksum | `<dump>.sha256` が存在し、backup directory 起点で `sha256sum -c` 成功 |
| orphan | dump without manifest / manifest without dump / manifest target mismatch を拒否 |
| tmp | 60分超の `cwwd-*.dump.tmp` を拒否 |
| サイズ | zero-byte dump を拒否 |
| 権限 | backup dir は group/other 権限なし、dump/manifest/env は `0600` 相当 |

手動検証:

```bash
deploy/scripts/db-backup-check.sh \
  --backup-dir /var/backups/cwwd/postgres \
  --warn-age-hours 24 \
  --max-age-hours 26 \
  --env-file /home/kensan/.config/cwwd/db-backup.env

sudo systemctl start cwwd-db-backup-check.service
journalctl -u cwwd-db-backup-check.service -n 100 --no-pager
```

### Restore Drill Monitor

`cwwd-db-backup-restore-drill.timer` が daily で最新 dump の restore drill を行います。
これは破壊的 restore ではなく、checksum 検証後に `pg_restore --list` で custom-format archive が読めることだけを確認します。
DB へ接続せず、`DATABASE_URL` / `PGPASSWORD` / `PGHOST` などの DB/libpq 環境変数は scrub します。

| 項目 | 値 |
|---|---|
| script | `deploy/scripts/db-backup-restore-drill.sh` |
| unit | `deploy/systemd/cwwd-db-backup-restore-drill.service` |
| timer | `deploy/systemd/cwwd-db-backup-restore-drill.timer` |
| 実行時刻 | 毎日 04:20 + `RandomizedDelaySec=30min` |
| warning | 最新 valid dump が 26h 超過 |
| critical | 最新 valid dump が 30h 超過、checksum不一致、`pg_restore --list` 失敗、orphan、権限不備 |
| DB接続 | なし。`pg_restore --list` のみ |
| failure | `OnFailure=cwwd-db-backup-failure@%n.service` |

手動検証:

```bash
deploy/scripts/db-backup-restore-drill.sh \
  --backup-dir /var/backups/cwwd/postgres \
  --warn-age-hours 26 \
  --max-age-hours 30

sudo systemctl start cwwd-db-backup-restore-drill.service
journalctl -u cwwd-db-backup-restore-drill.service -n 100 --no-pager
```

### Disk Space Monitor

`cwwd-disk-space-check.timer` が hourly で backup/export に必要なディスク余力を確認します。
`cwwd-db-backup.service` と `cwwd-db-backup-export.service` は、実際に書き込む直前の `ExecStartPre` でも同じ gate を通します。

| 項目 | 値 |
|---|---|
| script | `deploy/scripts/disk-space-check.sh` |
| unit | `deploy/systemd/cwwd-disk-space-check.service` |
| timer | `deploy/systemd/cwwd-disk-space-check.timer` |
| 実行時刻 | 起動3分後、その後1時間ごと + `RandomizedDelaySec=5min` |
| 対象 | `/`, `/var/backups/cwwd/postgres`, `/var/backups/cwwd/exports` |
| bytes floor | root 4GiB、backup/export 10GiB、かつ最新 dump size の2x/3x |
| percent floor | free 15%、inode free 10% |
| failure | `OnFailure=cwwd-ops-failure@%n.service` |

手動検証:

```bash
deploy/scripts/disk-space-check.sh \
  --path / \
  --path /var/backups/cwwd/postgres \
  --path /var/backups/cwwd/exports \
  --root-min-free-mib 4096 \
  --data-min-free-mib 10240 \
  --min-free-percent 15 \
  --min-inode-free-percent 10 \
  --dump-size-dir /var/backups/cwwd/postgres

sudo systemctl start cwwd-disk-space-check.service
journalctl -u cwwd-disk-space-check.service -n 100 --no-pager
```

### Ops Alert

`deploy/scripts/ops-alert.sh` は backup job / freshness monitor の失敗を通知します。
既定では journald へ `alert` priority で記録し、`/home/kensan/.config/cwwd/ops-alert.env` に webhook がある場合だけ
Slack / Teams へも送信します。この経路は backend / DB / UI 設定に依存しないため、アプリ障害時にも動作します。

| 項目 | 値 |
|---|---|
| script | `deploy/scripts/ops-alert.sh` |
| unit | `deploy/systemd/cwwd-db-backup-failure@.service` |
| env file | `/home/kensan/.config/cwwd/ops-alert.env`（alert-only, `0600`, optional） |
| secret policy | webhook URL を `ExecStart` / ログ / dry-run 出力へ表示しない |
| payload | 固定メッセージ + 失敗 unit 名。raw journal tail は送らない |
| safety | URL / `DATABASE_URL` / `PASSWORD` / `TOKEN` / `SECRET` / `WEBHOOK` 形式を redaction、payload length cap |

env file 例:

```bash
sudo install -m 600 -o kensan -g kensan deploy/systemd/cwwd-ops-alert.env.example /home/kensan/.config/cwwd/ops-alert.env
sudoedit /home/kensan/.config/cwwd/ops-alert.env
```

dry-run:

```bash
deploy/scripts/ops-alert.sh \
  --env-file /home/kensan/.config/cwwd/ops-alert.env \
  --title "CWWD backup alert test" \
  --message "dry-run only" \
  --severity alert \
  --dry-run
```

`backups/` は Git 管理外です。local dump は短期復旧用であり、長期保管・災害対策は
暗号化された off-host storage へ転送する運用を追加してください。
`pg_dump` / `pg_restore` にはパスワード付き URL を argv で渡さず、script 内で一時 `PGPASSFILE` を作成して libpq 環境変数で接続します。

## アーカイブ検証

破壊的な復元前に必ず archive validation を実行します。`--dry-run` は DB へ書き込まず、
checksum 検証と `pg_restore --list` による dump 読み取り確認だけを行います。
`db-restore.sh` は既定で `<dump>.sha256` の存在と `sha256sum -c` の成功を必須にします。
checksum manifest は dump basename で作成するため、dump と `.sha256` を同じディレクトリに保ったままなら
保管先や検証先へ移動しても検証できます。

```bash
deploy/scripts/db-restore.sh \
  --env-file backend/.env \
  --dump backups/postgres/cwwd-YYYYMMDDTHHMMSSZ.dump \
  --dry-run
```

## リストア

production への直接リストアは CTO / 現場責任者 / DB管理者の明示承認がある場合のみ実施します。
通常は Neon branch または一時DBへ復元し、検証後に切替判断します。

本番DBへ直接戻す前に、Neon のブランチまたは一時DBへ復元し、`/health`、ログイン、代表APIを確認します。
`<dump>.sha256` が無いダンプは既定で復元できません。緊急時に CTO / DB 管理者が明示承認した場合のみ
`--allow-missing-checksum` を付け、承認理由と代替検証を運用記録へ残します。

> **復元先は PostgreSQL 17 以上が必要**（2026-08-01 実機検証で確認）。本番 dump には
> `SET transaction_timeout = 0`（PG17 で追加）が含まれるため、PostgreSQL 16 以前の一時DBへは
> 復元できません。復元ドリルは Neon branch または PG17+ の一時インスタンスで行ってください。
> 検証実績: PG18 一時クラスタへ `db-restore.sh` で復元し、全18テーブル・行数・
> `alembic_version` が本番と一致することを確認済み（2026-08-01）。

実行時の `pg_restore` は `--single-transaction --exit-on-error --clean --if-exists` を使い、
復元エラー時に部分的な drop/restore が残るリスクを抑えます。

```bash
CWWD_RESTORE_CONFIRM=RESTORE \
deploy/scripts/db-restore.sh \
  --env-file backend/.env \
  --dump backups/postgres/cwwd-YYYYMMDDTHHMMSSZ.dump
```

復元後:

```bash
python -m alembic current
curl -sS http://127.0.0.1:55019/health
curl -sS http://127.0.0.1:55019/readyz
```

## 復旧判定

| 項目 | 合格条件 |
|---|---|
| スキーマ | `alembic current` が head |
| 管理者 | production admin がログイン可能 |
| デモユーザー | production では demo user が無効 |
| 暗号化設定 | DB dump と対応する `SETTINGS_ENCRYPTION_KEY` / 必要な旧鍵が揃い、AI/API設定が復号可能 |
| API | `/health`（プロセス）・`/readyz`（DB/Alembic/主要テーブル）・代表APIが応答 |
| 監査 | 復旧作業日時・担当・ダンプハッシュを GitHub Project / 運用記録へ残す |

## 注意

- `db-restore.sh` は `CWWD_RESTORE_CONFIRM=RESTORE` が無い限り破壊的復元を拒否します。
- `db-restore.sh` は既定で `.dump.sha256` の欠落・不一致を拒否します。
  `--allow-missing-checksum` は明示承認済みの緊急復旧だけで使います。
- DB dump 内の AI/API 設定値はアプリ側の鍵で暗号化されています。DB dump、`.sha256`、
  `SETTINGS_ENCRYPTION_KEY`、ローテーション中の `SETTINGS_ENCRYPTION_PREVIOUS_KEYS` は
  ひとまとまりの復旧セットとして扱います。
- ダンプは個人情報・現場情報・判断履歴を含む可能性があります。暗号化保管し、共有リンクに置かないでください。
- Neon の PITR と論理ダンプは役割が異なります。誤削除直後は PITR、移行・監査・長期保管は論理ダンプを使います。

## 実機メモ（2026-07-13）

| 項目 | 状態 |
|---|---|
| `DATABASE_URL_DIRECT` | `backend/.env` に設定済み（pooled URL から direct host を導出、値は非表示管理） |
| backup env | `/home/kensan/.config/cwwd/db-backup.env` 作成済み（DB-only, `0600`, 値は非表示管理） |
| local client | PostgreSQL 17.10 client 導入済み（`/usr/lib/postgresql/17/bin`）。スクリプトは最大 version を自動選択 |
| Neon server | PostgreSQL 17.10 |
| backup timer | `cwwd-db-backup.timer` enabled/active。次回 `2026-07-14 02:21:37 JST` 実行予定 |
| encrypted export | `cwwd-db-backup-export.timer` enabled/active。次回 `2026-07-14 03:12:21 JST` 実行予定。最新dumpから `cwwd-20260713T041247Z.dump.tar.gpg` 作成・sha256・復号tar一覧検証済み |
| export freshness monitor | `cwwd-db-backup-export-check.timer` enabled/active。最新暗号化exportのage/checksum/orphan/権限/復号tar一覧検査をhourly実行 |
| freshness monitor | `cwwd-db-backup-check.timer` enabled/active。最新dumpのage/checksum/orphan/権限検査をhourly実行 |
| restore drill monitor | `cwwd-db-backup-restore-drill.timer` enabled/active。最新dumpのchecksumと`pg_restore --list` parseabilityをdaily実行 |
| disk space monitor | `cwwd-disk-space-check.timer` enabled/active。`/`・backup・export filesystem のfree bytes/free%/inode%をhourly検査し、backup/export直前にもpreflight gate実行 |
| ops alert | `cwwd-db-backup-failure@.service` 適用済み。journald fallback + optional Slack/Teams webhook env 対応 |
| live dump | `/var/backups/cwwd/postgres/cwwd-20260713T041247Z.dump` 作成済み。sha256 検証・restore dry-run 済み |
