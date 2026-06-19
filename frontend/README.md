# frontend — WebUI（ClaudeDesign 取り込み）

本ディレクトリは、ClaudeDesign で作成した WebUI プロトタイプをリポジトリに取り込んだものです。
現状は**モックデータで動作する完全な6画面 SPA** で、次フェーズで実 API に接続します。

```
frontend/
├── design/                          # ClaudeDesign 出力（再取り込みの対象。原則ここを直接編集しない）
│   ├── 気象河川施工判断支援.dc.html   # 本体（<x-dc>テンプレート ＋ <script data-dc-script> ロジック）
│   ├── support.js                   # dc-runtime（React/ReactDOM を CDN から自動ロードしマウント）
│   └── index.html                   # エントリ（.dc.html へ自動遷移）
├── test/
│   └── logic-smoke.mjs              # ロジック層スモークテスト（DOM 不要 / Node 実行）
└── README.md
```

## アーキテクチャ

`.dc.html` は素の HTML ではなく、**dc-runtime（`support.js`）上で動く React アプリ**です。

- `support.js` が React 18.3.1 / ReactDOM を unpkg から **SRI 付きで自動ロード**し、`<x-dc>` テンプレートと `<script data-dc-script>` の `class Component extends DCLogic` をパースして `ReactDOM.createRoot().render()` でマウントする。
- 画面状態は `state.screen`（`dashboard` / `site` / `decision` / `wbgt` / `history` / `source` の6画面）で切替。
- `renderVals()` が画面ごとのビューモデル（`vals`）を生成し、テンプレートにバインドする。**DOM/Leaflet に触れない純粋計算**なので、ロジックだけ Node で単体検証できる（`test/logic-smoke.mjs`）。
- 地図は Leaflet（OpenStreetMap タイル）、グラフは JS で生成する SVG。

> CDN 依存: React/ReactDOM/Babel（unpkg）、Leaflet（unpkg）、Noto Sans JP（Google Fonts）、OSM タイル。
> 完全オフライン運用時はこれらを self-host する必要がある（将来課題）。

## 起動方法（ローカル）

`.dc.html` は `./support.js` を相対参照するため、**同一ディレクトリを HTTP 配信**して開く（`file://` は不可）。

```bash
# 任意の静的サーバでよい。例:
cd frontend/design
python3 -m http.server 5173
# → ブラウザで http://localhost:5173/ を開く
```

> 注意: 本リポジトリの CI 実行環境（コンテナ）では Chrome がヘッドレス起動できず（SIGTRAP / core dump）、
> 自動スクリーンショット検証は不可。**実描画は各自のブラウザで確認**すること。
> ロジックの健全性は `node frontend/test/logic-smoke.mjs`（DOM 不要）で担保する。

## ClaudeDesign からの再取り込み

UI を ClaudeDesign 側で更新したら、DesignSync コネクタで `design/` 配下を上書きする。

- プロジェクト: `Civil-Weather-Water-Decision`（projectId: `4561f893-fd3f-4d53-aca0-6fc13aacbef6`）
- 対象ファイル: `気象河川施工判断支援.dc.html` / `support.js`
- ファイル名は ClaudeDesign 側と一致させること（差分追跡のため）。
- 取り込み後は `node frontend/test/logic-smoke.mjs` で回帰確認する。

## データ接続計画（次フェーズ）

現状の `<script data-dc-script>` 内のハードコード値・合成データを、バックエンド API（詳細設計 §7）に差し替える。
**`vals` の形は変えず**、データ取得元だけ `fetch` 化し `setState` に流す方針。これでテンプレート（画面）は無改修で接続できる。

| 現状（モック） | 役割 | 接続先 API（詳細設計 §7） |
|---|---|---|
| `SITES`（ダッシュボード一覧） | 現場別リスク一覧 | `GET /api/dashboard/site-risk` |
| `SITES`（現場詳細の1件） | 現場詳細 | `GET /api/sites/{id}` |
| `genHourly()` 気温/雨量/風速 | 気象時系列グラフ | `GET /api/weather/timeseries` |
| `genHourly()` 水位 + 閾値 | 河川時系列グラフ | `GET /api/river/timeseries` |
| `genHourly()` WBGT | WBGT時系列・ランキング | `GET /api/wbgt/timeseries` |
| `STATIONS` / `COORDS` | 観測所マーカー・上流線 | `GET /api/sites/{id}/stations`（site_stations） |
| `resultVM()` 作業種別別の判定 | 作業判断の評価結果 | `POST /api/work-plans/{id}/evaluate`（判定エンジン §8） |
| `evaluate()` | 評価実行 | 同上 |
| `record()` | 判断メモ記録 | `POST /api/decision-logs` |
| `state.history` | 判断履歴 | `GET /api/decision-logs` |
| `exportCsv()` | CSV出力 | `GET /api/decision-logs/export.csv` |
| `sources`（データソース状態） | データソース状態画面 | `GET /api/dashboard/data-sources` |
| `refresh()` | 手動再取得 | `POST /api/data-collectors/run` |
| `links` | 公式リンク | 静的（気象庁/川の防災情報/環境省WBGT/Open-Meteo） |

### 接続時の設計原則（要件・詳細設計より厳守）

- **断定しない**: レベル0は「主要注意条件なし」。「作業可能」と表示しない。
- **不確実性を隠さない**: 欠測=`確認不能(3)`、取得時刻・データ元・更新時刻を必ず表示（既に UI に箇所あり）。
- **公式優先**: 気象庁の警報・注意報を Open-Meteo 等の外部予報より優先。
- 詳細設計 §8.2 の判定エンジン出力 JSON（`overall_level` / `reasons[]` / `data_quality_summary`）を `resultVM()` の形に合わせて返すと差し替えが最小になる。

## スモークテスト

```bash
node frontend/test/logic-smoke.mjs
# 6画面のビューモデル生成・判定エンジン(6作業種別)・グラフ生成・WBGT境界を検証（DOM不要）
```
