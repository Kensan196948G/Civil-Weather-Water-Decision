# API / データソース カタログ

`T0-11`（要件§5, §22 準拠）。本システムが利用する外部データソースの取得元・条件・実装状況を一覧化する。
「実態以上に良く見せない」方針（設計§16.3）に基づき、未接続・推定値であるソースもその旨を明記する。

## 一覧（`backend/app/seed.py` `SOURCES` / `data_collectors/source_probe.py` `PROBE_TARGETS` 準拠）

| ID | 名称 | 区分 | 用途 | エンドポイント | 認証 | 利用条件・制限 | 実装状況 | 実装参照 |
|---|---|---|---|---|---|---|---|---|
| DS-JMA-XML | 気象庁 防災情報XML | 公式・最優先 | 警報・注意報を判定に反映（公式優先） | atomフィード `www.data.jma.go.jp/developer/xml/feed/extra.xml` → 個別警報XML | 不要 | 無料・登録不要（気象庁利用規約の範囲で二次利用可）。高頻度取得は避ける運用が望ましく本実装は10分キャッシュ | **実装済み・実接続**（10分キャッシュ） | `jma_warnings.py` |
| DS-OPEN-METEO | Open-Meteo | 外部API・補完 | 気象予報の補完・時系列生成 | `api.open-meteo.com/v1/forecast` | 不要（APIキー不要） | 非商用利用は無料枠（10,000 calls/日・SLAなし）。**商用利用・SLA保証には有償プラン契約が必要**（[確認書](./open-meteo-commercial-terms-review.md)・要承認） | **実装済み・実接続**（予報キャッシュ5分毎ウォーム） | `open_meteo.py` |
| DS-WBGT | 環境省 暑さ指数(WBGT) | 公式 | 熱中症対策の判定材料 | 予報CSV `www.wbgt.env.go.jp/prev15WG/dl/yohou_{地点コード}.csv` | 不要 | 公開サイト。提供は夏期（概ね4月下旬〜10月、期間外は404）。大量・商用取得は個別に利用条件確認が望ましい | **実装済み・実接続（設定制）**: `WBGT_STATION_CODE` 設定時のみ公式予報値を採用（`wbgtDerived=false`）。未設定時・取得失敗時は気温湿度からの **derived 推定**へフォールバック | `wbgt_env.py` / `open_meteo.py: estimate_wbgt()` |
| DS-RIVER-GO | 川の防災情報（国交省） | 公式（参照） | 河川水位情報の参照案内 | `www.river.go.jp` | 不要 | 無認証の機械可読APIは非公開（Webサイト参照のみ想定） | 疎通プローブのみ。無認証の実測水位APIが無いため公式サイト参照リンク案内に留める | `source_probe.py` |
| DS-WATER-OPEN | 水防災オープンデータ提供サービス | 準公式（契約制） | 河川観測所の実測データ（将来） | ― | 要契約 | **本格利用には利用者登録・契約が必要**（有償プランあり）。再配布・SLA等は契約条件に依存 | **意図的にプローブ対象外**。未接続・シードのError状態を保持（契約・利用条件確認が必要） | `seed.py: SOURCES` |
| DS-NASA-POWER | NASA POWER | 公式・補助 | 日射量・長期傾向の参考データ | `power.larc.nasa.gov` | 不要 | NASA公開データ方針に基づき自由利用可。呼出頻度はFair Use的運用（明示的な数値上限は非公開） | 疎通プローブのみ。判定へは未反映（将来拡張） | `source_probe.py` |
| DS-JMA-CSV | 気象庁 気象データ高度利用（アメダス） | 公式 | アメダス気温・雨量・風速（CSV/機械判読） | `www.jma.go.jp/bosai/amedas/` | 不要 | 無料・登録不要（気象庁利用規約の範囲で二次利用可） | 疎通プローブのみ。判定へは未反映 | `source_probe.py` |
| DS-JAXA | JAXA G-Portal / Earth API | 公式・補助 | 衛星データ・水循環 | `gportal.jaxa.jp/gpr/` | 本格利用は要利用登録 | 研究目的中心。商用利用は個別に利用規約確認が必要 | 疎通プローブのみ。初期は将来拡張扱い | `source_probe.py` |
| DS-NOAA | NOAA | 公式・補助 | 海外気象・研究・補完 | `api.weather.gov` | 不要 | 米国政府データは原則パブリックドメイン相当で自由利用可 | 疎通プローブのみ。国内現場では初期必須ではない | `source_probe.py` |

