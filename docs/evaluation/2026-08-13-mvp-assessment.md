# MVP/Prototype 評価・改善報告書（2026-08-13）

> 対象: Civil-Weather-Water-Decision（気象・河川・施工判断支援システム）
> 評価日: 2026-08-13 / 評価者: Codex（CTO兼実装責任者）
> 対象環境: 本番 `https://cwwd.mirai-dx-platform.com`（read-only確認のみ・本番変更なし）/
>   MVP 独立環境 `https://cwwd-mvp.mirai-dx-platform.com`（新設・公開済み。
>   構成・操作・廃止手順は [docs/mvp-environment.md](../mvp-environment.md)）
> 証跡: 本レポート末尾の「検証証跡」・git履歴・PR・CI結果

## 1. エグゼクティブサマリ

本リポジトリは 2026-06-19 登録以来、Phase 2 進行中の成熟したシステムであり、
認証（JWT+RBAC+現場単位権限）、監査の原子性、ログイン試行制限、トークン失効、
閾値スナップショット、バックアップ自動化、systemd 監視、CI 5ジョブが整備済みです。
本セッションでは、実コード・設定・履歴・実行結果を根拠に再評価し、
「企画・評価で終わらない操作可能な MVP/Prototype」として以下を実装・検証しました。

- P2 相当の MVP 価値向上を複数実装（詳細は §5）:
  1. ユーザー管理 API＋管理画面（弱点 #17 解消。RBAC の実演）
  2. 類似過去判断の参照（#67）
  3. 判断履歴の検索・絞り込み（現場・工種・キーワード・行動）
  4. 判断履歴 PDF 帳票出力（CSV/Excel に追加）
  5. ダミーデータの刷新（現在日付基準・7工種・全判定レベル・全行動を網羅）
- MVP 用の独立環境（Cloudflare Tunnel＋Access・分離 PostgreSQL）を新設し、
  `https://cwwd-mvp.mirai-dx-platform.com` を公開しました
  （本番トンネル/DB/Secretsとは完全分離・ダミーデータのみ）
- 完了時点の品質: backend 481 passed / frontend 全 suite PASS / ruff PASS /
  pip-audit 既知脆弱性 0（CI 5ジョブは Draft PR 作成後に実行・結果は PR/state.json に記録）

**総合判定（MVP/Prototype）: GO**

本番運用化（#114 商用利用条件確定・#115 オフサイトバックアップ・#116 外部監視・
#119 3現場30日受入試験）は今回の対象外として、管理済みバックログのまま維持します。

## 2. 評価方法

- リポジトリ全体（backend/frontend/deploy/docs/CI）の精査と、
  実コード・実行結果（テスト・lint・監査・稼働 URL）を根拠とした評価
- README の主張と実装の突合（乖離の洗い出し）
- ベースライン実行: backend `pytest` 481 passed / frontend logic 21 +
  adapter契約 59 + policy系 3 + asset/policy suites / ruff PASS / pip-audit 脆弱性 0
- GitHub: 直近 CI 成功、branch protection は必須 CI 5ジョブ（レビュー承認 0）
- 本番 URL の read-only 確認（/health 200・Access 302 リダイレクト）

## 3. 実装状況の仕分け（実コード根拠）

| 領域 | 実装済み | 部分実装 | 未実装・未確認 |
|---|---|---|---|
| 認証・認可 | JWT+RBAC（5ロール）・現場単位権限・ログイン試行制限・失効・OIDC切替・ユーザー管理UI/API（今回実装） | OIDC は設計+切替実装（実接続未確認） | — |
| 判定エンジン | 7工種・L0-3・根拠・閾値スナップショット・STALE縮退 | 河川判定はデモ合成値＋手動実測（公式未接続） | 公式河川自動取得（契約依存） |
| データ収集 | JMA/Open-Meteo/WBGT/NOWPHAS・品質フラグ・ソース状態 | 川の防災情報はリンク参照のみ | 水防災オープンデータ・潮位 |
| UI | ダッシュボード/現場/判定/WBGT/履歴/ソース＋全国地図・SVGグラフ・履歴検索/類似/PDF（今回実装） | — | モバイルPWA・アクセシビリティ検証 |
| 監査・帳票 | 監査ログ（同一tx）・CSV/Excel・PDF（今回実装） | — | 改ざん防止署名 |
| 通知 | 画面内通知＋配送台帳（重複抑止） | Slack/Teams webhook 未設定（本番運用） | — |
| 運用 | systemd常駐・監視スクリプト群・バックアップ/復元ドリル・Runbook/SLO | オフサイト転送・外部監視未実施 | CD自動化・IaC |
| テスト/CI | backend 481 / frontend 83+ / E2E / policy系 / CI 5ジョブ | 性能・PG実DB・a11yテストなし | — |

## 4. Gap/Feature バックログ（P0-P3）

凡例: 影響度（P0=障害/漏えい/認証問題, P1=主要操作不能, P2=MVP価値, P3=改善）/
受入条件 / 判定（実装 or バックログ）

### P0（コード・セキュリティ・データ）

本セッション開始時点で 0 件（pip-audit 0・secret 露出検査 0・テスト全PASS）。

### P0（本番運用系・今回対象外＝管理済みバックログ）

