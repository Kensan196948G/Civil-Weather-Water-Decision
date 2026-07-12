# backend — FastAPI バックエンド（PoC）

気象（Open-Meteo ライブ）→ 正規化・品質フラグ → 判定エンジン → API、を提供する。
WebUI（`frontend/`）のデータ接続先（`#46` data-adapter 経由で実装済み）。開発は **SQLite**（DBサーバ不要）、本番候補は **PostgreSQL**（Alembic、#12）。

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
│   ├── core/{config,db,security,deps}.py  # 設定(.env) / SQLAlchemy / JWT・bcrypt / RBAC依存
│   ├── models.py                     # ORM（users/audit_logs含む10テーブル）
│   ├── seed.py                       # テーブル作成＋16現場・9データソース・5ロールのデモユーザー投入（冪等）
│   ├── scheduler.py                  # APScheduler（定期プローブ・予報リフレッシュ）
│   ├── services/
│   │   ├── decision_engine.py        # 判定ルール（設計§8/§9）★中核
│   │   ├── assessment.py             # 気象取得→Reading→判定 のオーケストレーション
│   │   ├── audit.py                  # 監査ログ記録
│   │   ├── notifications.py          # 通知導出（画面内 + Slack/Teams拡張点）
│   │   └── data_collectors/{open_meteo,jma_warnings,source_probe}.py
│   └── api/{routes.py,auth.py}       # エンドポイント（設計§7）/ 認証
├── migrations/                       # Alembic（3リビジョン。#12参照）
├── tests/                            # 54 tests（api17 / auth10 / engine13 / jma_warnings3 / notifications1 / open_meteo5 / source_probe5）
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

ルータ全体（`/api/*`、認証エンドポイント除く）は既定で認証必須（`Depends(get_current_user)`）。`ENABLE_AUTH=false`（ローカル開発）でバイパス可。

| メソッド | パス | 概要 | 認可 |
|---|---|---|---|
| GET | `/health` | ヘルスチェック | 不要 |
| POST | `/api/auth/login` | ログイン（JWT発行、5回/300秒でロック） | 不要 |
| GET | `/api/auth/me` | ログイン中ユーザー情報 | 認証済み全員 |
| GET | `/api/sites` /`/api/sites/{id}` /`/api/sites/{id}/stations` | 現場一覧/詳細（ライブ判定・作業予定込み）/観測所 | 認証済み全員 |
| POST/PUT/DELETE | `/api/sites`（登録）/`/api/sites/{id}`（更新/無効化） | 現場CRUD（#14） | `admin`,`tech_manager` |
| GET | `/api/work-types` | 作業種別マスタ | 認証済み全員 |
| GET | `/api/dashboard/site-risk` | 現場別リスク（ライブ判定） | 認証済み全員 |
| GET | `/api/dashboard/data-sources` | データソース状態 | 認証済み全員 |
| GET | `/api/weather/timeseries?site_id=` | 気象時系列（24h） | 認証済み全員 |
| POST | `/api/decisions/evaluate` | 作業判断評価＋`decision_results`/`decision_reasons`永続化（#23） | 認証済み全員 |
| GET | `/api/decision-results/{id}` | 判定結果詳細取得（#23） | 認証済み全員 |
| GET/POST | `/api/decision-logs` | 判断履歴 取得/記録 | POSTは`admin`,`tech_manager`,`site_manager`,`safety` |
| GET | `/api/decision-logs/export.csv` | 判断履歴CSV（BOM付） | 認証済み全員 |
| POST | `/api/data-collectors/run` | 手動再取得（全ソース同期プローブ） | 認証済み全員 |
| GET | `/api/notifications` | 通知一覧（判定結果から導出） | 認証済み全員 |
| GET | `/api/admin/audit-logs` | 監査ログ一覧 | `admin`,`tech_manager` |

## テスト

```bash
cd backend && python3 -m pytest      # 54 passed
```

## 設計準拠ポイント

- **断定しない**: レベル0は「主要注意条件なし」。`summary` に「作業可能」を出さない（テストで担保）。
- **不確実性を隠さない**: Open-Meteo 取得失敗・欠測は `weatherStatus`/`data_quality_summary` に明示し、判定は「確認不能」。
- **公式優先**: 河川の洪水情報・気象庁警報は外部予報より高い severity（`flood_warning` 等）。
- **WBGT は derived**: 公式（環境省）未接続のため気温・湿度から推定し `wbgtDerived=true` で明示。

