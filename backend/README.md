# backend — FastAPI バックエンド（PoC）

気象（Open-Meteo ライブ）→ 正規化・品質フラグ → 判定エンジン → API、を提供する。
WebUI（`frontend/`）の `#46 データ接続` の接続先。PoC は **SQLite**（DBサーバ不要）。

## アーキテクチャ / データフロー

```
Open-Meteo (REST) ──fetch──> open_meteo.normalize (単位統一/品質フラグ/WBGT推定)
                                   │
                          window_reading (作業時間帯の代表値抽出)
                                   │
                     assessment.build_reading + decision_engine.evaluate
                                   │  (欠測=確認不能, 公式優先, 断定しない)
                                   ▼
                         FastAPI ルータ (/api/...) ──> WebUI
SQLite: sites / stations / work_types / work_plans / decision_logs / data_source_statuses
```

```
backend/
├── app/
│   ├── main.py                       # FastAPI app / CORS / lifespan(init_db) / /health
│   ├── core/{config,db}.py           # 設定(.env) / SQLAlchemy(SQLite)
│   ├── models.py                     # ORM（設計§6のサブセット）
│   ├── seed.py                       # テーブル作成＋サンプル6現場投入（冪等）
│   ├── services/
│   │   ├── decision_engine.py        # 判定ルール（設計§8/§9）★中核
│   │   ├── assessment.py             # 気象取得→Reading→判定 のオーケストレーション
│   │   └── data_collectors/open_meteo.py  # Open-Meteo 取得・正規化・WBGT推定
│   │   └── data_collectors/marine.py      # Open-Meteo Marine（波高・周期・うねり）
│   └── api/routes.py                 # エンドポイント（設計§7）
├── tests/                            # 71 tests（engine/collector/api/auth/audit/notifications/jma_warnings/source_probe 等）
├── requirements.txt / pyproject.toml
```

## 起動

```bash
cd backend
# 依存は requirements.txt（多くの環境で導入済み）: pip install -r requirements.txt
# ポートは競合回避のため空きポートを使う例:
PORT=$(python3 -c "import socket;s=socket.socket();s.bind(('127.0.0.1',0));print(s.getsockname()[1]);s.close()")
python3 -m uvicorn app.main:app --host 127.0.0.1 --port "$PORT"
echo "http://127.0.0.1:$PORT/health  /  /docs (Swagger UI)"
```

> 注意: ポート 8000 は他プロジェクトが使用している場合がある。固定せず空きポートを使うこと。
> ダッシュボードは全16現場分のライブ予報を取得するため、外部ネットワーク（api.open-meteo.com）への到達性が必要。
> 取得失敗時も画面は落とさず、欠測として「確認不能」を返す（設計 §15.2）。

## DB / マイグレーション（Alembic, #12 実装済み）

スキーマの正本は **Alembic**（`create_all` は廃止）。`init_db()` が起動時に `alembic upgrade head` を実行してからシードする。**SQLite（開発）・PostgreSQL（本番候補）の双方で同一マイグレーション**が通る（モデルは両対応型のみ使用、env.py は `render_as_batch=True`）。

```bash
cd backend
python3 -m alembic upgrade head        # スキーマ適用
python3 -m alembic revision --autogenerate -m "xxx"  # モデル変更時に差分生成
python3 -m alembic current             # 現在のリビジョン
```

PostgreSQL に切替（本番候補）:
```bash
export DATABASE_URL="postgresql+psycopg2://cw_user:cw_password@localhost:5432/civil_weather_water"
python3 -m alembic upgrade head
```

### Docker Compose（postgres + backend + frontend）
```bash
cp .env.example .env
docker compose up -d            # backend 起動時に alembic upgrade head ＋ seed
# ホスト 5432 が使用中なら: PG_HOST_PORT=15432 docker compose up -d
# ブラウザ: http://localhost:3000/?api=http://localhost:8000
```
検証済み: 同一 Alembic マイグレーションが PostgreSQL16 でテーブル作成→シード（16現場/9ソース）まで成功。

## 主なエンドポイント（設計 §7）