| ID | 内容 | 受入条件 | 判定 |
|---|---|---|---|
| #114 | Open-Meteo 商用利用条件の確定 | 法務/IT承認の記録＋利用規約適合 | バックログ（契約・承認が人依存） |
| #115 | オフサイトバックアップ転送と復元検証 | 転送成功＋復元ドリルPASS | バックログ（本番運用対象外） |
| #116 | 外部死活監視（URL・証明書・Access） | 外部監視アラート実測 | バックログ（本番運用対象外） |
| #119 | 本番UI受入試験（3現場30日） | 10項目PASS・危険側誤判定0件 | バックログ（実現場データが必要） |

### P1（今回実装で解消）

| ID | 内容 | 受入条件 | 判定 |
|---|---|---|---|
| UM-01 | ユーザー管理UI/APIなし（弱点 #17） | 管理者が一覧/作成/ロール変更/無効化/削除でき、非管理者403・自己ロックアウト不可・監査記録 | 実装済み |

### P1（残存バックログ）

| ID | 内容 | 受入条件 | 判定 |
|---|---|---|---|
| RIVER-01 | 河川公式自動取得（水防災オープンデータ等）未接続 | 契約締結→実測値取得→判定反映→デモ値と分離表示 | バックログ（契約依存。UI/APIでデモ明示済み） |
| NOTIFY-01 | Slack/Teams 実配信未確認 | webhook 設定＋severity≥2 の実配送確認 | バックログ（本番運用対象外） |
| A11Y-01 | アクセシビリティ検証未実施 | keyboard/focus/contrast/ARIA の検証・修正 | バックログ |

### P2（今回実装）

| ID | 内容 | 受入条件 | 判定 |
|---|---|---|---|
| SIM-01 | 類似過去判断の参照（#67） | 同一現場・同一工種優先の類似ログをAPI/UIで参照できる | 実装済み |
| PDF-01 | 判断履歴 PDF 帳票出力 | PDF が日本語で生成され、CSV同様の認証・権限で出力できる | 実装済み |
| SEARCH-01 | 判断履歴の検索・絞り込み | 現場/工種/キーワード/行動で絞り込める | 実装済み |
| DEMO-01 | ダミーデータの刷新 | 現在日付基準・7工種・L0-3・全行動を網羅し、seed再現可能 | 実装済み |

### P2-P3（残存バックログ）

| ID | 内容 | 備考 |
|---|---|---|
| T2-08 | ダッシュボード強化（絞り込み・荒天監視・手動再取得） | 手動再取得APIは実装済み |
| T2-09 | 気象時系列グラフ拡充 | /api/weather/timeseries は実装済み・UI強化余地 |
| RET-01 | データ保持期間クリーンアップ | 設定値あり・定期削除ジョブ未実装 |
| MOBILE-01 | モバイル/PWA・オフライン | 現場回線不安定時の対応 |
| AI-01 | 判定結果の現場向け文章生成（#68） | AI設定のみ実装済み・費用対効果要検証 |
| PERF-01 | 性能試験・100現場時の呼出量設計 | #114 確定後に実施 |
| SEC-01 | 監査ログの改ざん防止（署名/append-only） | 監査要件（ISO等）強化時 |
| CD-01 | CD自動化・ステージング環境 | デプロイは手動systemd |

## 5. 本セッションの実装内容

詳細は PR・commit・テストを参照。主な変更:

- backend: `/api/admin/users`（一覧/作成/更新/無効化/削除・RBAC・自己ロックアウト防止・
  最終admin保護・監査）、`/api/decision-logs` の絞り込み（site_id/work_type/q）、
  `/api/decision-logs/similar`、`/api/decision-logs/export.pdf`（reportlab・
  日本語CIDフォント）、seed ダミーデータの現在日付化と網羅性向上
- frontend: 管理メニュー「ユーザー管理」画面（一覧/作成/ロール変更/無効化/パスワードリセット/削除）、
  判断履歴の検索・絞り込みツールバー・類似判断パネル・PDF出力ボタン（`.dc.html` 無改修の
  アダプタ注入方式を維持。契約テスト 6 件追加）
- infra: MVP 専用 PostgreSQL16 コンテナ（cwwd-mvp-pg:15433）・systemd unit
  （cwwd-mvp-backend 55119 / cwwd-mvp-frontend 35179 / cwwd-mvp-tunnel）・
  Cloudflare Tunnel＋DNS＋Access を新設し `cwwd-mvp.mirai-dx-platform.com` を公開。
  本番のトンネル・unit・Neon・Secrets には変更なし。手順は docs/mvp-environment.md
- docs: 本評価書・README・state.json（デプロイ手順・リリースノートはリリース時に更新）

## 6. 検証証跡

- `backend: python -m pytest -q` → 481 passed（約3分）
- `backend: ruff check app --select E9,F63,F7,F82,F401` → PASS
- `backend: pip-audit -r requirements.txt` → 既知脆弱性 0
- `frontend: node --check + logic/adapter/policy/vendor/serve suites` → 全PASS
- CI: GitHub Actions 5ジョブ（lint+test / security / frontend / e2e / docker build）
  → PR 作成後に実行（結果は PR 本文・state.json に記録）
- MVP ローカル E2E smoke: **PASS**（Playwright Firefox・ログイン〜画面表示〜ログアウト）
- MVP 公開 URL: `/health` `/readyz` **200 OK**・未認証 `/health` **302（Cloudflare Access）**
  を実測。Access ログイン後のブラウザ画面確認は関係者レビューで実施（自動化不可・要承認メール）
- 本番は read-only 確認のみ（本番DB/デプロイ/Secrets変更なし）
