# 外部データソース 実レスポンスサンプル（`#9` データソース接続検証）

`T0-09`（要件§5 準拠。データソースカタログは `#48` マージ後に `docs/api-data-source-catalog.md` として整備予定）。
本ディレクトリは、backend が接続する外部データソースへ実際にHTTPリクエストを送り、
取得できた生レスポンスをそのまま保存したスナップショットである。
「実態以上に良く見せない」方針（設計§16.3）に基づき、モック・加工済みデータではなく実接続結果を記録する。

## 取得日時

2026-07-12 10:24〜10:28 JST（気象データは時々刻々変化するため、あくまで当該時点のスナップショット）。

## サンプル一覧

| ファイル | 取得元 | HTTP | サイズ | 実装参照 |
|---|---|---|---|---|
| `open-meteo-forecast-sample.json` | `api.open-meteo.com/v1/forecast`（緯度43.06/経度141.35＝札幌付近） | 200 | 1,446 bytes | `backend/app/services/data_collectors/open_meteo.py` |
| `jma-warnings-feed-sample.xml` | `www.data.jma.go.jp/developer/xml/feed/extra.xml`（atomフィード全体） | 200 | 319,846 bytes | `backend/app/services/data_collectors/jma_warnings.py` |
| `jma-warning-individual-sample.xml` | 上記フィード内 `<entry><id>` の個別警報XML（函館地方気象台発表分の1件） | 200 | 138,405 bytes | 同上 |
| `wbgt-env-yohou-sample.csv` | `www.wbgt.env.go.jp/prev15WG/dl/yohou_44132.csv`（東京・予報、2026-07-12 14:25更新分） | 200 | 345 bytes | `backend/app/services/data_collectors/wbgt_env.py` |

## 1. Open-Meteo（`open-meteo-forecast-sample.json`）

リクエストパラメータ: `hourly=temperature_2m,relative_humidity_2m,precipitation,wind_speed_10m,wind_gusts_10m&forecast_days=1&timezone=Asia/Tokyo`

トップレベルキー:

```
latitude, longitude, generationtime_ms, utc_offset_seconds,
timezone, timezone_abbreviation, elevation, hourly_units, hourly
```

`hourly` は「配列のオブジェクト」（各キーが同じ長さの配列で、`time[i]` と `temperature_2m[i]` 等が対応する列指向形式）:

```json
{
  "time": ["2026-07-12T00:00", ...],
  "temperature_2m": [20.7, ...],
  "relative_humidity_2m": [97, ...],
  "precipitation": [0.2, ...],
  "wind_speed_10m": [12.8, ...],
  "wind_gusts_10m": [29.2, ...]
}
```

`backend/app/services/data_collectors/open_meteo.py` はこの列指向データを行単位に変換し、`estimate_wbgt(temp_c, rh_pct)` で気温・湿度からWBGTを近似推定する（環境省実測値ではなく **derived** 推定であることが重要な制約）。

## 2. 気象庁 防災情報XML（`jma-warnings-feed-sample.xml` / `jma-warning-individual-sample.xml`）

### 2.1 atomフィード（一覧）

標準的なAtom 1.0形式。`<feed>` 直下に各警報発表を表す `<entry>` が並ぶ:

```xml
<entry>
  <title>気象警報・注意報（Ｈ２７）</title>
  <id>https://www.data.jma.go.jp/developer/xml/data/{発表ID}.xml</id>
  <updated>2026-07-12T01:22:45Z</updated>
  <author><name>{発表官署名}</name></author>
  <link type="application/xml" href="https://www.data.jma.go.jp/developer/xml/data/{発表ID}.xml"/>
  <content type="text">{概要テキスト}</content>
</entry>
```

`<id>` と `<link href>` は同じURL（個別警報XMLへのポインタ）。1回の取得で全国の官署から発表された警報・注意報が時系列に混在して並ぶため、`jma_warnings.py` は対象官署・エリアでフィルタしてから個別XMLを取得する設計になっている。

### 2.2 個別警報XML（JMAXML形式・詳細）

`<Report xmlns="http://xml.kishou.go.jp/jmaxml1/">` を頂点とするJMAXML 1.2形式:

```
Report
├── Control（Title / DateTime / Status / EditorialOffice / PublishingOffice）
└── Head（ReportDateTime / TargetDateTime / InfoKind / Headline）
    └── Headline
        └── Information（type="気象警報・注意報（府県予報区等）" 等）
            └── Item
                ├── Kind（Name: 警報種別名 / Code: 警報種別コード）
                └── Areas > Area（Name: 地域名 / Code: 地域コード）
```

`jma_warnings.py` はこの構造から `Kind/Name`（例: 「大雨注意報」「洪水警報」）と `Area/Code` を抽出し、現場の所在市区町村コードと突き合わせて severity を引き上げる（`services/jma_warnings.py`、公式優先 §8.3-6）。`defusedxml` でパースしXXE対策済み。

## 3. 環境省 暑さ指数（WBGT）— `wbgt-env-yohou-sample.csv`（予報CSV・実接続成功）

**2026-07-12 追記**: 当初調査（下表の旧記録）では実況CSVのURLパターン特定に失敗していたが、
**予報CSV**は `https://www.wbgt.env.go.jp/prev15WG/dl/yohou_{地点コード}.csv` の静的URLで
取得できることを確認（HTTP 200、認証不要）。`yohou_44132.csv`（東京）の実取得結果を
`wbgt-env-yohou-sample.csv` として保存し、`backend/app/services/data_collectors/wbgt_env.py`
で実接続コレクタを実装済み。

| 確認内容 | 結果 |
|---|---|
| トップページ `https://www.wbgt.env.go.jp/` | HTTP 200（疎通OK） |
| **予報CSV** `prev15WG/dl/yohou_44132.csv` | **HTTP 200（345 bytes、コレクタ実装済み）** |
| 実況値CSVの直接URL（推測） `wbgt_01100_2026.csv` 等 | HTTP 404（このパターンでは取得不可・未実装のまま） |
| データダウンロードページ `wbgt_data_download.php` | HTTP 200 だが動的フォーム形式（実況値の取得経路としては未解明のまま） |

### レスポンス形状（予報CSV）

2行のCSV。1行目が予測対象時刻列、2行目が地点・更新時刻・値列:

```
,,2026071215,2026071218,...,2026071424
44132,2026/07/12 14:25, 280, 250,..., 250
```

- 予測対象時刻: `YYYYMMDDHH`（JST・3時間刻み）。**HH=01..24 で、24は翌日00時を意味する**（パーサで繰り上げ処理）
- 値: WBGT×10 の整数（` 280` → 28.0℃）。先頭に半角スペースあり。欠測は空文字
- サービス提供は夏期（概ね4月下旬〜10月）のみで、**期間外はCSV自体が404になる**想定
  （コレクタは status=ERROR で返し、判定側は従来の推定値へフォールバックする）

利用は `WBGT_STATION_CODE`（.env）に地点コードを設定した場合のみ有効。未設定なら従来どおり
`estimate_wbgt()` による推定（`wbgtDerived=true`）。現場別の最寄り地点自動選定は `#29` のスコープ。

## 注意事項

- ここに保存された内容は取得時点の実際の気象状況・警報発表状況を反映したものであり、再取得すれば内容は変化する（気象警報は日々更新されるため、このサンプルに含まれる警報がその後解除されている可能性がある）。
- 個人情報・秘匿情報は含まれない（気象庁・Open-Meteoともに公開・認証不要のAPIレスポンス）。
