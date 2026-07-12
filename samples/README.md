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

## 3. 環境省 暑さ指数（WBGT）— 疎通確認のみ、データサンプルなし

| 確認内容 | 結果 |
|---|---|
| トップページ `https://www.wbgt.env.go.jp/` | HTTP 200（疎通OK） |
| 実測値CSVの直接URL（推測） `wbgt_01100_2026.csv` 等 | HTTP 404（このパターンでは取得不可） |
| データダウンロードページ `wbgt_data_download.php` | HTTP 200 だが、地点コード・年月を JavaScript 経由のフォーム送信で指定する動的な仕組みで、静的HTMLから単純な1本のCSV URLを特定できなかった |

このため本プロジェクトでは環境省WBGTの実測値取得は行わず、`backend/app/services/data_collectors/open_meteo.py` の `estimate_wbgt()` による気温・湿度からの近似推定（`wbgtDerived=true` 明示）に留めている。将来的に実測値接続を行う場合は、地点コード一覧・フォームのPOSTパラメータ仕様を別途調査する必要がある（`#9` の残課題）。

## 注意事項

- ここに保存された内容は取得時点の実際の気象状況・警報発表状況を反映したものであり、再取得すれば内容は変化する（気象警報は日々更新されるため、このサンプルに含まれる警報がその後解除されている可能性がある）。
- 個人情報・秘匿情報は含まれない（気象庁・Open-Meteoともに公開・認証不要のAPIレスポンス）。
