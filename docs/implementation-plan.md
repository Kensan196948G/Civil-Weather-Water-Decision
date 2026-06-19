# 実装計画書 / Implementation Plan

| 項目 | 内容 |
|---|---|
| 文書種別 | 実装計画書（ロードマップ・WBS・Issue 草案・リスク管理） |
| システム名 | Construction Weather & Water Decision Support / 気象・河川・施工判断支援システム |
| リポジトリ | `Civil-Weather-Water-Decision` |
| 前提文書 | `Civil-Weather-Water-Decision_Requirements.md` / `Civil-Weather-Water-Decision_Detailed_Design.md` |
| 作成日 | 2026-06-19 |
| 登録日 | 2026-06-19（金） |
| 本番リリース期限 | **2026-12-19（土）／登録から6ヶ月後（絶対厳守）** |
| 総開発期間 | 183 日 |

---

## 0. この計画書の位置づけ

本書は、要件定義書・詳細設計書で定義済みの「**何を作るか**」を、登録日 2026-06-19 を起点とした「**いつ・どの順で作るか**」へ落とし込む実行計画である。実装はまだ着手しない。本書で全体ロードマップ・依存関係・凍結ラインを固めてから Build フェーズに入る。

設計の最重要制約（断定しない／不確実性を隠さない／公式情報優先）は全フェーズで不変であり、各タスクの完了判定（DoD）にも反映する。

---

## 1. マイルストーンとフェーズ（絶対日付）

設計書 §20（要件ロードマップ）と §24（MVP実装順）を、CLAUDE.md の6ヶ月分割・残日数自動縮退ルールに整合させた。

| フェーズ | 期間 | ゴール | 対応マイルストーン |
|---|---|---|---|
| **Phase 0** 足場・調査 | 2026-06-19 〜 07-02 | リポジトリ・足場・データソース接続検証・画面モック | M0: Foundation Ready |
| **Phase 1** PoC / MVP | 2026-07-03 〜 07-30 | 現場登録〜Open-Meteo〜簡易判定〜ダッシュボード〜判断メモ | M1: PoC Demoable |
| **Phase 2** 機能拡張 | 2026-07-31 〜 09-24 | 河川・WBGT・閾値管理・ダッシュボード強化・データ品質 | M2: Feature Complete |
| **Phase 3** 検証環境 | 2026-09-25 〜 11-19 | 認証・RBAC・監査ログ・通知・複数現場横断 | M3: Verification Ready |
| **Phase 4** リリース準備 | 2026-11-20 〜 12-19 | 統合テスト・安定化・ドキュメント・タグ付け・本番移行準備 | **M4: Production Release（12-19）** |

### 1.1 残日数自動縮退ライン（CLAUDE.md 準拠）

| ライン | 日付 | 適用 |
|---|---|---|
| 残30日 | **2026-11-19** | Improvement 縮退。Verify／リリース準備を優先。Phase 3 はこの日までに機能を入れ切る |
| 残14日 | **2026-12-05** | **新機能開発禁止**。バグ修正・安定化のみ |
| 残7日 | **2026-12-12** | リリース準備のみ（CHANGELOG・README・タグ付け） |

> 含意: 新機能（Phase 2/3 の Should/Could）は **2026-12-05 まで**にマージを完了させる。これ以降に間に合わない機能は Phase 5（リリース後）へ送る。

### 1.2 GitHub Projects に登録するマイルストーン

```
M0  Foundation Ready          due 2026-07-02
M1  PoC Demoable              due 2026-07-30
M2  Feature Complete          due 2026-09-24
M3  Verification Ready        due 2026-11-19   ← 機能凍結ライン
M4  Production Release        due 2026-12-19   ← 絶対厳守
```

---

## 2. 技術スタック確定（設計書 §2.2 PoC候補を採用）

