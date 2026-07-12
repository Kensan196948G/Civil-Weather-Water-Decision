# Civil-Weather-Water-Decision

Construction Weather & Water Decision Support
気象・河川・施工判断支援システム

## 概要 / Purpose

気象庁・国土交通省 川の防災情報・環境省 WBGT・Open-Meteo などの公開データを統合し、
土木建設現場の作業判断（コンクリート打設・クレーン作業・河川内作業・土工・舗装・熱中症対策）を
支援する Web アプリケーションです。

## ⚠️ 重要なお知らせ / Important Notice

**本システムは施工判断を「支援」するものであり、作業の実施・中止・延期を自動決定するものではありません。**
最終判断は現場責任者が、発注者指示・契約条件・施工計画書・安全衛生計画・社内基準・現地目視確認・
気象庁/国土交通省/自治体等の公式発表を踏まえて行ってください。

システムは「いつ・どのデータを見て・なぜ注意と判断したか」の根拠と注意レベルを提示し、
データの欠測・遅延・不整合を隠さず明示します。

## 対象作業 / Initial Target Work Types

- コンクリート打設
- クレーン作業
- 河川内作業
- 土工
- 舗装
- 熱中症対策

## 主なデータソース / Main Data Sources

- 気象庁（防災情報XML・気象データ高度利用ポータル）
- 国土交通省 川の防災情報
- 水防災オープンデータ提供サービス
- Open-Meteo（予報・補完）
- 環境省 暑さ指数 WBGT
- NASA POWER / JAXA G-Portal・Earth API（将来拡張）

## 判断レベル / Decision Levels

| レベル | 表示 | 意味 |
|---|---|---|
| 0 | 通常 | 主要な注意条件なし（「作業可能」と断定しない） |
| 1 | 注意 | 一部条件に注意。現地確認・追加確認が必要 |
| 2 | 中止検討 | 中止・延期・作業方法変更を検討すべき条件あり |
| 3 | 確認不能 | 欠測・遅延・取得失敗で判断材料が不足 |

## ドキュメント / Documentation

- [要件定義書](./Civil-Weather-Water-Decision_Requirements.md)
- [詳細仕様設計書](./Civil-Weather-Water-Decision_Detailed_Design.md)
- [実装計画書（ロードマップ・WBS・リスク）](./docs/implementation-plan.md)

## WebUI

WebUI は ClaudeDesign で作成し `frontend/` に取り込み済み（6画面 SPA）。
バックエンド API へのデータ接続は外部アダプタ方式（`#46`、`data-adapter.js`）で実装済み。
起動方法・ClaudeDesign 再取り込み手順・データ接続の仕組みは [frontend/README.md](./frontend/README.md) を参照。

## 開発ステータス / Status

| 項目 | 内容 |
|---|---|
| フェーズ | コア機能実装済み・拡張/品質強化フェーズへ移行中 |
| 登録日 | 2026-06-19 |
| 本番リリース期限 | 2026-12-19（登録から6ヶ月） |

認証/RBAC/監査ログ（#25）・現場CRUD（#14）・判定結果永続化（#23）・気象庁防災情報XML警報の実反映（#26）・
データ接続アダプタ（#46）・定期バッチ（#47）まで実装済み（backend 54 tests pass）。
残課題（河川観測所マスタ正規化・閾値設定UI・データ品質サービス等）は [backend/README.md](./backend/README.md) の
「次フェーズ（残作業）」を参照。マイルストーン・タスクは GitHub Issues / Milestones で管理します。

## 技術スタック / Tech Stack

- フロントエンド: ClaudeDesign 生成 SPA（`.dc.html` + dc-runtime + CDN 経由 React 18。Vite/TypeScript ビルドは不使用）
- バックエンド: FastAPI (Python 3.12) + SQLAlchemy + Alembic
- DB: SQLite（開発）/ PostgreSQL 16（本番候補、同一 Alembic マイグレーションで両対応）
- バッチ: APScheduler（データソース定期プローブ・予報リフレッシュ）
- コンテナ: Docker Compose（postgres + backend + frontend）

## ライセンス / License

[LICENSE](./LICENSE) を参照してください。
