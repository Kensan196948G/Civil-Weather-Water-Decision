# frontend — WebUI（ClaudeDesign 取り込み）

本ディレクトリは、ClaudeDesign で作成した WebUI プロトタイプをリポジトリに取り込んだものです。
現状は**モックデータで動作する完全な6画面 SPA** で、次フェーズで実 API に接続します。

```
frontend/
├── design/                          # ClaudeDesign 出力（再取り込みの対象）
│   ├── 気象河川施工判断支援.dc.html   # 本体（無改修。<x-dc>テンプレ ＋ <script data-dc-script>）
│   ├── support.js                   # dc-runtime（React/ReactDOM を CDN 自動ロードしマウント）
│   ├── data-adapter.js              # ★実APIへ接続する外部アダプタ（.dc.html を触らず prototype をラップ）
│   └── index.html                   # ローダ（.dc.html を取得しアダプタ＋API設定を注入）
├── test/
│   ├── logic-smoke.mjs              # ロジック層スモークテスト（DOM不要）
│   └── adapter-contract.cjs         # アダプタ契約テスト（patch後 renderVals が API由来か検証）
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

## データ接続（data-adapter 方式・実装済み）

`.dc.html` を**1バイトも変更せず**実APIへ接続する。仕組み:

1. `index.html`（ローダ）が `.dc.html` を fetch し、`<head>` に API ベース設定、`</body>` 直前に `data-adapter.js` を注入して展開。
2. `data-adapter.js` が dc-runtime 生成の Component(`window.__dcRegistry[root].Logic`) の prototype を**ラップ**:
   - `renderVals` ラップ — 呼出前に `this.SITES`/`this.state.history` を API データへ差替え、呼出後に `vals.sources` を上書き。
   - `genHourly`/`resultVM`/`evaluate`/`record`/`refresh` をメソッド差替えで API 化。
   - 再描画は `window.__dcSetProps()`（= `registry.bump()`）で発火。
3. ClaudeDesign 再取り込み（`.dc.html` 上書き）でも `data-adapter.js`/`index.html` は残るため配線は維持される。

### データ付きで起動

```bash
# 1) バックエンド（空きポート）
cd backend && BPORT=$(python3 -c "import socket;s=socket.socket();s.bind(('0.0.0.0',0));print(s.getsockname()[1]);s.close()") \
  && python3 -m uvicorn app.main:app --host 0.0.0.0 --port "$BPORT"
# 2) フロント（別ターミナル, 空きポート）
cd frontend/design && python3 -m http.server 0   # 割当ポートは起動ログ参照
# 3) ブラウザで:  http://<host>:<frontport>/?api=http://<host>:<BPORT>
#    ?api= は localStorage に保存され、次回以降は省略可。バックエンド未起動ならモック表示にフォールバック。
```

> `vals` の形は変えていないため、テンプレート（画面）は無改修。`?api=` を付けなければ素のモック表示。

### アダプタが追加する機能（.dc.html 無改修）

- **「現場登録」正式画面**（ヘッダーナビに統合）: `afterRender` フックでナビに「現場登録」項目を追加し、`state.screen==='register'` のとき注入した全面パネルを表示。`POST /api/sites` で登録→ダッシュボードへ自動遷移。作業種別は `/api/work-types`。河川近接チェックで河川状態欄を表示。
- **5分ごとの定期自動更新**: ダッシュボード/データソースを再取得（サーバ側は APScheduler でも実施 → backend/README）。
- **「現場詳細」はドリルダウン専用**: ナビタブから除外（`hideNav:["現場詳細"]`）。詳細はダッシュボードの現場カード/地図ピンのクリックで特定の1現場のみ表示。"全現場の詳細" は存在せず一覧はダッシュボードが担う。

> **ネイティブ ClaudeDesign 画面化への移行**: 現状の登録画面はアダプタ注入。正式に ClaudeDesign の画面にしたい場合は、ClaudeDesign で「現場登録」画面（フォーム）を1枚追加し `POST /api/sites`（body は上記payload）を呼ぶだけ。API は完成済みなので、デザイン側にフォームができたらアダプタの注入版は外せる。

### 旧計画（対応表・参考）

`<script data-dc-script>` のハードコード値を API（詳細設計 §7）へ差し替える対応。data-adapter.js が下表を実装済み。

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

## テスト（DOM不要 / Node）

```bash
node frontend/test/logic-smoke.mjs       # 6画面VM・判定(6作業種別)・グラフ・WBGT境界（21件）
node frontend/test/adapter-contract.cjs  # アダプタ: patch後 renderVals が API由来か（9件）
```

> 本環境では Chrome がヘッドレス起動不可のため、実ブラウザ描画は各自で確認すること。
> 上記2テストで「ロジック」と「API配線契約」を担保する。
