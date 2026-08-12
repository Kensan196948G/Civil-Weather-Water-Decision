# frontend — WebUI（ClaudeDesign 取り込み）

本ディレクトリは、ClaudeDesign で作成した WebUI プロトタイプをリポジトリに取り込んだものです。
現状は**モックデータで動作する完全な6画面 SPA** で、次フェーズで実 API に接続します。

```
frontend/
├── design/                          # ClaudeDesign 出力（再取り込みの対象）
│   ├── 気象河川施工判断支援.dc.html   # 本体（無改修。<x-dc>テンプレ ＋ <script data-dc-script>）
│   ├── support.js                   # dc-runtime（React/ReactDOM は vendor/ からロードしマウント）
│   ├── data-adapter.js              # ★実APIへ接続する外部アダプタ（.dc.html を触らず prototype をラップ）
│   └── index.html                   # ローダ（.dc.html を取得しアダプタ＋API設定を注入）
├── test/
│   ├── logic-smoke.mjs              # ロジック層スモークテスト（DOM不要）
│   ├── adapter-contract.cjs         # アダプタ契約テスト（patch後 renderVals が API由来か検証）
│   ├── api-config-policy.cjs        # ?api= の公開/開発オリジン制限
│   ├── serve-proxy-policy.py        # serve.py の同一オリジン /api proxy 契約
│   ├── cdn-policy.cjs               # JS/CSS/font CDN の混入検出
│   ├── vendor-assets-policy.cjs     # vendor参照・Leaflet画像・SRI・ライセンス同梱検査
│   └── e2e_smoke.py                 # Playwright: local起動→ログイン→表示→ログアウト
└── README.md
```

## アーキテクチャ

`.dc.html` は素の HTML ではなく、**dc-runtime（`support.js`）上で動く React アプリ**です。

- `support.js` が `vendor/` 配下の React 18.3.1 / ReactDOM / Babel standalone をロードし、`<x-dc>` テンプレートと `<script data-dc-script>` の `class Component extends DCLogic` をパースして `ReactDOM.createRoot().render()` でマウントする。
- 画面状態は `state.screen`（`dashboard` / `site` / `decision` / `wbgt` / `history` / `source` の6画面）で切替。
- `renderVals()` が画面ごとのビューモデル（`vals`）を生成し、テンプレートにバインドする。**DOM/Leaflet に触れない純粋計算**なので、ロジックだけ Node で単体検証できる（`test/logic-smoke.mjs`）。
- 地図は self-host した Leaflet（既定はタイルなし）、グラフは JS で生成する SVG。

> JS/CSS/font CDN 依存は `frontend/design/vendor/` へ固定版をself-host済み。
> タイルURLは起動前の `window.__CW_TILE_URL__` または `serve.py` の `CW_TILE_URL` で指定する。
> 未指定時は国土地理院 標準地図（APIキー不要）を既定表示し、マップ非表示（背景タイル欠落）を防ぐ。
> `CW_TILE_URL=none` / `off` / `disabled` を明示した場合のみタイルなし（グリッド背景）で表示する。
> タイルの帰属表示は `CW_TILE_ATTRIBUTION`（既定: 国土地理院）で設定する。

## 起動方法（ローカル）

**`frontend/serve.py`（推奨）** を使う。自動IP＋空きポートで配信し、`.dc.html` をディスク上は無改修のまま
HTTP応答時に data-adapter.js / API設定を**サーバ側注入**する（`document.write` を使わないので、
Leaflet 等の script が parser-blocking 警告なしで読み込まれる）。

```bash
python3 frontend/serve.py
# 起動ログの URL を開く。backend proxy を使う場合:
#   CW_BACKEND_PROXY_BASE=http://127.0.0.1:55019 python3 frontend/serve.py
```