| レイヤー | 採用技術 | 備考 |
|---|---|---|
| フロントエンド | React + Vite + TypeScript | 設計 §2.2 PoC候補 |
| UI | Tailwind CSS + shadcn/ui 系 | |
| 状態/データ取得 | TanStack Query | API キャッシュと取得時刻表示に好適 |
| グラフ | Recharts または ECharts | 気象・河川時系列（SC-004/005） |
| バックエンド | FastAPI (Python 3.12) | 設計 §2.2 |
| ORM / Migration | SQLAlchemy 2.x + Alembic | 13テーブル（設計 §6） |
| DB | PostgreSQL 16 | PoC は Docker、本番候補 Azure DB for PostgreSQL |
| バッチ | APScheduler | JOB-001〜007（設計 §11） |
| キャッシュ | In-memory（PoC）→ Redis（本番候補） | |
| 認証 | 簡易トークン（PoC）→ Entra ID OIDC（本番候補） | Phase 3 で導入 |
| テスト | pytest（backend）/ Vitest + Playwright（frontend） | |
| コンテナ | Docker Compose | 設計 §21 |
| CI | GitHub Actions（lint/test/build） | Phase 0 で雛形 |

> 確定理由: 設計書がすでに FastAPI / React / PostgreSQL / Docker を前提に DB スキーマ・API・`.env`・compose まで具体化しているため、PoC候補をそのまま採用するのが最短。本番候補スタック（Azure）への移行は Phase 4 で評価。

---

## 3. WBS（フェーズ別タスク分解）

各タスクに ID を付し、参照要件（FR）・画面（SC）・設計章を併記する。`P` は優先度（Must/Should/Could）。

### Phase 0 — 足場・調査（06-19 〜 07-02）

| ID | タスク | 参照 | P | DoD |
|---|---|---|---|---|
| T0-01 | git 初期化・`.gitignore`・ブランチ戦略・`.env.example` | 設計§19,§20 | Must | `.env` 非追跡、main 保護 |
| T0-02 | ディレクトリ構成生成（frontend/backend/batch/database/samples/infra/docs） | 設計§19 | Must | 構成図どおり存在 |
| T0-03 | README 初期版（断定禁止の注意文を含む） | 設計§25 | Must | 「最終判断は現場責任者」明記 |
| T0-04 | docs 移設（requirements/detailed-design を docs/ へ）＋本計画書 | 設計§19 | Must | docs/ に集約 |
| T0-05 | docker-compose（postgres/backend/frontend）雛形・起動確認 | 設計§21 | Must | `docker compose up` で3コンテナ起動 |
| T0-06 | FastAPI 雛形（/health, 設定読込, ロガー） | 設計§13 | Must | `GET /health` 200 |
| T0-07 | React + Vite 雛形（Tailwind, ルーティング, レイアウト, フッター注意文） | 設計§10.2 | Must | トップ表示 |
| T0-08 | CI 雛形（lint + test 空ジョブ） | 設計§18 | Should | PR で CI 実行 |
| T0-09 | **データソース接続検証**: Open-Meteo / WBGT / 気象庁XML の疎通・レスポンス形状調査 | 要件§5, 設計§4 | Must | サンプルJSON/XMLを samples/ に保存・形状文書化 |
| T0-10 | 画面モック（ダッシュボード/現場詳細/作業判断）静的版 | SC-001/002/003 | Should | クリック可能なモック |
| T0-11 | `docs/api-data-source-catalog.md` 起票（取得元・条件・制限） | 要件§5,§22 | Should | 各APIの利用条件記録 |

**M0 完了条件**: compose で空アプリが起動し、Open-Meteo/WBGT の実データ形状が判明し、主要3画面のモックがある。

### Phase 1 — PoC / MVP（07-03 〜 07-30）

設計 §24.1 の実装順をそのまま採用。