## 認証 / RBAC / 監査ログ / 通知（#25, T3-04 実装済み）

- `core/security.py`: JWT発行・bcryptハッシュ・ログイン試行回数制限・タイミング攻撃対策（ダミーハッシュ比較）。
- `core/deps.py`: `require_role()` によるロールベース認可（`admin` / `tech_manager` / `site_manager` / `safety` / `viewer` の5ロール）。
- `services/audit.py`: 認証・現場変更・判断記録などの監査ログを永続化（`GET /api/admin/audit-logs`）。
- `services/notifications.py`: 判定結果（severity≥2 = 中止検討/河川/障害）から通知を導出。画面内通知に加え、Slack/Teams Webhook連携の拡張点あり（未設定時はログのみ）。
- `core/config.py: _guard_production()`: `APP_ENV!=local` 時に `ENABLE_AUTH=false`・既定JWT_SECRET・32byte未満のsecretを起動時 `RuntimeError` で拒否（本番誤設定の防止）。

## 次フェーズ（残作業）

- `#20` / `#9` 環境省 WBGT 実データ接続（現状 `estimate_wbgt()` による derived 推定。実測CSVは地点コード・年月をフォーム送信で指定する動的仕様のため未接続）。
- `#29`〜`#31` 河川観測所マスタの正規化（`site_stations` 多対多）・河川観測取り込み・専用時系列API。
- `#34` / `#35` 閾値設定画面・判定ルール管理API（`/api/admin/rules`）。現状 `decision_engine.py` 内の `RULES` にハードコード。
- `#33` データ品質サービス（異常値・重複検知の専用化。欠測処理は実装済み）。
- `#36`〜`#39` ダッシュボード強化・気象時系列グラフ・バッチ拡張・データソース監視強化。

## 定期バッチ / データソース実プローブ（#47 実装済み）

`app/scheduler.py`（APScheduler `AsyncIOScheduler`）が lifespan で起動:

| ジョブ | 間隔 | 内容 |
|---|---|---|
| `probe_sources` | **300秒（5分, `PROBE_INTERVAL_SECONDS`）** | 各ソースへ実HTTP疎通し status/last_ok/fails/avg_ms を更新 |
| `refresh_forecasts` | 300秒（5分, `FORECAST_REFRESH_SECONDS`） | Open-Meteo 予報キャッシュをウォーム |

> データソース状態は **5分ごとに自動更新**（フロントの「データソース」画面にも明記）。サンプル現場は全国16件（札幌〜那覇）。

- `app/services/data_collectors/source_probe.py` が実プローブ。OK / Warning(遅延・3xx/4xx) / Error(5xx・接続失敗) を判定。
- プローブ対象（公開・認証不要, 8件）: Open-Meteo / 気象庁XML / 気象庁CSV(アメダス) / WBGT / 川の防災情報 / NASA POWER / JAXA G-Portal / NOAA。

## 気象庁 防災情報XML 警報の実反映（#26）

`services/data_collectors/jma_warnings.py` が気象庁の防災情報XML（atomフィード→個別警報XML）を取得・パース（`defusedxml`でXXE対策）し、市町村エリア別の発表中警報を集約。`assess` が現場所在地と突き合わせ、**洪水警報/注意報→河川 sev2、大雨警報→河川/土工/打設/舗装 sev2** に引き上げる（公式優先 §8.3-6）。10分キャッシュ・取得失敗時は無警報に縮退。`ENABLE_JMA_WARNINGS=false` で無効化。
河川の実水位は無認証APIが無いため公式「川の防災情報」リンク参照に留める（水防災オープンデータは契約制）。
- データソースは計9件（設計§4.1 準拠。+ DS-JMA-CSV / DS-JAXA / DS-NOAA を追加）。
- **水防災オープンデータ（契約制）は対象外**＝シードの Error/未接続を保持（実態以上に良く見せない）。
- 手動再取得 `POST /api/data-collectors/run` も同期で全ソースをプローブする。
- テストでは `ENABLE_SCHEDULER=false`（ネット非依存）。
