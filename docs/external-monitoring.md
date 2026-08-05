# 外部死活監視（Issue #116 / P0）

> ステータス: **実監視サービスは未設定・要設定**。
> 本ドキュメントは設定手順・当番・復旧手順の正本であり、外部SaaSのアカウント作成と
> アラート通知先の設定は IT・DX部門の作業が必要です。

## 1. 目的

現在は同一Ubuntu上の systemd 監視のみで、ホスト停止・Cloudflare Tunnel障害・
Accessポリシー設定ミスを外部から検知できません。外部監視を導入し、
「ユーザーが見られない」状態をSaaSまたは別ホストから検知します。

## 2. 監視対象と合格条件

| # | 監視対象 | 期待値 | 頻度 | 失敗時の意味 |
| --- | --- | --- | --- | --- |
| 1 | `https://cwwd.mirai-dx-platform.com/` | HTTP 302（Cloudflare Accessリダイレクト） | 5分 | ホスト停止・Tunnel断・Access設定ミス |
| 2 | TLS証明書 | 有効期限残日数 >= 14日 | 日次 | 証明書更新漏れ |
| 3 | `https://cwwd.mirai-dx-platform.com/health` | 302（Access経由）または 200（ポリシー次第） | 5分 | エッジ到達性・Appルート異常 |
| 4 | DNS `cwwd.mirai-dx-platform.com` | 名前解決が成功 | 日次 | DNS/Tunnel設定異常 |

> 注意: 監視本体は「外部」から行うこと。同一ホスト上で動かしてもホスト停止の検知には
> なりません。外部SaaS（UptimeRobot等）か別ホスト/サーバーレス関数を使用します。

## 3. 外部SaaSでの設定例（UptimeRobot 等）

1. アカウント作成後、Monitor を追加:
   - Monitor Type: HTTP(S)
   - URL: `https://cwwd.mirai-dx-platform.com/`
   - 期待ステータスコード: **302**（Cloudflare Access は302を返す）
   - 監視間隔: 5分
   - タイムアウト: 30秒
2. TLS監視（SSL監視）を有効化し、残日数が14日未満でアラート
3. アラート通知:
   - Slack/Teams へは既存 `deploy/scripts/ops-alert.sh` の webhook 設定を流用
     （実URLは環境変数 `SLACK_WEBHOOK_URL` / `TEAMS_WEBHOOK_URL` で管理し、
     コード・ドキュメントへ記載しない）
   - メール通知は UptimeRobot 側の Alert Contact で運用担当アドレスを登録
4. 別ホスト方式の場合: 監視対象ホストに `deploy/scripts/external-readiness-check.sh` を
   cron（5分）で実行し、失敗時は `ops-alert.sh` または直接 webhook へ通知

## 4. 当番・エスカレーション設計

| レベル | 担当 | 対応 | 目標 |
| --- | --- | --- | --- |
| 第1次 | IT・DX運用担当 | アラート受信→Tunnel/バックエンドの状態確認 | 15分以内に認識 |
| 第2次 | 開発（バックエンド/フロント） | アプリ起因の復旧対応 | 営業時間内1時間 |
| 第3次 | 現場システム管理者 | 本番DB・設定・Accessポリシー確認 | 必要に応じ即時 |

当番表は月次で更新し、`docs/deploy.md` または社内運用Wikiへ掲載する。

## 5. 復旧手順（障害時）

```text
1. 外部監視アラート確認（URL/TLS/DNSのどれが失敗したか）
2. 対象ホストへSSHし、systemd 状態を確認:
   systemctl status cwwd-backend cwwd-frontend cwwd-tunnel
3. Tunnel 障害: cloudflared tunnel list / 設定差分確認（deploy/cloudflared-config.yml.example）
4. Access 302 が返らない場合: Cloudflare Access のアプリ・ポリシー設定を確認
5. バックエンド障害: journalctl -u cwwd-backend -n 200 --no-pager で原因特定→再起動
6. 復旧後: 外部監視の直近チェックが PASS になること、障害時間を記録
```

## 6. 導入後の検証

- 監視停止テスト: 一時的に backend を停止し、外部監視がダウン検知→通知送信を確認
- TLSテスト: テスト証明書または期限操作でアラート発報を確認（本番では行わない）
- 月次レビュー: アラート数・復旧時間・当番の遵守状況を確認

## 7. 既知の制限

- Cloudflare Access が302を返すため、認証後画面の内部エラーは外部監視では検知できない
  （内部ヘルスはホスト上の `cwwd-app-health-check` で担保）
- 外部SaaSのアカウント・通知先が未設定の間は、本ドキュメントの手順のみ整備済み
