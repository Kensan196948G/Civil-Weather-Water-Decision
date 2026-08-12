# WMCDSS 統合記録（2026-08-12）

> ユーザー判断（2026-08-12）: 旧 WMCDSS は本プロジェクト（Civil-Weather-Water-Decision,
> 以下 CWW-D）へ統合する。本ファイルは統合の経緯・移植内容・残課題を記録する。

## 1. 背景

- 旧 WMCDSS（weather-marine 施工判断支援）は GitHub リポジトリ消失後に private 再作成・
  履歴保全済み（Kensan196948G/wmcdss、PR #1）。
- 総合評価（18カテゴリ平均 57.8→66.0、代替率 39%→52%）の結果、機能が重複する CWW-D
  （河川・WBGT・Entra OIDC・通知・現場別権限・SLO/Runbook 実装済み）へ統合する方針を承認。

## 2. 移植・統合内容（本PR）

| 項目 | 内容 |
|---|---|
| NOWPHAS 海象コレクタ | `backend/app/services/data_collectors/nowphas.py` 新規。国交省リアルタイムXML（局マスタ/波浪実況/潮位）を取得・正規化、品質フラグ（OK/MISSING/OUTLIER）、鮮度ガード（2h）、最近傍局選定（最大200km）、プロセス内キャッシュ（局1h/実況5min） |
| 判定への優先統合 | `assessment._cached_marine` が NOWPHAS を優先、NO_STATION/ERROR 時のみ Open-Meteo へフォールバック。`source_marine` に実際の source_id を記録（出所の可視化） |
| データソース監視 | `source_probe.PROBE_TARGETS` と `seed.SOURCES` に DS-NOWPHAS を追加 |
| 設定 | `nowphas_base_url` / `nowphas_max_distance_km`（env で上書き可） |
| 鮮度ガード | WMCDSS の「古い観測値は欠測扱い（fail-closed）」方針を NOWPHAS 実況に適用（2時間超は NO_STATION 扱い） |

## 3. 検証

- backend: `pytest -q` → **471 passed**
- ruff（CI と同一ルール）: clean
- frontend logic / adapter / policy / vendor テスト: 全 passed
- NOWPHAS 実測（2026-08-12）: 121局・東京湾→京浜港(横浜) 潮位 1.16m 取得

## 4. WMCDSS 由来の残課題（CWW-D 側で検討）

| 課題 | 現状 | 推奨 |
|---|---|---|
| 判定スナップショット（inputs/thresholds を JSONB 保存） | CWW-D は DecisionLog に理由・サマリ保存 | 必要なら入力値スナップショット拡張（Phase 2） |
| 判定の責任主体（generated_by） | CWW-D は audit_log に actor 記録 | 追加対応不要（監査で代替済み） |
| 予報カードのサンプル明示 | CWW-D は Open-Meteo 予報を表示 | 対応済み |
| M365 ROPC → 対話型 OIDC | CWW-D は Entra ID OIDC 実装済み | 対応済み |
| バックアップ外部退避（scp/rclone） | CWW-D docs/backup-restore.md に手順あり | スクリプトへの統合は Phase 2 |

## 5. 本番デプロイ先

Cloudflare Tunnel + Neon PostgreSQL（CWW-D 既存構成）を継続利用。