| ID | タスク | 参照 | P | DoD |
|---|---|---|---|---|
| T1-01 | DBスキーマ実装（13テーブル）＋Alembic 初期マイグレーション | 設計§6, FR全般 | Must | `alembic upgrade head` 成功 |
| T1-02 | シードデータ（サンプル現場3件・作業種別6種・ルール） | 要件§18.1 | Must | 実在現場名を使わない（設計§16.3） |
| T1-03 | 現場 CRUD API（/api/sites …） | FR-001〜005, SC | Must | 緯度経度付き登録・一覧・詳細 |
| T1-04 | 現場登録/一覧/詳細 画面 | SC-002 | Must | サンプル現場が表示される |
| T1-05 | 作業予定 API（/api/work-plans …） | FR-011〜014 | Must | 登録・一覧・日別表示 |
| T1-06 | 作業予定 画面 | SC-003 | Must | 作業判断画面に表示 |
| T1-07 | Open-Meteo コレクタ（取得・正規化・品質フラグ・保存） | FR-021〜025, 設計§4.3,§5 | Must | 取得時刻・source_id 保持、欠測=MISSING |
| T1-08 | 気象予報 API（/api/weather/forecast, /timeseries） | FR-022,024 | Must | 取得元・時刻を返す |
| T1-09 | WBGT 参照/取り込み・API | FR-041〜043, SC-006 | Must | WBGT 値 or 代替表示 |
| T1-10 | **判定エンジン（簡易版）**: 入力抽出→ルール評価→理由生成→最大重大度→欠測=確認不能 | 設計§8,§9, FR-051〜053 | Must | 入出力JSON（設計§8.1/8.2）準拠 |
| T1-11 | 判定ルール（コンクリート/クレーン/河川/土工/舗装/熱中症 初期値） | 設計§9, 要件§11 | Must | 4段階レベルと理由文 |
| T1-12 | `POST /api/work-plans/{id}/evaluate` 結果保存 | 設計§7.2.2 | Must | decision_results/reasons 保存 |
| T1-13 | ダッシュボード（現場別リスク一覧） | SC-001, FR | Must | 最大リスク・主要理由・取得時刻 |
| T1-14 | 判断メモ登録（実施/延期/中止/再開/監視継続） | FR-061〜063, SC-007 | Must | decision_logs 保存 |
| T1-15 | データソース状態画面 | SC-008, FR-081,082 | Must | 接続状態・最終取得時刻 |
| T1-16 | CSV出力（判断履歴） | 設計§23, FR-055 | Should | 検索条件に合う履歴出力 |
| T1-17 | 判定エンジン単体テスト（TC-003〜006） | 設計§18.2 | Must | 主要ケースgreen |

**M1 完了条件 = PoC 受入条件（要件§18.1）**: サンプル現場3件登録／主要気象データ取得表示／作業別注意レベル＋理由文／判断メモ登録／取得元・時刻表示／欠測時「確認不能」。

### Phase 2 — 機能拡張（07-31 〜 09-24）

| ID | タスク | 参照 | P | DoD |
|---|---|---|---|---|
| T2-01 | 河川観測所マスタ・現場紐付け（site_stations） | FR-031, 設計§6.2.6 | Must | nearest/upstream/reference 区分 |
| T2-02 | 川の防災情報リンク管理（現場別公式リンク） | FR-035, DS-RIVER-GO | Must | 現場詳細に公式導線 |
| T2-03 | 河川観測 取り込み・時系列API・画面 | FR-032〜034, SC-005 | Should | 水位・上流雨量・閾値表示 |
| T2-04 | 気象庁 防災情報XML連携（警報・注意報） | FR-023, DS-JMA-XML | Should | 公式警報で注意レベル引上げ（設計§8.3-6） |
| T2-05 | データ品質サービス（欠測/遅延/異常値/重複の一元チェック） | 設計§5, FR | Must | STALE/OUTLIER/DUPLICATE 付与 |
| T2-06 | 閾値設定画面（現場・工種・会社基準） | FR-054, SC-009 | Should | 画面から閾値変更→判定反映 |
| T2-07 | 判定ルール管理API（/api/admin/rules） | 設計§7.2.6 | Should | ルールCRUD |
| T2-08 | ダッシュボード強化（絞り込み・荒天監視・手動再取得） | SC-001操作 | Should | リスク/作業種別フィルタ |
| T2-09 | 気象時系列グラフ（SC-004） | FR, SC-004 | Should | 気温/雨量/風速/湿度 |
| T2-10 | バッチ/スケジューラ（JOB-001〜006・リトライ/バックオフ） | 設計§11 | Should | 失敗を data_source_statuses 記録 |
| T2-11 | データソース監視強化（応答時間・連続失敗・代替案内） | FR-082,083 | Should | 平均応答・連続失敗回数 |
| T2-12 | 結合テスト（取得→保存→判定→表示, TC-007） | 設計§18.2 | Should | 障害時に画面落ちない |

