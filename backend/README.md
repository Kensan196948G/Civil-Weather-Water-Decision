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
│   └── api/routes.py                 # エンドポイント（設計§7）
├── tests/                            # 26 tests（engine12 / collector5 / api9）
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
> ダッシュボードは6現場分のライブ予報を取得するため、外部ネットワーク（api.open-meteo.com）への到達性が必要。
> 取得失敗時も画面は落とさず、欠測として「確認不能」を返す（設計 §15.2）。

## 主なエンドポイント（設計 §7）

| メソッド | パス | 概要 |
|---|---|---|
| GET | `/health` | ヘルスチェック |
| GET | `/api/sites` /`/api/sites/{id}` /`/api/sites/{id}/stations` | 現場一覧/詳細/観測所 |
| GET | `/api/dashboard/site-risk` | 現場別リスク（ライブ判定） |
| GET | `/api/dashboard/data-sources` | データソース状態 |
| GET | `/api/weather/timeseries?site_id=` | 気象時系列（24h） |
| POST | `/api/decisions/evaluate` | 作業判断評価（判定エンジン §8.2） |
| GET/POST | `/api/decision-logs` | 判断履歴 取得/記録 |
| GET | `/api/decision-logs/export.csv` | 判断履歴CSV（BOM付） |
| POST | `/api/data-collectors/run` | 手動再取得（キャッシュ更新） |

## テスト

```bash
cd backend && python3 -m pytest      # 26 passed
```

## 設計準拠ポイント

- **断定しない**: レベル0は「主要注意条件なし」。`summary` に「作業可能」を出さない（テストで担保）。
- **不確実性を隠さない**: Open-Meteo 取得失敗・欠測は `weatherStatus`/`data_quality_summary` に明示し、判定は「確認不能」。
- **公式優先**: 河川の洪水情報・気象庁警報は外部予報より高い severity（`flood_warning` 等）。
- **WBGT は derived**: 公式（環境省）未接続のため気温・湿度から推定し `wbgtDerived=true` で明示。

## 次フェーズ（残作業）

- `#46` WebUI のモックを本API へ接続（`frontend/README.md` 対応表）。CORS は許可済み。
- `#12` Alembic マイグレーション化（現状は `create_all`）。
- `#20` 環境省 WBGT 実データ接続（現状 derived 推定）。
- `#23` `decision_results`/`decision_reasons` の永続化（現状は都度計算）。
- 河川（川の防災情報）・気象庁防災XML の連携（Phase 2 / #29〜#33）。

## 定期バッチ / データソース実プローブ（#47 実装済み）

`app/scheduler.py`（APScheduler `AsyncIOScheduler`）が lifespan で起動:

| ジョブ | 間隔 | 内容 |
|---|---|---|
| `probe_sources` | 120秒（`PROBE_INTERVAL_SECONDS`） | 各ソースへ実HTTP疎通し status/last_ok/fails/avg_ms を更新 |
| `refresh_forecasts` | 600秒（`FORECAST_REFRESH_SECONDS`） | Open-Meteo 予報キャッシュをウォーム |

- `app/services/data_collectors/source_probe.py` が実プローブ。OK / Warning(遅延・3xx/4xx) / Error(5xx・接続失敗) を判定。
- プローブ対象（公開・認証不要）: Open-Meteo / 気象庁XML / WBGT / 川の防災情報 / NASA POWER。
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
