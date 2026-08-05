# リリースノート / Release Notes

## 2026-08-05 — 外部評価P0ロードマップ（#112〜#119）実装・本番反映

### 変更内容

- **河川実測の判定組み込み（#112 / PR #122）**: 最寄り/上流観測所の最新実測を評価へ反映。
  水位上昇率0.2m/h以上で `rising`、上流雨量ルール、30分超/欠測/ERRORはレベル3（確認不能）。
  判定理由に実測値・出典・時刻を記録。
- **環境省WBGT地点マスタ（#113 / PR #124）**: 地点マスタ同期API/CLI、全地点予報CSV
  （`yohou_all.csv`）対応、現場別最近傍自動選定（明示リンク優先）。
- **現場単位権限（#117 / PR #126）**: `user_site_access` 追加。site_manager/viewer は割当現場のみ、
  未割当は403。管理API（grant/revoke）と `/api/me/sites` を追加。
- **Entra ID OIDC（#118 / PR #127・Approval PR）**: Authorization Code + PKCE、id_token検証
  （JWKS/iss/aud/exp/nonce）、グループ→ロール、auto-provision。既定 `AUTH_MODE=app` のため
  現行認証は互換。本番切替は IT 部門と共同で実施。
- **ドキュメント/運用整備（#114/#115/#116/#119 / PR #123/#125/#128/#129）**:
  Open-Meteo商用条件確認書、オフサイトバックアップ転送・監視スクリプト/systemd、
  外部死活監視手順・ヘルパー、本番UI受入試験チェックリスト。

### 検証結果

| 項目 | 結果 |
| --- | --- |
| backend pytest | 417 passed |
| frontend tests / E2E | PASS |
| CI（各PR） | 全5ジョブ success |
| 本番反映 | 2026-08-05 21:40 JST、main反映＋alembic upgrade＋systemd再起動 |
| 本番DB | `user_site_access` 追加（0件）、alembic_version=h1i2j3k4l5m6 |
| 本番スモーク | health/readyz/login/sites/user-site-access/OIDC status/public 302 全PASS |

### 残課題

- #114 Open-Meteo契約要否は法務/IT承認待ち
- #115 実転送先設定と復元訓練、#116 外部SaaS設定と通知確認
- #119 3現場・30日間の受入試験実施
- #118 Entra側のアプリ登録・条件付きアクセス（IT部門共同）

## 2026-08-05 — 河川観測基盤・外部評価P0対応（PR #120 / v0.1.0）

### 変更内容

- **河川観測基盤（#29/#31）**: 観測所マスタ（`observation_stations`）・現場紐付け
  （`site_stations`、上流/最寄り/参照）・実測値（`river_observations`）と、
  観測所CRUD・紐付け・手動実測・時系列APIを追加。RBAC（admin/tech_manager）・監査同一tx・
  一意/上限・削除保護を実装。
- **UI明示**: 「河川観測」画面と「自動取得は未接続（未実装）」の警告表示、現場一覧の
  「河川:手動」バッジ。実測済みとの誤認を防止。
- **設計書**: 河川観測アーキテクチャ / Entra ID OIDC / 現場単位権限 / 閾値安全審査表 /
  PoC受入基準（3現場・30日間）を作成。
- **文書同期**: deploy.md・READMEの陳腐化修正、Issue #29/#31再設計、#112〜#119起票。

### 検証結果

| 項目 | 結果 |
| --- | --- |
| backend pytest | 405 passed（新規8件含む） |
| frontend tests / E2E | PASS |
| CI（PR #120 / main push） | 全5ジョブ success |
| 本番反映 | 2026-08-05 18:58 JST、backend/frontend再起動、app-health-check 全項目 PASS |
| 本番DB | 新規3テーブル作成（0件）、既存データ影響なし、alembic_version=a1b2c3d4e5f6 |

### 注意（操作ミスの報告）

検証中に `alembic upgrade head` をテストDB指定なしで実行し、本PRのマイグレーションが
本番Neonへ先行適用されてしまいました（2026-08-05、追加テーブルのみ・データ0件・影響なし）。
以降はDBコマンド実行時にテストDBを明示する運用へ変更します。