**M2 完了条件**: 河川・WBGT・閾値・データ品質・ダッシュボード強化が動作。Should 機能を入れ切る目標期限。

### Phase 3 — 検証環境（09-25 〜 11-19）

| ID | タスク | 参照 | P | DoD |
|---|---|---|---|---|
| T3-01 | 認証（PoC簡易ログイン→アプリ内ユーザー管理 or Entra ID検証） | 設計§12.1, 要件§9.3 | Must | ログイン/ログアウト |
| T3-02 | RBAC（5ロール権限マトリクス実装） | 設計§12.2, 要件§7 | Must | 権限テスト TC-008 green |
| T3-03 | 監査ログ（ログイン/設定変更/判定/判断/CSV出力） | 設計§13.2, 要件§9.3 | Must | 対象操作が記録 |
| T3-04 | 通知サービス（画面内通知 → メール/Slack/Teams拡張点） | 設計§14, FR-071〜074 | Should | 中止検討/WBGT/河川通知 |
| T3-05 | 複数現場横断ダッシュボード（100現場想定） | 要件§6.2, §9.2 | Should | 横断表示・性能5秒目標 |
| T3-06 | データ取得失敗時の再試行・通知 | 要件§6.2 | Should | 手動再取得・警告 |
| T3-07 | 施工判断履歴の検索・保存強化 | FR-064,065 | Should | 履歴検索 |
| T3-08 | 権限/障害/データ品質テスト一式 | 設計§18.1 | Must | 各テスト green |
| T3-09 | バックアップ/リストア手順（DBダンプ） | 設計§17 | Should | 手動復旧手順書 |

**M3 完了条件 = 検証環境受入条件（要件§18.2）**: 横断ダッシュボード／河川紐付け／WBGT表示／閾値変更／権限制御／監査ログ／CSV出力。**この日（11-19）が機能凍結ライン**。

### Phase 4 — リリース準備（11-20 〜 12-19）

| ID | タスク | 参照 | P | DoD |
|---|---|---|---|---|
| T4-01 | 統合テスト・受入テスト（要件§18, 設計§26 観点） | 設計§26 | Must | 受入観点すべて合格 |
| T4-02 | 性能確認（ダッシュボード3秒/現場一覧5秒, 要件§9.2） | 要件§9.2 | Should | 目標値計測 |
| T4-03 | セキュリティ点検（Secret管理・HTTPS・公開モック禁止事項） | 設計§16 | Must | APIキー非混入・実在現場名なし |
| T4-04 | バグ修正・安定化（残14日以降は本作業のみ） | — | Must | Critical/High 0 |
| T4-05 | ドキュメント整備（operations-guide / security-notes / decision-rule-guide） | 設計§19 | Must | docs 完備 |
| T4-06 | CHANGELOG・README 最終化（残7日: リリース準備のみ） | CLAUDE.md | Must | リリースノート |
| T4-07 | 本番候補構成評価（Azure 移行方針・縮退運転） | 設計§2.2,§15 | Could | 移行判断メモ |
| T4-08 | リリースタグ付け・本番移行準備 | — | Must | `v1.0.0` タグ（12-19まで） |

**M4 完了条件**: 受入テスト合格・安定版タグ・運用ドキュメント完備。**2026-12-19 厳守**。

---

## 4. クリティカルパスと依存関係