`serve.py` は `PORT` と `HOST` を環境変数で指定できる。本番 systemd は `HOST=127.0.0.1` で
Cloudflare Tunnel からのみ到達させる。LAN 直アクセス検証が必要な開発時だけ `HOST=0.0.0.0` を使う。
HTML/JS/CSS/404 等の全レスポンスに基本HTTPセキュリティヘッダ（nosniff / frame deny / no-referrer /
permissions policy / HSTS / COOP / CORP / legacy download hardening）を付与する。HTML と JS は更新反映を
優先して `Cache-Control: no-store` で配信する。
`Content-Security-Policy-Report-Only` も付与するが、現状は `.dc.html` の inline script/style と
`support.js` の runtime Babel / `new Function` があるため enforce はしない。CSP enforce は
ClaudeDesign 出力の precompile 化・inline style 削減後に切り替える。local E2E / 開発の `?api=` を保つため
report-only の `connect-src` は `127.0.0.1` / `localhost` / `[::1]` を許可する。

> 素の `python3 -m http.server`（`frontend/design` で）も可だが、その場合 `index.html` が
> `document.write` でアダプタを注入するため Chrome の parser-blocking 警告が出る。常用は `serve.py` 推奨。

> 注意: このホストでは Chromium/Chrome の headless が SIGTRAP で落ちるため、E2E smoke は
> Playwright Firefox を既定にする。CI でも local backend/frontend と Open-Meteo スタブを起動し、
> 本番SecretsやCloudflare Accessには依存しない。

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
# 2) フロント（別ターミナル, serve.py が自動IP＋空きポートで配信）
CW_BACKEND_PROXY_BASE="http://127.0.0.1:$BPORT" python3 frontend/serve.py
# 3) ブラウザで起動ログの LOOPBACK URL を開く（同一オリジン /api proxy）
```

`?api=` は公開ホストでは同一オリジン以外を無視する。localhost / 127.0.0.1 / プライベートIP で開いた
開発時のみ、同じ開発ネットワーク上の API オリジンを保存する。production loopback 確認では
`?api=` を使わず、`CW_BACKEND_PROXY_BASE` による同一オリジン proxy を使う。

> `vals` の形は変えていないため、テンプレート（画面）は無改修。`?api=` も proxy も無ければ素のモック表示。

### アダプタが追加する機能（.dc.html 無改修）

- **「現場登録」正式画面**（ヘッダーナビに統合）: `afterRender` フックでナビに「現場登録」項目を追加し、`state.screen==='register'` のとき注入した全面パネルを表示。`POST /api/sites` で登録→ダッシュボードへ自動遷移。作業種別は `/api/work-types`。河川近接チェックで河川状態欄を表示。
- **5分ごとの定期自動更新**: ダッシュボード/データソースを再取得（サーバ側は APScheduler でも実施 → backend/README）。
- **「現場詳細」はドリルダウン専用**: ナビタブから除外（`hideNav:["現場詳細"]`）。詳細はダッシュボードの現場カード/地図ピンのクリックで特定の1現場のみ表示。"全現場の詳細" は存在せず一覧はダッシュボードが担う。
- **全国マップ対応**: API の緯度経度から `this.COORDS` を再構築し、ダッシュボード地図に全16現場（札幌〜那覇）を表示。`fitBounds` で全国にズーム。データ更新時は地図キャッシュを破棄して再描画。
- **熱中症/WBGT 画面に全国地図**: Leaflet 地図で現場を WBGT リスク色（ほぼ安全〜危険）のピンで表示。スケール凡例＋現場別ランキング併設（注入パネル。`.dc.html` 無改修）。タイル背景は `CW_TILE_URL` 指定時のみ表示。
- **海象データ：全国版（#72 段5）**: Open-Meteo Marine API の波高・周期・波向・うねりを全登録現場で表示。全国地図（波高リスク色）＋一覧表。潮位・NOWPHAS は未接続を明示し公式リンクを案内。
- **海上作業判定（#72 段5 連動）**: 有義波高・うねり・海上風・突風・濃霧の閾値判定。現場選択→評価→判定理由と地図を表示。
- **地図表示の安定化**: Leaflet を vendor から確実に読み込み（helmet注入失敗時のフォールバック）、未登録作業種別でも地図構築を継続、タイル未設定時はグリッド背景で地図領域を可視化。
- **AI設定は DeepSeek 既定**: プロバイダ選択（DeepSeek / Anthropic）と疎通テストに対応。APIキーは暗号化保存・末尾4桁のみ表示。
- **データソース画面に更新間隔を明記**: 「5分ごとに自動更新」の注記バーを画面下部に表示。
- **ログイン/RBAC**: 未認証時はログイン画面を表示。全 fetch に `Authorization: Bearer` を付与し、401 でローカルトークンを削除。右上ログアウトは `POST /api/auth/logout` でサーバ側JWT失効台帳へ登録してからローカルトークンを削除。デモ: `admin/admin123`（管理者）, `yamada/pass1234`（現場管理者）, `viewer/pass1234`（閲覧。現場登録は403）。
- **通知ベル**: 右上の🔔に要対応件数（中止検討/河川/障害＝severity≥2）のバッジ。クリックで通知一覧（`GET /api/notifications` を判定結果から導出）。5分ごと更新。Slack/Teams 外部送信は管理画面の通知フラグとサーバ側 `SLACK_WEBHOOK_URL` / `TEAMS_WEBHOOK_URL` の両方が有効な場合のみ行う。
- **運用状態画面**: 管理メニューの「運用状態」（admin/技術管理者限定）が `opsStatusSnapshot()` で `GET /api/admin/ops/status-snapshot` を認証付き取得。allowlist 済み systemd service / timer / failed unit snapshot を表形式で表示し、再読込できる。
- **現場別 公式リンク（#85）**: 現場詳細の「公式情報の確認リンク」を `GET /api/sites/{id}/links`
  （`site_links` マスタ）から表示。未登録の現場は従来の静的公式リンク（気象庁/川の防災情報/
  環境省WBGT/Open-Meteo）へフォールバックして導線を維持する。
- **Open-Meteo 帰属表示（CC BY 4.0）**: 画面右下へ「気象・海象データの一部: Open-Meteo
  (CC BY 4.0)」を固定表示（利用条件確認書 #114 のギャップ解消）。

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
| `links` | 公式リンク | `GET /api/sites/{id}/links`（#85 実装済み。未登録は静的公式リンクへフォールバック） |

### 接続時の設計原則（要件・詳細設計より厳守）

- **断定しない**: レベル0は「主要注意条件なし」。「作業可能」と表示しない。
- **不確実性を隠さない**: 欠測=`確認不能(3)`、取得時刻・データ元・更新時刻を必ず表示（既に UI に箇所あり）。
- **公式優先**: 気象庁の警報・注意報を Open-Meteo 等の外部予報より優先。
- 詳細設計 §8.2 の判定エンジン出力 JSON（`overall_level` / `reasons[]` / `data_quality_summary`）を `resultVM()` の形に合わせて返すと差し替えが最小になる。

## テスト（DOM不要 / Node）

```bash
node frontend/test/logic-smoke.mjs       # 6画面VM・判定(6作業種別)・グラフ・WBGT境界（21件）
node frontend/test/adapter-contract.cjs  # アダプタ: patch後 renderVals が API由来か（29件）
node frontend/test/api-config-policy.cjs # ?api= の公開/開発オリジン制限（3件）
python3 frontend/test/serve-proxy-policy.py # serve.py: /api proxy 固定origin/GET/POST/502契約
node frontend/test/cdn-policy.cjs        # JS/CSS/font CDN の混入検出
node frontend/test/vendor-assets-policy.cjs # vendor参照・Leaflet画像・SRI・ライセンス同梱
python -m pip install playwright         # 初回のみ
python -m playwright install firefox     # 初回のみ
python frontend/test/e2e_smoke.py        # E2E: local起動→ログイン→運用状態表示→ログアウト
```

> `e2e_smoke.py` は `APP_ENV=local` の一時SQLite DBとOpen-Meteoスタブを使う。
> E2E中は `tile.openstreetmap.org` をブロックし、外部タイル通信に依存しない起動を検証する。
> 失敗調査時は `E2E_DEBUG=1 python frontend/test/e2e_smoke.py` でブラウザ/プロセスログを出力する。