> 「利用条件・制限」列は一般に公知とされる情報の要約（PoC調査時点）であり、正式な契約・大量アクセスを伴う本番運用の前には、各機関の最新の利用規約・APIドキュメントを必ず確認すること（実態以上に良く見せない方針・設計§16.3）。

## 判定エンジンへの反映優先順位（公式優先原則・設計§8.3-6）

1. **DS-JMA-XML**（気象庁警報・注意報）— 最優先。洪水警報/注意報→河川、大雨警報→河川/土工/打設/舗装の severity を引き上げる。
2. **DS-RIVER-GO**（河川公式参照）— 実測水位が取得できない場合の代替案内。
3. **DS-OPEN-METEO**（予報補完）— 気温・降雨・風速の時系列補完。公式警報と矛盾する場合は公式を優先。
4. **DS-WBGT**（環境省WBGT予報）— `WBGT_STATION_CODE` 設定時は公式予報値を推定値より優先（§5.3）。未設定・取得失敗時は気温湿度からの推定へフォールバック。
5. その他（NASA POWER・JMA-CSV・JAXA・NOAA）— 補助情報・将来拡張。

## プローブ・キャッシュ関連の環境変数（`backend/app/core/config.py`）

| 変数 | 既定値 | 説明 |
|---|---|---|
| `OPEN_METEO_BASE_URL` | `https://api.open-meteo.com/v1` | Open-Meteo エンドポイント |
| `JMA_FEED_URL` | `https://www.data.jma.go.jp/developer/xml/feed/extra.xml` | 気象庁防災情報XML atomフィード |
| `WBGT_BASE_URL` | `https://www.wbgt.env.go.jp` | 環境省WBGT予報CSVのベースURL |
| `WBGT_STATION_CODE` | （空＝無効） | 環境省WBGT予報の地点コード（例: 44132=東京）。設定時のみ公式予報値を採用 |
| `ENABLE_JMA_WARNINGS` | `true` | 気象庁警報反映の有効/無効 |
| `ENABLE_SCHEDULER` | `true` | 定期プローブ/予報リフレッシュの有効/無効（テストでは `false`） |
| `PROBE_INTERVAL_SECONDS` | `300`（5分） | データソース実プローブの間隔 |
| `FORECAST_REFRESH_SECONDS` | `300`（5分） | 予報キャッシュのウォーム間隔 |
| `PROBE_TIMEOUT_SECONDS` | `8` | 疎通プローブのタイムアウト |
| `DATA_FETCH_TIMEOUT_SECONDS` | `20` | 気象データ取得のタイムアウト |
| `DATA_FETCH_RETRY_COUNT` | `3` | 気象データ取得のリトライ回数 |

## 既知の制限・残課題

- **Open-Meteo（DS-OPEN-METEO）の商用利用条件は未承認**（[確認書](./open-meteo-commercial-terms-review.md)）。
  社内業務利用として有償プラン契約の要否・帰属表示（CC BY 4.0）・SLAを法務/ITへ確認中。
- WBGT（DS-WBGT）は予報CSVの実接続を実装済みだが**単一地点設定制**（`WBGT_STATION_CODE`）。現場ごとの最寄り地点自動選定は観測所マスタ正規化（`#29`）待ち。実況値CSVは取得経路未解明のため未実装（`samples/README.md` §3）。地点コード未設定時は従来どおり気温・湿度からの推定値。
- 河川（DS-RIVER-GO, DS-WATER-OPEN）は実測水位の自動取得が未接続（`#31` 残課題）。
  観測所マスタ・現場紐付け（`#29`）と、手動実測を使った判定エンジン組み込み（`#112`）は実装済み。
- NASA POWER / JMA-CSV / JAXA / NOAA は疎通確認のみで判定エンジンには未反映（将来拡張）。