| メソッド | パス | 概要 |
|---|---|---|
| GET | `/health` | ヘルスチェック |
| GET | `/api/sites` /`/api/sites/{id}` /`/api/sites/{id}/stations` | 現場一覧/詳細/観測所 |
| GET | `/api/dashboard/site-risk` | 現場別リスク（ライブ判定） |
| GET | `/api/dashboard/data-sources` | データソース状態 |
| GET | `/api/weather/timeseries?site_id=` | 気象時系列（24h） |
| GET | `/api/marine/national` | 海象データ：全国版（波高・周期・うねり・海上風） |
| POST | `/api/decisions/evaluate` | 作業判断評価（判定エンジン §8.2） |
| GET/POST | `/api/decision-logs` | 判断履歴 取得/記録 |
| GET | `/api/decision-logs/export.csv` | 判断履歴CSV（BOM付） |
| POST | `/api/data-collectors/run` | 手動再取得（キャッシュ更新） |

## テスト

```bash
cd backend && python3 -m pytest      # 71 passed
```

## 設計準拠ポイント

- **断定しない**: レベル0は「主要注意条件なし」。`summary` に「作業可能」を出さない（テストで担保）。
- **不確実性を隠さない**: Open-Meteo 取得失敗・欠測は `weatherStatus`/`data_quality_summary` に明示し、判定は「確認不能」。
- **公式優先**: 河川の洪水情報・気象庁警報は外部予報より高い severity（`flood_warning` 等）。
- **WBGT は derived**: 公式（環境省）未接続のため気温・湿度から推定し `wbgtDerived=true` で明示。

## 認証 / RBAC / 監査ログ / 通知（#25, T3-04 実装済み）

- `core/security.py`: JWT発行・bcryptハッシュ・ログイン試行回数制限・タイミング攻撃対策。
- `core/deps.py`: `require_role()` によるロールベース認可（管理者 / 現場管理者 / 閲覧者）。
- `services/audit.py`: 認証・現場変更・判断記録などの監査ログを永続化。
- `services/notifications.py`: 判定結果（severity≥2 = 中止検討/河川/障害）から通知を導出。画面内通知ベルに加え、Slack/Teams 連携の拡張点あり。
- フロント側の対応は [frontend/README.md](../frontend/README.md) の「ログイン/RBAC」「通知ベル」を参照。

## 次フェーズ（残作業）

- `#20` / `#9` 環境省 WBGT 実データ接続（現状 `estimate_wbgt()` による derived 推定）。
- `#29`〜`#31` 河川観測所マスタの正規化（`site_stations` 多対多）・河川観測取り込み・専用時系列API。
- `#34` / `#35` 閾値設定画面・判定ルール管理API（`/api/admin/rules`）。現状 `decision_engine.py` 内の `TH` 辞書にハードコード。
- `#33` データ品質サービス（異常値・重複検知の専用化。欠測処理は実装済み）。
- `#38` バッチジョブ拡張（設計書 JOB-001〜006 のうち未実装分）・リトライ/バックオフ。
- `#39` データソース監視強化（連続失敗閾値判定・代替案内ロジック）。

## 定期バッチ / データソース実プローブ（#47 実装済み）

`app/scheduler.py`（APScheduler `AsyncIOScheduler`）が lifespan で起動:

| ジョブ | 間隔 | 内容 |
|---|---|---|
| `probe_sources` | **300秒（5分, `PROBE_INTERVAL_SECONDS`）** | 各ソースへ実HTTP疎通し status/last_ok/fails/avg_ms を更新 |
| `refresh_forecasts` | 300秒（5分, `FORECAST_REFRESH_SECONDS`） | Open-Meteo 予報キャッシュをウォーム |

> データソース状態は **5分ごとに自動更新**（フロントの「データソース」画面にも明記）。サンプル現場は全国16件（札幌〜那覇）。

- `app/services/data_collectors/source_probe.py` が実プローブ。OK / Warning(遅延・3xx/4xx) / Error(5xx・接続失敗) を判定。
- プローブ対象（公開・認証不要, 9件）: Open-Meteo / Open-Meteo Marine / 気象庁XML / 気象庁CSV(アメダス) / WBGT / 川の防災情報 / NASA POWER / JAXA G-Portal / NOAA。