```
T0-01/02 足場
   └─> T1-01 DBスキーマ ──┬─> T1-03 現場API ─> T1-04 現場画面
                          ├─> T1-05 作業予定API ─> T1-06 作業予定画面
                          └─> T1-07 Open-Meteoコレクタ ─> T1-08 気象API
                                                            │
              T1-10 判定エンジン <── T1-07/08/09(WBGT) ─────┘
                    │
                    ├─> T1-12 evaluate保存 ─> T1-13 ダッシュボード ─> T1-14 判断メモ
                    │
              [M1 PoC]  ──> Phase2(河川/品質/閾値) ──> Phase3(認証/監査/通知) ──> Phase4(統合/リリース)
```

**最長経路（クリティカルパス）**: 足場 → DBスキーマ → Open-Meteoコレクタ → 判定エンジン → evaluate → ダッシュボード。
→ **判定エンジン（T1-10）が PoC の心臓部**であり、ここが遅れると全体が遅れる。Phase 0 の T0-09（データソース接続検証）で入力データ形状を早期確定させ、判定エンジン着手の手戻りを防ぐ。

**並行可能**: フロントエンド雛形（T0-07/10）はバックエンドと独立して進行可能。河川（Phase2）とWBGT（Phase1）は別データソースなので独立。

---

## 5. GitHub Issue 草案

Epic（マイルストーン単位）＋ 子 Issue（WBS タスク単位）で構成する。ラベル: `epic` / `backend` / `frontend` / `data-source` / `decision-engine` / `infra` / `docs` / `priority:must|should|could`。

### Epic 一覧

```
[EPIC] Phase 0: 足場・調査                 milestone=M0 Foundation Ready
[EPIC] Phase 1: PoC / MVP                  milestone=M1 PoC Demoable
[EPIC] Phase 2: 機能拡張（河川/WBGT/閾値） milestone=M2 Feature Complete
[EPIC] Phase 3: 検証環境（認証/監査/通知） milestone=M3 Verification Ready
[EPIC] Phase 4: リリース準備               milestone=M4 Production Release
```

### 子 Issue テンプレート（例: T1-10）

```md
## [P1] 判定エンジン（簡易版）実装  (T1-10)

### 目的
作業予定・気象/河川/WBGT データを入力に、注意レベル（0通常/1注意/2中止検討/3確認不能）と
理由文を生成する。本システムの心臓部。

### 参照
- 詳細設計 §8 判定エンジン設計, §9 初期判定ルール仕様
- 要件 FR-051〜053, §11 初期判定ルール案
- 入出力JSON: 設計 §8.1 / §8.2

### 受入条件 (DoD)
- [ ] 入力JSON（site/work_type/時間帯/forecasts/observations/quality_flags）を受理
- [ ] 作業時間帯に該当する予報・観測を抽出
- [ ] work_type に紐づく有効ルールを評価し reasons を生成
- [ ] 重大度の最大値を overall_level とする
- [ ] 主要データに MISSING/STALE/SOURCE_ERROR があれば「確認不能(3)」を含める
- [ ] 公式警報がある場合は注意レベルを引き上げる（§8.3-6）
- [ ] 「作業可能」と断定しない（§29）。0は「主要注意条件なし」表記
- [ ] 単体テスト TC-003〜006 が green

### 依存
- T1-01 DBスキーマ, T1-07 Open-Meteoコレクタ, T1-09 WBGT

### ラベル
backend, decision-engine, priority:must
milestone: M1 PoC Demoable
```

> Phase 0 着手時に、上記テンプレートで全 WBS タスク（T0-01〜T4-08）の Issue を一括起票する。GitHub リポジトリ作成後、`gh issue create` または GitHub MCP で自動生成可能。

---

## 6. リスク登録簿

要件§19 のリスクに、実装計画上のリスクを追加。

