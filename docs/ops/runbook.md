# インシデント対応・復旧 Runbook

## 1. 連絡先（プレースホルダ）

| レベル | 役割 | 担当者 | 連絡手段 |
| --- | --- | --- | --- |
| 第1次 | IT・DX運用担当 | （要設定） | 電話/チャット |
| 第2次 | 開発担当 | （要設定） | チャット/GitHub |
| 第3次 | 現場システム管理者 | （要設定） | 電話 |

## 2. 障害分類と優先度

| 優先度 | 定義 | 例 |
| --- | --- | --- |
| P1 | 主要機能停止・認証不能・データ損失リスク | ログイン不可、API 5xx連続、DB接続不能 |
| P2 | 一部機能停止・性能劣化 | 河川判定データ欠測、応答遅延 |
| P3 | 軽微・監視上のみ | 単発エラー、バックアップ遅延（警告） |

## 3. 切り分け手順（P1/P2）

```bash
# 1) エッジ到達性
curl -sS -o /dev/null -w '%{http_code}\n' --max-time 15 https://cwwd.mirai-dx-platform.com/
#   302 = Access正常 / その他 = Access・Tunnel・DNS の疑い

# 2) サービス状態
systemctl status cwwd-backend cwwd-frontend cwwd-tunnel

# 3) バックエンドログ
journalctl -u cwwd-backend -n 200 --no-pager

# 4) 死活・準備状態
curl -s http://127.0.0.1:55019/health
curl -s http://127.0.0.1:55019/readyz

# 5) データソース状態（adminログイン後に /api/dashboard/data-sources を確認）
# 6) DB接続確認（接続文字列は画面・ログへ出さない）
```

## 4. 復旧手順（代表例）

### バックエンド再起動

```bash
sudo systemctl restart cwwd-backend
systemctl is-active cwwd-backend
curl -s http://127.0.0.1:55019/readyz
```

### Tunnel 障害

```bash
sudo systemctl restart cwwd-tunnel
systemctl is-active cwwd-tunnel
# 設定差分がある場合は deploy/cloudflared-config.yml.example と実configを比較
```

### Cloudflare Access が302を返さない

- Accessダッシュボードでアプリ `cwwd` / ポリシー `CWWD` を確認
- 誤って全拒否・無効化していないか確認（変更はApproval対象）

### コード起因の障害（rollback）

1. 直前の正常commitを特定（`git log --oneline -10`）
2. `git revert <不良merge commit>` → 作業ブランチ → PR → CI → マージ
3. 本番反映: `git pull` + `sudo systemctl restart cwwd-backend cwwd-frontend`
4. スモークテスト（§5）

### マイグレーション失敗

- 追加のみのmigration（本プロジェクト方針）は再実行（冪等）
- 破壊的migrationは適用しない。事前にbackup/復元確認が必須
- rollback: `alembic downgrade -1` は開発/検証DBで事前検証後に限る

## 5. 本番スモークテスト（復旧後・リリース後）

```bash
curl -s -o /dev/null -w 'health:%{http_code}\n' http://127.0.0.1:55019/health
curl -s -o /dev/null -w 'readyz:%{http_code}\n' http://127.0.0.1:55019/readyz
curl -s -o /dev/null -w 'public:%{http_code}\n' --max-time 15 https://cwwd.mirai-dx-platform.com/
# ログイン・/api/sites・/api/dashboard/site-risk はadminセッションで確認
```

## 6. データ訂正手順

1. 対象範囲を特定し、バックアップ（直近dump）を一時DBへ復元して現状と差分確認
2. 訂正SQLをレビュー（影響・監査・承認）し、**本番直SQLは原則禁止**
3. 管理者API/アプリ操作で訂正。直SQLが不可避な場合はバックアップ＋承認記録付きで実施
4. 訂正内容を監査ログ・運用日誌へ記録

## 7. メンテナンス手順

```bash
# 一時停止（timerを止めて手動実行）
sudo systemctl stop cwwd-db-backup.timer
sudo systemctl start cwwd-db-backup.service   # 手動実行
sudo systemctl start cwwd-db-backup.timer
```

## 8. インシデント記録テンプレート

```text
発生日時: ____ / 検知: ____ / 復旧: ____
分類・優先度: ____
症状: ____
原因: ____
対応: ____
再発防止策: ____ / 担当: ____ / Issue番号: ____
```

## 9. 未整備事項

- 外部死活監視の実設定（#116）・通知の実送信確認
- 担当者名の具体化（本Runbookの連絡先はプレースホルダ）
