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

## 🖥️ WebUI

WebUI は ClaudeDesign 生成 UI（`frontend/design/`）＋ `data-adapter.js` による実 API 接続で動作します。
詳細は [frontend/README.md](./frontend/README.md) を参照。

### 🌐 アクセス

| 経路 | URL | 備考 |
|---|---|---|
| 公開（Cloudflare Tunnel + Access） | `https://cwwd.mirai-dx-platform.com/` | 許可メンバーのみ（Access ログイン） |
| LAN | `http://<LAN-IP>:34979/?api=http://<LAN-IP>:55019` | 例: 192.168.0.185 |

backend(55019)・frontend(34979)・cloudflared は **systemd 常駐**（OS 起動時に自動起動、`deploy/systemd/` 参照）。

### 🗺️ メニュー体系（#72/#79）

```
📊 ダッシュボード
🏗️ 現場管理        現場一覧 / ＋現場登録
🌤️ 気象・海象データ  気象データ：全国版 / 海象データ：全国版※
⚖️ 施工判定        作業判断 / コンクリート打設 / 海上作業※ / 熱中症・WBGT
📈 分析           判断履歴 / 過去データ分析 / 50年確率波※
⚙️ 管理           閾値管理 / データ取得状況 / レポート出力 / 監査ログ / 設定（通知・保存期間・AI設定）
```
※印は準備中（データソース調査・蓄積が前提。エピック #72 段5-7）。
管理系はロール制御（閾値管理・監査ログ=admin/技術管理者、設定=admin）。

## 開発ステータス / Status

| 項目 | 内容 |
|---|---|
| フェーズ | Phase 2 進行中（Phase 1 MVP完了・Phase 3 認証/監査/通知は大幅先行・Phase 4 CI/CD着手済み） |
| 登録日 | 2026-06-19 |
| 本番リリース期限 | 2026-12-19（登録から6ヶ月） |
| テスト状況 | backend 71 / frontend logic 21 + adapter契約 14、全pass（2026-07-12時点） |
| CI | GitHub Actions（backend lint+test / 依存脆弱性スキャン / frontend test / docker build） |

マイルストーン・タスクは GitHub Issues / Milestones で管理します。

## 技術スタック / Tech Stack

- フロントエンド: ClaudeDesign 生成の静的 UI（dc-runtime、React 18 を CDN 経由でロード）＋ `data-adapter.js` による実 API 接続（`.dc.html` 無改修）
- バックエンド: FastAPI (Python 3.12) + SQLAlchemy + Alembic + APScheduler
- 認証/認可: JWT + RBAC（管理者・技術管理者・現場管理者・安全担当・閲覧）＋ 監査ログ（ドメイン変更と同一トランザクション記録）
- DB: **Neon PostgreSQL**（本番・2026-07-12 切替済み）/ SQLite（テスト）※同一 Alembic マイグレーションが両対応
- 公開基盤: systemd 常駐（backend/frontend/cloudflared）＋ Cloudflare Tunnel ＋ Cloudflare Access（エッジ認可）
- CI/CD: GitHub Actions（lint・test・依存脆弱性スキャン・docker build）＋ Codex 対抗レビュー/CodeRabbit

> フロントエンドは PoC 当初計画（React+Vite+TypeScript+Tailwind の自前実装）から、ClaudeDesign 生成 UI ＋ 外部データアダプタ方式へ変更されています。詳細は [frontend/README.md](./frontend/README.md) を参照してください。

## ライセンス / License

[LICENSE](./LICENSE) を参照してください。