## 海象データ：全国版・海上作業判定（#72 段5）

- `data_collectors/marine.py`: Open-Meteo Marine API（波高・周期・波向・うねり・海水温）を
  取得・正規化（物理範囲チェック・MISSING/OUTLIER フラグ）。
- `GET /api/marine/national`: 全アクセス可能現場の海象サマリ＋波高リスクレベル。
- 判定エンジンに `marine`（海上作業）を追加: 有義波高（注意1.0m / 中止検討2.0m）・うねり・
  海上風・突風・濃霧（気象コード45/48）の閾値判定。視程は実測未接続のため代理判定であることを明示。
- 潮位（気象庁）・NOWPHAS は利用条件確認前のため未接続（画面・カタログに明示）。

## AI設定（DeepSeek 既定）

- `PUT /api/admin/settings` で `ai_provider`（`deepseek` / `anthropic`）を保存・監査。
- `POST /api/admin/settings/ai/test` は既定 DeepSeek の `/models` へ Bearer 認証で疎通。
  `provider=anthropic` 指定時は従来どおり `x-api-key` で Anthropic へ疎通。

## 気象庁 防災情報XML 警報の実反映（#26）

`services/data_collectors/jma_warnings.py` が気象庁の防災情報XML（atomフィード→個別警報XML）を取得・パース（`defusedxml`でXXE対策）し、市町村エリア別の発表中警報を集約。`assess` が現場所在地と突き合わせ、**洪水警報/注意報→河川 sev2、大雨警報→河川/土工/打設/舗装 sev2** に引き上げる（公式優先 §8.3-6）。10分キャッシュ・取得失敗時は無警報に縮退。`ENABLE_JMA_WARNINGS=false` で無効化。
河川の実水位は無認証APIが無いため公式「川の防災情報」リンク参照に留める（水防災オープンデータは契約制）。
- データソースは計9件（設計§4.1 準拠。+ DS-JMA-CSV / DS-JAXA / DS-NOAA を追加）。
- **水防災オープンデータ（契約制）は対象外**＝シードの Error/未接続を保持（実態以上に良く見せない）。
- 手動再取得 `POST /api/data-collectors/run` も同期で全ソースをプローブする。
- テストでは `ENABLE_SCHEDULER=false`（ネット非依存）。

## 現場の登録/更新/無効化（#14 実装済み）

| メソッド | パス | 概要 |
|---|---|---|
| GET | `/api/work-types` | 作業種別マスタ（登録フォーム用） |
| POST | `/api/sites` | 現場登録（id自動採番・バリデーション） |
| PUT | `/api/sites/{id}` | 現場更新（部分） |
| DELETE | `/api/sites/{id}` | 現場無効化（status=inactive、ダッシュボードから除外） |

## 作業予定 API（#16 T1-05・FR-011〜014 実装済み）

`WorkPlan` モデル（現場ごとの作業予定）に対する CRUD 相当（物理削除エンドポイントはなく、取消は `status=cancelled` への更新で対応）＋ 評価実行。詳細設計 §7 準拠。

| メソッド | パス | 概要 |
|---|---|---|
| GET | `/api/work-plans` | 作業予定一覧（`site_id` / `date`（YYYY-MM-DD, 日別表示 FR-014）で絞込可） |
| GET | `/api/work-plans/{id}` | 作業予定詳細 |
| POST | `/api/work-plans` | 作業予定登録（id自動採番 `WP##`。site存在チェック・時刻整合・XSS入力境界チェック） |
| PUT | `/api/work-plans/{id}` | 作業予定更新（部分。status は `planned/done/postponed/cancelled`） |
| POST | `/api/work-plans/{id}/evaluate` | 作業予定に紐づく気象・河川リスクの自動評価（`/api/decisions/evaluate` と同じ判定エンジン経路。結果は `DecisionResult` として永続化） |

登録・更新は `admin` / `tech_manager` / `site_manager` ロールのみ許可（`viewer` は403）。