## 2026-08-01 — リリース後安定化（PR #108 / #109）

### 修正・改善

- **DBバックアップ復旧**: `db-backup.env` の認証情報不整合により7/13以来停止していた日次ダンプを復旧。
  systemd経由で backup / 暗号化export / 鮮度チェック / 復元ドリルが全て成功することを実機検証。
- **backend セキュリティ強化**: 全APIレスポンスにセキュリティヘッダ11項目を付与。
  本番では `/docs` `/redoc` `/openapi.json` を無効化（404）。
- **frontend ハードニング（PR #104内容を含む）**: `HOST=127.0.0.1` への loopback bind 化、
  セキュリティヘッダ＋CSP report-only、同一オリジン `/api` proxy、CDN→vendor self-host、
  `?api=` の公開/開発オリジン制限。E2E（Playwright Firefox）を含むテスト5種をCIへ追加。
- **監視基盤の正常化**: ops-status / json-export / json-check を含む全監視ユニットが
  `Result=success`、failed unit 0件を確認。
- **branch protection 有効化（Issue #56）**: main は PR必須＋CI 5種（strict）＋admin適用、
  force push・削除禁止。
- **CI メンテナンス（PR #109）**: Node 20 deprecated 警告解消のため
  checkout v5 / setup-python v6 / setup-node v5 へ更新、node 22 LTS へ移行。
- **リストア実証**: 本番ダンプを PostgreSQL 18 一時クラスタへ `db-restore.sh` で復元し、
  全18テーブル・行数・`alembic_version` が本番と一致することを確認。
  復元先は **PG17 以上**が必要（`transaction_timeout` 対応）であることを文書化。
- **PR #98 対抗レビュー**: notification_deliveries migration を冪等化
  （既存テーブルはスキップして revision のみ記録、回帰テスト3件追加）。

### 検証結果

| 項目 | 結果 |
|---|---|
| backend pytest | 389 passed（#98ブランチは391 passed） |
| frontend tests / E2E | 74件 / Playwright Firefox PASS |
| 実機チェック | health / readyz / security-surface / network-exposure / public-edge 全PASS |
| CI | PR #108 / #109 とも全6チェック PASS |

## 2026-07-23 — 本番反映・修正

- ログイン画面のデモ資格情報ヒントを `env=local` 限定に修正（PR #105）。
- 本番稼働中のブランチを main へ同期し backend/frontend を再起動（#107 までの main 反映）。
- state.json の KPI を実測値（backend 383 / frontend 47）へ同期（PR #106）。

## 2026-07-13 — 本番ハードニング（PR #90 / #92 / #94 / #96）

- 認証本番ハードニング: JWT失効台帳・ログイン試行制限・本番起動ガード（#89）。
- `?api=` による Token Exfiltration 脆弱性の修正（#91）。
- DBバックアップ自動化と ops 監視基盤（#95）: 日次dump / 暗号化export / 鮮度・復元ドリル /
  app-health / security-surface / network-exposure / public-edge チェック。
- `backups/` を .gitignore へ追加（#93）。

## 2026-07-12 — Phase 1 完了 / 本番公開基盤（PR #77〜#86 ほか）

- Neon PostgreSQL への本番切替（project `shiny-frog-23437883`, us-east-2）。
- Cloudflare Tunnel `cwwd-civil-weather-water` 開通・DNS ルーティング（人間実行）。
- Cloudflare Access アプリ `cwwd` / ポリシー `CWWD` によるエッジ認証（302 実測）。
- backend/frontend/tunnel の systemd 常駐化（Issue #77）。
- 監査ログの同一トランザクション化（#63）、設定API＋AI設定（#80）、
  川の防災情報リンク管理（#30）、メニュー体系拡張（#79）、README 更新（#86）。

## 2026-06-19 — プロジェクト登録 / PoC

- プロジェクト登録（M0: 基盤準備 2026-07-02 達成）。
- 判定エンジン・データソース8種・WebUI（ClaudeDesign）のPoC実装。