| ID | リスク | 影響 | 確率 | 対策 | 監視タイミング |
|---|---|---|---|---|---|
| R-01 | 判断の過信（システム表示だけで作業判断） | 高 | 中 | 画面・帳票・通知に「判断支援」「最終判断は現場責任者」明記。0を「作業可能」と表記しない | 全画面レビュー |
| R-02 | 外部API更新遅延・停止 | 中 | 高 | 取得時刻表示・確認不能表示・公式リンク案内・リトライ/バックオフ | 日次 |
| R-03 | 閾値が工種・現場に不適合 | 中 | 高 | 初期値は仮値と明記。現場/技術部門レビュー必須。閾値を画面変更可に | Phase2 閾値設計時 |
| R-04 | 公式警報と外部API予報の差異 | 中 | 中 | 公式警報・注意報を優先（§8.3-6, §5.3） | 判定ロジック実装時 |
| R-05 | 河川データの利用条件・費用（水防災オープンデータ） | 中 | 中 | PoCは公式リンク参照中心。本格利用前に契約・条件確認 | Phase2 着手前 |
| R-06 | 6ヶ月期限超過（スコープ過大） | 高 | 中 | 残30/14/7日の縮退ライン厳守。Should/Could は12-05までにマージ、間に合わなければPhase5送り | 各Monitorフェーズ |
| R-07 | 判定エンジン手戻り（入力形状未確定） | 中 | 中 | Phase0 T0-09 でデータ形状を先に確定 | Phase0 |
| R-08 | Secret/実在現場名の混入 | 高 | 低 | `.env`非追跡・GitHub Secret・公開モック禁止事項チェックをCI/レビューで | 全PR |
| R-09 | 認証・RBAC・DBスキーマ変更時の不具合 | 高 | 中 | CLAUDE.md準拠で Codex 対抗レビュー必須。権限テスト必須 | Phase3 |
| R-10 | データ欠測の「無言の補完」混入 | 高 | 低 | 補完禁止を設計原則化。欠測=MISSINGフラグ表示をテストで担保 | データ品質テスト |

---

## 7. 品質ゲート（CLAUDE.md 準拠）

各 PR は以下を満たしてからマージ:

- test / lint / build / CI すべて success、error 0、security critical 0（STABLE 判定）
- Verify は Codex review（`/codex:review`）＋ CodeRabbit（`/coderabbit:review`）併用。Critical/High は同PR内で解消
- **認証・認可・DBスキーマ・並列処理変更時は Codex 対抗レビュー（`/codex:adversarial-review`）必須**（Phase1 T1-01, Phase3 T3-01〜03 が該当）
- 自動マージは条件付き許可（Trust Level 2以上・Critical/High 0・認証/認可/DBスキーマ/本番デプロイを含まない通常PRに限る）

---

## 8. プロジェクト状態管理

CLAUDE.md 準拠で、プロジェクト直下に `state.json` を保持し、毎 Monitor フェーズで残日数を確認する。

```json
{
  "project": {
    "name": "Civil-Weather-Water-Decision",
    "registered_at": "2026-06-19",
    "release_deadline": "2026-12-19",
    "current_phase": "Phase 0",
    "milestones": {
      "M0_foundation_ready": "2026-07-02",
      "M1_poc_demoable": "2026-07-30",
      "M2_feature_complete": "2026-09-24",
      "M3_verification_ready": "2026-11-19",
      "M4_production_release": "2026-12-19"
    },
    "freeze_lines": {
      "improvement_taper": "2026-11-19",
      "feature_freeze": "2026-12-05",
      "release_prep_only": "2026-12-12"
    }
  }
}
```

---

## 9. 直近の次アクション（Phase 0 着手時）

1. GitHub リポジトリ作成（`Kensan196948G/Civil-Weather-Water-Decision`）
2. 上記5マイルストーンを GitHub Projects に登録（Production Release = 12-19 厳守）
3. WBS（T0-01〜T4-08）を Epic＋子Issue として一括起票
4. `state.json` をリポジトリ直下に配置
5. T0-01〜T0-05（git初期化・ディレクトリ・README・compose）から着手

---

## 10. まとめ

本計画は、完成済みの要件・設計を、登録日起点の絶対日付ロードマップ（M0〜M4）と WBS、依存関係、リスク、品質ゲートに展開した。クリティカルパスは「足場→DBスキーマ→Open-Meteoコレクタ→判定エンジン→ダッシュボード」であり、判定エンジン（T1-10）が PoC の成否を握る。新機能は **2026-12-05（残14日）** までにマージし、**2026-12-19** の本番リリースを厳守する。
