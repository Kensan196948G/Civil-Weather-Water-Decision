# Construction Weather & Water Decision Support  
# 気象・河川・施工判断支援システム 詳細仕様設計書

| 項目 | 内容 |
|---|---|
| 文書種別 | 詳細仕様設計書 |
| システム名 | Construction Weather & Water Decision Support / 気象・河川・施工判断支援システム |
| 推奨リポジトリ名 | `Civil-Weather-Water-Decision` |
| GitHub URL | `https://github.com/Kensan196948G/Civil-Weather-Water-Decision.git` |
| 作成日 | 2026-06-19 |
| 前提文書 | 要件定義書 `Civil-Weather-Water-Decision_Requirements.md` |

---

## 1. 設計方針

本システムは、気象・河川・暑さ指数などの公開データを収集し、現場作業ごとの施工判断材料を整理するWebアプリケーションとして設計する。

設計上の最重要方針は以下である。

1. 最終判断は現場責任者が行う。
2. システムは判断材料、注意レベル、根拠、データ取得時刻を提示する。
3. 公式情報を優先し、外部予報APIは補助データとして扱う。
4. 欠測・遅延・不整合を隠さず、明示する。
5. PoCでは公開データ中心、本番候補では認証・監査・運用を強化する。

---

## 2. 全体アーキテクチャ

### 2.1 論理構成

```mermaid
flowchart LR
    U[利用者\n現場管理者・安全担当・技術部門] --> FE[Web UI]
    FE --> API[Backend API]
    API --> DB[(PostgreSQL)]
    API --> CACHE[(Cache)]
    API --> RULE[Decision Rule Engine]
    API --> AUTH[Auth / RBAC]

    COL[Data Collector] --> EXT1[気象庁]
    COL --> EXT2[川の防災情報]
    COL --> EXT3[水防災オープンデータ]
    COL --> EXT4[Open-Meteo]
    COL --> EXT5[環境省 WBGT]
    COL --> EXT6[NASA / JAXA]
    COL --> DB
    COL --> DSS[Data Source Status]

    RULE --> DB
    API --> LOG[Audit / App Logs]
```

### 2.2 推奨技術スタック

| レイヤー | PoC候補 | 本番候補 |
|---|---|---|
| フロントエンド | React + Vite / Next.js | React + Next.js |
| UI | Tailwind CSS / shadcn系 | Tailwind CSS / 社内UIガイド準拠 |
| バックエンド | FastAPI | FastAPI / Azure Container Apps |
| DB | SQLite / PostgreSQL | Azure Database for PostgreSQL |
| バッチ | Python APScheduler / cron | Azure Functions / Container Job |
| キャッシュ | In-memory / SQLite | Redis / Azure Cache for Redis |
| 認証 | 簡易ログイン | Entra ID / HENNGE ONE方針と整合 |
| 監視 | アプリログ | Azure Monitor / Application Insights |
| デプロイ | Cloudflare Pages + API | Azure Static Web Apps + API / App Service |

---

## 3. システム構成

### 3.1 コンポーネント一覧

| コンポーネント | 役割 |
|---|---|
| Web UI | ダッシュボード、現場詳細、判断画面、管理画面を提供 |
| Backend API | フロントエンド向けAPI、認証、権限制御、判定呼び出し |
| Data Collector | 外部データ取得、正規化、保存、取得状態監視 |
| Decision Rule Engine | 作業種別ごとの注意レベル判定、理由生成 |
| Data Quality Service | 欠測、遅延、異常値、重複のチェック |
| Notification Service | 画面通知、将来のメール/Slack/Teams通知 |
| Audit Logger | 操作ログ、判断履歴、設定変更ログを記録 |
| Admin Console | 現場、観測所、閾値、データソース、ユーザー管理 |

---

## 4. データソース設計

### 4.1 データソース種別

| ID | データソース | 種別 | 取得方式 | 優先度 | 用途 |
|---|---|---|---|---|---|
| DS-JMA-XML | 気象庁 防災情報XML | 公式 | PULL / Atom / XML | 高 | 警報・注意報・防災気象情報 |
| DS-JMA-CSV | 気象庁 気象データ高度利用 | 公式 | CSV取得 | 高 | アメダス、気温、雨量、風速 |
| DS-RIVER-GO | 川の防災情報 | 公式 | 画面リンク・参照 | 高 | 水位・雨量・洪水情報の公式確認 |
| DS-WATER-OPEN | 水防災オープンデータ | 公式/準公式 | 契約後データ配信 | 中〜高 | 雨量・水位・レーダ雨量 |
| DS-OPEN-METEO | Open-Meteo | 外部API | REST JSON | 高 | 予報・時系列・PoC補完 |
| DS-WBGT | 環境省 WBGT | 公式 | CSV / API | 高 | 熱中症リスク |
| DS-NASA-POWER | NASA POWER | 公式 | REST JSON/CSV | 中 | 日射・気象長期傾向 |
| DS-JAXA | JAXA G-Portal / Earth API | 公式 | API / ファイル | 中 | 衛星データ・研究用途 |
| DS-NOAA | NOAA | 公式 | API / ファイル | 低〜中 | 海外・研究・補完 |

### 4.2 データ取得頻度

| データ | PoC取得頻度 | 本番候補取得頻度 | 備考 |
|---|---:|---:|---|
| 気象予報 | 1〜3時間 | 30分〜1時間 | API制限に合わせる |
| 気象観測 | 30分〜1時間 | 10〜30分 | データ公開頻度に合わせる |
| 警報・注意報 | 10〜30分 | 5〜15分 | 荒天時は頻度増加検討 |
| 河川水位 | 手動参照〜30分 | 5〜10分 | 利用データ契約に依存 |
| WBGT | 1時間 | 30分〜1時間 | 夏期重点 |
| 衛星・長期データ | 日次〜週次 | 日次〜週次 | 初期は参考値 |

### 4.3 データ正規化仕様

| 項目 | 正規化方針 |
|---|---|
| 時刻 | UTC保存、画面はJST表示 |
| 緯度経度 | WGS84、10進数 |
| 気温 | ℃ |
| 降雨量 | mm/h、累積値はmm |
| 風速 | m/s |
| 湿度 | % |
| 水位 | m |
| WBGT | ℃相当の指数値 |
| データ元 | `source_id`で必ず保持 |
| 信頼区分 | `official`, `semi_official`, `external`, `derived` |

---

## 5. データクレンジング設計

### 5.1 入力検証

| チェック | 内容 | 異常時処理 |
|---|---|---|
| 必須項目 | 地点、時刻、値、データ元 | 取り込み拒否、ログ記録 |
| 型 | 数値、日時、文字列 | 変換失敗時は取り込み拒否 |
| 範囲 | 気温、雨量、風速、水位の常識範囲 | 異常フラグ付与 |
| 重複 | 同一地点・同一時刻・同一項目 | 最新または高信頼データを採用 |
| 遅延 | 更新時刻が古い | staleフラグ付与 |
| 欠測 | 値なし、null、欠測コード | missingフラグ付与 |

### 5.2 データ品質フラグ

| フラグ | 意味 |
|---|---|
| `OK` | 通常利用可能 |
| `MISSING` | 欠測 |
| `STALE` | 更新遅延 |
| `OUTLIER` | 異常値候補 |
| `DUPLICATE` | 重複 |
| `SOURCE_ERROR` | データソース障害 |
| `UNVERIFIED` | 検証未完了 |

### 5.3 判定時の扱い

- `MISSING`、`STALE`、`SOURCE_ERROR` が主要データに含まれる場合、判定結果に「確認不能」または「追加確認」を含める。
- `OUTLIER` は自動補正せず、画面上で異常値候補として表示する。
- 公式データと外部APIが矛盾する場合、公式データを優先し、外部APIは参考扱いにする。

---

## 6. データベース設計

### 6.1 ER概要

```mermaid
erDiagram
    users ||--o{ decision_logs : creates
    sites ||--o{ work_plans : has
    sites ||--o{ site_stations : maps
    work_plans ||--o{ decision_results : evaluated_by
    decision_results ||--o{ decision_reasons : has
    decision_results ||--o{ decision_logs : recorded_as
    data_sources ||--o{ data_source_statuses : has
    stations ||--o{ weather_observations : records
    stations ||--o{ river_observations : records
    stations ||--o{ wbgt_observations : records
    decision_rules ||--o{ decision_results : used_by
```

### 6.2 テーブル定義

#### 6.2.1 `users`

| カラム | 型 | 必須 | 説明 |
|---|---|---|---|
| id | UUID | Yes | ユーザーID |
| display_name | varchar(100) | Yes | 表示名 |
| email | varchar(255) | Yes | メールアドレス |
| role | varchar(50) | Yes | ロール |
| department | varchar(100) | No | 部署 |
| is_active | boolean | Yes | 有効フラグ |
| created_at | timestamptz | Yes | 作成日時 |
| updated_at | timestamptz | Yes | 更新日時 |

#### 6.2.2 `sites`

| カラム | 型 | 必須 | 説明 |
|---|---|---|---|
| id | UUID | Yes | 現場ID |
| site_code | varchar(50) | Yes | 現場コード |
| name | varchar(200) | Yes | 現場名 |
| address | text | No | 住所 |
| latitude | numeric(10,7) | Yes | 緯度 |
| longitude | numeric(10,7) | Yes | 経度 |
| project_type | varchar(30) | No | 公共/民間/その他 |
| river_work_flag | boolean | Yes | 河川内・河川近接作業有無 |
| status | varchar(30) | Yes | active / inactive / archived |
| manager_user_id | UUID | No | 現場管理者 |
| created_at | timestamptz | Yes | 作成日時 |
| updated_at | timestamptz | Yes | 更新日時 |

#### 6.2.3 `work_types`

| カラム | 型 | 必須 | 説明 |
|---|---|---|---|
| id | UUID | Yes | 作業種別ID |
| code | varchar(50) | Yes | 作業コード |
| name | varchar(100) | Yes | 作業名 |
| description | text | No | 説明 |
| default_rule_profile_id | UUID | No | 標準ルール |
| is_active | boolean | Yes | 有効フラグ |

#### 6.2.4 `work_plans`

| カラム | 型 | 必須 | 説明 |
|---|---|---|---|
| id | UUID | Yes | 作業予定ID |
| site_id | UUID | Yes | 現場ID |
| work_type_id | UUID | Yes | 作業種別ID |
| planned_start_at | timestamptz | Yes | 作業開始予定 |
| planned_end_at | timestamptz | Yes | 作業終了予定 |
| contractor_name | varchar(200) | No | 協力会社名 |
| work_summary | text | No | 作業概要 |
| status | varchar(30) | Yes | planned / done / postponed / cancelled |
| created_by | UUID | Yes | 登録者 |
| created_at | timestamptz | Yes | 作成日時 |
| updated_at | timestamptz | Yes | 更新日時 |

#### 6.2.5 `stations`

| カラム | 型 | 必須 | 説明 |
|---|---|---|---|
| id | UUID | Yes | 観測所ID |
| source_id | varchar(50) | Yes | データソースID |
| station_code | varchar(100) | Yes | 観測所コード |
| name | varchar(200) | Yes | 観測所名 |
| station_type | varchar(50) | Yes | weather / river / wbgt / satellite |
| latitude | numeric(10,7) | No | 緯度 |
| longitude | numeric(10,7) | No | 経度 |
| river_name | varchar(200) | No | 河川名 |
| basin_name | varchar(200) | No | 流域名 |
| is_active | boolean | Yes | 有効フラグ |

#### 6.2.6 `site_stations`

| カラム | 型 | 必須 | 説明 |
|---|---|---|---|
| id | UUID | Yes | ID |
| site_id | UUID | Yes | 現場ID |
| station_id | UUID | Yes | 観測所ID |
| relation_type | varchar(50) | Yes | nearest / upstream / reference / manual |
| distance_km | numeric(8,3) | No | 距離km |
| priority | int | Yes | 優先順位 |

#### 6.2.7 `weather_forecasts`

| カラム | 型 | 必須 | 説明 |
|---|---|---|---|
| id | UUID | Yes | ID |
| source_id | varchar(50) | Yes | データソースID |
| site_id | UUID | No | 現場ID |
| station_id | UUID | No | 観測所ID |
| forecast_time | timestamptz | Yes | 予報対象時刻 |
| fetched_at | timestamptz | Yes | 取得時刻 |
| temperature_c | numeric(5,2) | No | 気温 |
| precipitation_mm | numeric(8,2) | No | 降雨量 |
| wind_speed_ms | numeric(6,2) | No | 風速 |
| wind_gust_ms | numeric(6,2) | No | 突風 |
| relative_humidity_pct | numeric(5,2) | No | 湿度 |
| weather_code | varchar(50) | No | 天気コード |
| quality_flag | varchar(30) | Yes | 品質フラグ |

#### 6.2.8 `river_observations`

| カラム | 型 | 必須 | 説明 |
|---|---|---|---|
| id | UUID | Yes | ID |
| source_id | varchar(50) | Yes | データソースID |
| station_id | UUID | Yes | 観測所ID |
| observed_at | timestamptz | Yes | 観測時刻 |
| fetched_at | timestamptz | Yes | 取得時刻 |
| water_level_m | numeric(8,3) | No | 水位 |
| rainfall_10min_mm | numeric(8,2) | No | 10分雨量 |
| rainfall_1h_mm | numeric(8,2) | No | 1時間雨量 |
| rainfall_continuous_mm | numeric(8,2) | No | 連続雨量 |
| flood_warning_level | varchar(50) | No | 水位・警戒区分 |
| quality_flag | varchar(30) | Yes | 品質フラグ |

#### 6.2.9 `wbgt_observations`

| カラム | 型 | 必須 | 説明 |
|---|---|---|---|
| id | UUID | Yes | ID |
| source_id | varchar(50) | Yes | データソースID |
| station_id | UUID | Yes | WBGT地点ID |
| target_time | timestamptz | Yes | 対象時刻 |
| fetched_at | timestamptz | Yes | 取得時刻 |
| wbgt | numeric(5,2) | No | 暑さ指数 |
| risk_level | varchar(30) | No | 注意/警戒/厳重警戒/危険等 |
| quality_flag | varchar(30) | Yes | 品質フラグ |

#### 6.2.10 `decision_rules`

| カラム | 型 | 必須 | 説明 |
|---|---|---|---|
| id | UUID | Yes | ルールID |
| rule_profile_name | varchar(200) | Yes | ルールプロファイル名 |
| work_type_id | UUID | Yes | 作業種別ID |
| condition_key | varchar(100) | Yes | 条件キー |
| operator | varchar(20) | Yes | 比較演算子 |
| threshold_value | numeric(10,3) | No | 閾値 |
| threshold_text | varchar(100) | No | 文字閾値 |
| severity | int | Yes | 0〜3 |
| message_template | text | Yes | 理由文テンプレート |
| is_active | boolean | Yes | 有効フラグ |

#### 6.2.11 `decision_results`

| カラム | 型 | 必須 | 説明 |
|---|---|---|---|
| id | UUID | Yes | 判定結果ID |
| work_plan_id | UUID | Yes | 作業予定ID |
| evaluated_at | timestamptz | Yes | 判定日時 |
| overall_level | int | Yes | 0通常/1注意/2中止検討/3確認不能 |
| overall_label | varchar(50) | Yes | 表示ラベル |
| summary | text | Yes | 判定サマリ |
| data_quality_summary | text | No | データ品質サマリ |
| rule_version | varchar(50) | No | ルールバージョン |

#### 6.2.12 `decision_reasons`

| カラム | 型 | 必須 | 説明 |
|---|---|---|---|
| id | UUID | Yes | ID |
| decision_result_id | UUID | Yes | 判定結果ID |
| severity | int | Yes | 重要度 |
| reason_code | varchar(100) | Yes | 理由コード |
| message | text | Yes | 理由文 |
| source_id | varchar(50) | No | 関連データソース |
| observed_value | varchar(100) | No | 観測値・予報値 |

#### 6.2.13 `decision_logs`

| カラム | 型 | 必須 | 説明 |
|---|---|---|---|
| id | UUID | Yes | 判断履歴ID |
| decision_result_id | UUID | Yes | 判定結果ID |
| site_id | UUID | Yes | 現場ID |
| final_action | varchar(50) | Yes | execute / postpone / cancel / monitor / other |
| comment | text | No | 現場判断メモ |
| decided_by | UUID | Yes | 判断者 |
| decided_at | timestamptz | Yes | 判断日時 |
| attachment_url | text | No | 添付・参照URL |

---

## 7. API設計

### 7.1 共通仕様

| 項目 | 仕様 |
|---|---|
| API形式 | REST JSON |
| 文字コード | UTF-8 |
| 認証 | PoCは簡易トークン、本番候補はOIDC / Entra ID |
| 日時形式 | ISO 8601 |
| タイムゾーン | DBはUTC、レスポンスはJST指定可 |
| エラー形式 | `code`, `message`, `details`, `request_id` |

### 7.2 エンドポイント一覧

#### 7.2.1 現場管理

| メソッド | パス | 概要 |
|---|---|---|
| GET | `/api/sites` | 現場一覧取得 |
| POST | `/api/sites` | 現場登録 |
| GET | `/api/sites/{site_id}` | 現場詳細取得 |
| PUT | `/api/sites/{site_id}` | 現場更新 |
| DELETE | `/api/sites/{site_id}` | 現場無効化 |
| GET | `/api/sites/{site_id}/stations` | 紐付け観測所取得 |
| POST | `/api/sites/{site_id}/stations` | 観測所紐付け |

#### 7.2.2 作業予定

| メソッド | パス | 概要 |
|---|---|---|
| GET | `/api/work-plans` | 作業予定一覧 |
| POST | `/api/work-plans` | 作業予定登録 |
| GET | `/api/work-plans/{work_plan_id}` | 作業予定詳細 |
| PUT | `/api/work-plans/{work_plan_id}` | 作業予定更新 |
| POST | `/api/work-plans/{work_plan_id}/evaluate` | 作業判断評価実行 |

#### 7.2.3 ダッシュボード

| メソッド | パス | 概要 |
|---|---|---|
| GET | `/api/dashboard/site-risk` | 現場別リスク一覧 |
| GET | `/api/dashboard/today` | 当日作業リスク一覧 |
| GET | `/api/dashboard/data-sources` | データソース状態 |

#### 7.2.4 気象・河川データ

| メソッド | パス | 概要 |
|---|---|---|
| GET | `/api/weather/forecast` | 気象予報取得 |
| GET | `/api/weather/timeseries` | 気象時系列取得 |
| GET | `/api/river/timeseries` | 河川時系列取得 |
| GET | `/api/wbgt/timeseries` | WBGT時系列取得 |
| POST | `/api/data-collectors/run` | 手動データ取得 |

#### 7.2.5 判断履歴

| メソッド | パス | 概要 |
|---|---|---|
| GET | `/api/decision-results/{id}` | 判定結果詳細 |
| POST | `/api/decision-logs` | 現場判断メモ登録 |
| GET | `/api/decision-logs` | 判断履歴検索 |
| GET | `/api/decision-logs/export.csv` | 判断履歴CSV出力 |

#### 7.2.6 管理設定

| メソッド | パス | 概要 |
|---|---|---|
| GET | `/api/admin/rules` | 判定ルール一覧 |
| POST | `/api/admin/rules` | 判定ルール登録 |
| PUT | `/api/admin/rules/{rule_id}` | 判定ルール更新 |
| GET | `/api/admin/data-sources` | データソース一覧 |
| PUT | `/api/admin/data-sources/{source_id}` | データソース設定更新 |

---

## 8. 判定エンジン設計

### 8.1 入力

判定エンジンは以下を入力とする。

```json
{
  "site_id": "uuid",
  "work_plan_id": "uuid",
  "work_type": "concrete_placement",
  "planned_start_at": "2026-06-20T08:00:00+09:00",
  "planned_end_at": "2026-06-20T12:00:00+09:00",
  "weather_forecasts": [],
  "river_observations": [],
  "wbgt_observations": [],
  "data_quality_flags": []
}
```

### 8.2 出力

```json
{
  "overall_level": 1,
  "overall_label": "注意",
  "summary": "作業時間帯に降雨予報があり、風速もやや高めです。打設計画と養生計画を再確認してください。",
  "reasons": [
    {
      "severity": 1,
      "reason_code": "RAIN_FORECAST",
      "message": "作業時間帯に降雨予報があります。",
      "source_id": "DS-OPEN-METEO",
      "observed_value": "precipitation=1.5mm/h"
    }
  ],
  "data_quality_summary": "主要データは取得済みです。"
}
```

### 8.3 判定ロジック共通仕様

1. 作業予定時間帯に該当する予報・観測データを抽出する。
2. 作業種別に紐づく有効ルールを取得する。
3. 各ルールを評価し、該当した理由を生成する。
4. 重大度の最大値を全体レベルとする。
5. 主要データに欠測・遅延がある場合は、確認不能理由を追加する。
6. 公式警報・注意報がある場合は、該当作業に関する注意レベルを引き上げる。
7. 判定結果と理由を保存する。

---

## 9. 初期判定ルール仕様

### 9.1 重大度

| severity | label | 説明 |
|---:|---|---|
| 0 | 通常 | 通常確認で作業検討可能 |
| 1 | 注意 | 現地確認・追加確認が必要 |
| 2 | 中止検討 | 作業中止・延期・方法変更の検討が必要 |
| 3 | 確認不能 | データ不足・取得失敗・遅延により判断材料不足 |

### 9.2 ルール例：コンクリート打設

| 条件キー | 条件 | severity | 理由文テンプレート |
|---|---|---:|---|
| `rain_forecast` | 作業時間帯に降雨予報あり | 1 | 作業時間帯に降雨予報があります。打設・養生計画を確認してください。 |
| `heavy_rain_forecast` | 降雨量が現場設定閾値以上 | 2 | 降雨量が閾値を超える可能性があります。打設延期または養生強化を検討してください。 |
| `low_temperature` | 気温が低温閾値以下 | 1 | 気温が低くなる見込みです。低温時の施工条件を確認してください。 |
| `high_temperature` | 気温が高温閾値以上 | 1 | 気温が高くなる見込みです。暑中コンクリート対策を確認してください。 |
| `missing_weather` | 気象データ欠測 | 3 | 主要気象データが取得できません。公式情報と現地確認を行ってください。 |

### 9.3 ルール例：クレーン作業

| 条件キー | 条件 | severity | 理由文テンプレート |
|---|---|---:|---|
| `strong_wind` | 風速が注意閾値以上 | 1 | 風速が高くなる見込みです。クレーン仕様・吊荷条件を確認してください。 |
| `gust_risk` | 突風が中止検討閾値以上 | 2 | 突風リスクがあります。作業中止・待機を検討してください。 |
| `thunderstorm` | 雷リスク・雷注意報 | 2 | 雷リスクがあります。屋外作業員の退避を含めて検討してください。 |
| `missing_wind` | 風速データ欠測 | 3 | 風速データが取得できません。現地風況と公式情報を確認してください。 |

### 9.4 ルール例：河川内作業

| 条件キー | 条件 | severity | 理由文テンプレート |
|---|---|---:|---|
| `upstream_rain` | 上流雨量が注意閾値以上 | 1 | 上流域で雨量が増加しています。水位上昇に注意してください。 |
| `water_level_rising` | 水位上昇傾向 | 1 | 水位が上昇傾向です。退避基準と作業継続条件を確認してください。 |
| `flood_warning` | 洪水予報・水位到達情報あり | 2 | 洪水関連情報が発表されています。河川内作業の中止・退避を検討してください。 |
| `river_data_stale` | 河川データ更新遅延 | 3 | 河川データが更新遅延しています。公式ページと現地確認を行ってください。 |

### 9.5 ルール例：熱中症対策

| 条件キー | 条件 | severity | 理由文テンプレート |
|---|---|---:|---|
| `wbgt_caution` | WBGT注意以上 | 1 | 暑さ指数が上昇しています。水分補給と休憩を徹底してください。 |
| `wbgt_danger` | WBGT危険相当 | 2 | 暑さ指数が高く危険です。作業時間変更・中止を検討してください。 |
| `wbgt_missing` | WBGTデータ欠測 | 3 | WBGTデータが取得できません。気温・湿度・現場環境から追加確認してください。 |

---

## 10. 画面詳細仕様

### 10.1 SC-001 ダッシュボード

#### 目的
複数現場の気象・河川・熱中症リスクを一覧で確認する。

#### 表示項目

| 項目 | 内容 |
|---|---|
| 現場名 | 現場詳細へのリンク |
| 所在地 | 市区町村または住所概要 |
| 本日の最大リスク | 通常/注意/中止検討/確認不能 |
| 主要理由 | 最大3件表示 |
| 降雨 | 現在/予報ピーク |
| 風速 | 最大風速/突風 |
| 気温 | 最高/最低 |
| WBGT | 最大値・リスク |
| 河川 | 水位傾向・公式リンク |
| データ取得時刻 | 最終更新時刻 |

#### 操作

- 現場詳細へ遷移
- リスクレベルで絞り込み
- 作業種別で絞り込み
- CSV出力
- 手動再取得

---

### 10.2 SC-002 現場詳細

#### 表示ブロック

1. 現場基本情報
2. 今日・明日・明後日の気象サマリ
3. 作業予定一覧
4. 作業別判断結果
5. 気象時系列グラフ
6. 河川時系列グラフ
7. WBGTグラフ
8. 判断履歴
9. 公式リンク

#### 重要UI仕様

- データ取得元と取得時刻を各ブロックに表示する。
- データ欠測時はグラフを空白にせず、欠測表示を出す。
- 「システムは判断支援であり、最終判断は現場責任者が行う」旨をフッターまたは注意文に表示する。

---

### 10.3 SC-003 作業判断画面

#### 入力項目

| 項目 | 入力方式 | 必須 |
|---|---|---|
| 現場 | 選択 | Yes |
| 作業種別 | 選択 | Yes |
| 作業開始 | 日時 | Yes |
| 作業終了 | 日時 | Yes |
| 協力会社 | テキスト | No |
| 作業概要 | テキスト | No |

#### 出力項目

| 項目 | 内容 |
|---|---|
| 全体レベル | 通常/注意/中止検討/確認不能 |
| サマリ | 判定文章 |
| 理由一覧 | 条件、値、データ元、時刻 |
| 参照データ | 気象、河川、WBGT |
| 公式リンク | 気象庁、川の防災情報等 |
| 判断メモ | 実施/延期/中止/監視継続と理由 |

---

### 10.4 SC-008 データソース状態画面

| 表示項目 | 内容 |
|---|---|
| データソース名 | 例：Open-Meteo、気象庁、WBGT |
| 状態 | OK / Warning / Error |
| 最終取得成功 | 日時 |
| 最終取得失敗 | 日時 |
| 失敗回数 | 連続失敗回数 |
| 平均応答時間 | ms |
| 備考 | API制限、メンテナンス情報等 |

---

## 11. バッチ・スケジューラ設計

### 11.1 ジョブ一覧

| ジョブID | ジョブ名 | 実行頻度 | 内容 |
|---|---|---:|---|
| JOB-001 | forecast_fetch | 1時間 | Open-Meteo等から予報取得 |
| JOB-002 | jma_alert_fetch | 15分 | 気象庁防災情報・警報注意報取得 |
| JOB-003 | wbgt_fetch | 1時間 | WBGTデータ取得 |
| JOB-004 | river_fetch | 10〜30分 | 河川水位・雨量取得。PoCは手動/限定 |
| JOB-005 | decision_refresh | 30分 | 当日・翌日の作業予定を再評価 |
| JOB-006 | data_quality_check | 30分 | 欠測・遅延・異常値チェック |
| JOB-007 | cleanup_old_data | 日次 | 保存期間超過データ整理 |

### 11.2 リトライ仕様

| 項目 | 仕様 |
|---|---|
| タイムアウト | 10〜30秒。データソースごとに設定 |
| リトライ回数 | 3回 |
| バックオフ | 30秒、1分、3分 |
| 失敗時 | `data_source_statuses`に記録 |
| 連続失敗 | 管理画面に警告表示 |

---

## 12. 認証・認可設計

### 12.1 認証方式

| フェーズ | 認証方式 |
|---|---|
| PoC | Basic認証、簡易ログイン、限定URL公開 |
| 検証 | アプリ内ユーザー管理またはEntra ID検証 |
| 本番候補 | Entra ID OIDC、既存HENNGE ONE方針との整合確認 |

### 12.2 権限制御

| 機能 | 管理者 | 技術管理者 | 現場管理者 | 安全担当 | 閲覧者 |
|---|---:|---:|---:|---:|---:|
| 全現場閲覧 | ○ | ○ | △ | ○ | △ |
| 担当現場閲覧 | ○ | ○ | ○ | ○ | ○ |
| 現場登録 | ○ | ○ | △ | × | × |
| 作業予定登録 | ○ | ○ | ○ | △ | × |
| 判断メモ登録 | ○ | ○ | ○ | ○ | × |
| 閾値設定 | ○ | ○ | × | △ | × |
| ユーザー管理 | ○ | × | × | × | × |
| データソース設定 | ○ | × | × | × | × |
| 監査ログ閲覧 | ○ | △ | × | × | × |

△は設定により許可可能。

---

## 13. ログ設計

### 13.1 アプリケーションログ

| 項目 | 内容 |
|---|---|
| request_id | リクエストID |
| timestamp | 発生日時 |
| level | INFO / WARN / ERROR |
| component | api / collector / rule_engine / ui |
| message | メッセージ |
| user_id | ユーザーID。未認証時はnull |
| site_id | 関連現場ID |
| source_id | 関連データソースID |

### 13.2 監査ログ対象

- ログイン成功・失敗
- 現場登録・更新・無効化
- 作業予定登録・更新
- 判定実行
- 判断メモ登録
- 閾値変更
- データソース設定変更
- CSV出力
- ユーザー権限変更

---

## 14. 通知設計

### 14.1 初期通知

PoCではアプリ画面内通知を基本とする。

| 通知種別 | 条件 | 表示先 |
|---|---|---|
| リスク通知 | 中止検討レベル発生 | ダッシュボード、現場詳細 |
| データ不確実通知 | 欠測・遅延・取得失敗 | ダッシュボード、データソース状態 |
| WBGT通知 | 厳重警戒以上 | ダッシュボード、熱中症画面 |
| 河川通知 | 水位上昇・洪水情報 | ダッシュボード、河川画面 |

### 14.2 将来通知

- メール通知
- Slack通知
- Teams通知
- DeskNet's Neo通知
- SharePointリスト連携

通知本文には必ず以下を含める。

1. 現場名
2. 作業種別
3. 注意レベル
4. 理由
5. データ取得元
6. 取得時刻
7. 公式確認リンク
8. 「最終判断は現場責任者」文言

---

## 15. エラー設計

### 15.1 APIエラーコード

| コード | HTTP | 内容 |
|---|---:|---|
| `AUTH_REQUIRED` | 401 | 認証が必要 |
| `FORBIDDEN` | 403 | 権限不足 |
| `NOT_FOUND` | 404 | 対象データなし |
| `VALIDATION_ERROR` | 400 | 入力値不正 |
| `DATA_SOURCE_ERROR` | 502 | 外部データ取得失敗 |
| `DATA_STALE` | 200/Warning | データ更新遅延 |
| `DECISION_UNAVAILABLE` | 200/Warning | 判定材料不足 |
| `INTERNAL_ERROR` | 500 | 内部エラー |

### 15.2 画面表示方針

- 外部データ取得失敗時は画面全体を落とさない。
- 該当ブロックだけ「取得できませんでした」と表示する。
- 公式サイトへの確認リンクを表示する。
- 取得失敗時刻を表示する。
- 判定結果は「確認不能」または「追加確認」とする。

---

## 16. セキュリティ設計

### 16.1 基本方針

- 公開データ中心で構成し、個人情報を原則保持しない。
- 現場名・場所が社外秘に該当する場合があるため、公開モックでは実在現場名を使わない。
- 本番候補では、認証、認可、監査ログ、通信暗号化を必須とする。

### 16.2 秘密情報管理

| 秘密情報 | 管理方式 |
|---|---|
| APIキー | `.env`、GitHub Secrets、Azure Key Vault等 |
| DB接続文字列 | 環境変数、Secret管理 |
| OIDC Client Secret | Secret管理 |
| 通知Webhook | Secret管理 |

`.env`はGit管理対象外とする。

### 16.3 公開モック禁止事項

- 実在現場名の掲載
- 個人名、メールアドレス、電話番号の掲載
- 本番データの利用
- 社外秘資料の添付
- 本番SSO接続
- 公式判断と誤認される表現

---

## 17. バックアップ・リストア設計

### 17.1 バックアップ対象

| 対象 | 方式 | 頻度 |
|---|---|---:|
| PostgreSQL | DBダンプ / マネージドバックアップ | 日次 |
| 設定ファイル | Git管理 / Secretは別管理 | 変更時 |
| 判断履歴 | DBバックアップ | 日次 |
| ログ | ログストレージ | 日次/随時 |

### 17.2 リストア要件

| 項目 | 要件 |
|---|---|
| PoC | 手動復旧で可 |
| 検証 | 1営業日以内に復旧 |
| 本番候補 | RTO 4〜8時間、RPO 24時間を初期目標 |

---

## 18. テスト設計

### 18.1 テスト種別

| 種別 | 内容 |
|---|---|
| 単体テスト | 判定ルール、データ正規化、APIバリデーション |
| 結合テスト | データ取得→保存→判定→画面表示 |
| UIテスト | 主要画面の表示・操作 |
| データ品質テスト | 欠測、遅延、異常値、重複 |
| 権限テスト | ロールごとの閲覧・登録制御 |
| 障害テスト | 外部API停止、タイムアウト、DB接続断 |
| 受入テスト | 現場利用者による判断画面確認 |

### 18.2 代表テストケース

| ID | テスト | 期待結果 |
|---|---|---|
| TC-001 | 現場を登録する | 緯度経度付きで登録される |
| TC-002 | 作業予定を登録する | 作業判断画面に表示される |
| TC-003 | 降雨予報ありで評価する | 注意理由が表示される |
| TC-004 | 風速が閾値超過 | クレーン作業で中止検討が表示される |
| TC-005 | 河川データ欠測 | 確認不能が表示される |
| TC-006 | WBGT危険相当 | 熱中症対策で中止検討が表示される |
| TC-007 | データソース停止 | 画面全体は表示され、該当ブロックのみエラー表示 |
| TC-008 | 閲覧者が閾値変更 | 権限不足で拒否される |
| TC-009 | 判断メモ登録 | 判断履歴に保存される |
| TC-010 | CSV出力 | 検索条件に合う履歴が出力される |

---

## 19. ディレクトリ構成詳細

```text
Civil-Weather-Water-Decision/
├── README.md
├── docs/
│   ├── requirements.md
│   ├── detailed-design.md
│   ├── api-data-source-catalog.md
│   ├── decision-rule-guide.md
│   ├── screen-list.md
│   ├── operations-guide.md
│   └── security-notes.md
├── frontend/
│   ├── src/
│   │   ├── app/
│   │   ├── components/
│   │   ├── features/
│   │   │   ├── dashboard/
│   │   │   ├── sites/
│   │   │   ├── work-plans/
│   │   │   ├── decisions/
│   │   │   ├── weather/
│   │   │   ├── river/
│   │   │   └── admin/
│   │   ├── lib/
│   │   └── types/
│   └── package.json
├── backend/
│   ├── app/
│   │   ├── main.py
│   │   ├── api/
│   │   ├── core/
│   │   ├── models/
│   │   ├── schemas/
│   │   ├── services/
│   │   │   ├── data_collectors/
│   │   │   ├── decision_engine/
│   │   │   ├── data_quality/
│   │   │   └── notifications/
│   │   └── repositories/
│   ├── tests/
│   └── pyproject.toml
├── batch/
│   ├── fetch_forecast.py
│   ├── fetch_wbgt.py
│   ├── fetch_river.py
│   └── refresh_decisions.py
├── database/
│   ├── migrations/
│   ├── seed/
│   └── schema.sql
├── samples/
│   ├── sites.sample.csv
│   ├── work_plans.sample.csv
│   └── weather_response.sample.json
├── infra/
│   ├── docker/
│   └── azure/
├── .env.example
├── docker-compose.yml
└── .gitignore
```

---

## 20. `.env.example`案

```env
APP_ENV=local
APP_NAME=Civil-Weather-Water-Decision
APP_TIMEZONE=Asia/Tokyo

DATABASE_URL=postgresql://cw_user:cw_password@localhost:5432/civil_weather_water

OPEN_METEO_BASE_URL=https://api.open-meteo.com/v1
JMA_XML_BASE_URL=https://xml.kishou.go.jp
WBGT_BASE_URL=https://www.wbgt.env.go.jp

ENABLE_AUTH=false
OIDC_ISSUER_URL=
OIDC_CLIENT_ID=
OIDC_CLIENT_SECRET=

LOG_LEVEL=INFO
DATA_FETCH_TIMEOUT_SECONDS=20
DATA_FETCH_RETRY_COUNT=3
```

---

## 21. Docker Compose案

```yaml
services:
  postgres:
    image: postgres:16
    container_name: civil-weather-water-postgres
    environment:
      POSTGRES_DB: civil_weather_water
      POSTGRES_USER: cw_user
      POSTGRES_PASSWORD: cw_password
    ports:
      - "5432:5432"
    volumes:
      - cw_pgdata:/var/lib/postgresql/data

  backend:
    build: ./backend
    container_name: civil-weather-water-backend
    env_file:
      - .env
    ports:
      - "8000:8000"
    depends_on:
      - postgres

  frontend:
    build: ./frontend
    container_name: civil-weather-water-frontend
    ports:
      - "3000:3000"
    depends_on:
      - backend

volumes:
  cw_pgdata:
```

---

## 22. 画面遷移

```mermaid
flowchart TD
    A[ログイン] --> B[ダッシュボード]
    B --> C[現場詳細]
    B --> D[作業予定一覧]
    B --> E[データソース状態]
    C --> F[作業判断画面]
    C --> G[気象時系列]
    C --> H[河川時系列]
    C --> I[WBGT]
    F --> J[判断メモ登録]
    J --> K[判断履歴]
    B --> L[管理設定]
    L --> M[現場管理]
    L --> N[観測所管理]
    L --> O[判定ルール管理]
    L --> P[ユーザー管理]
```

---

## 23. CSV出力仕様

### 23.1 判断履歴CSV

| カラム | 説明 |
|---|---|
| decision_log_id | 判断履歴ID |
| site_code | 現場コード |
| site_name | 現場名 |
| work_type | 作業種別 |
| planned_start_at | 作業開始予定 |
| planned_end_at | 作業終了予定 |
| evaluated_at | システム評価日時 |
| overall_label | 判定レベル |
| summary | 判定サマリ |
| final_action | 現場判断 |
| comment | 判断メモ |
| decided_by | 判断者 |
| decided_at | 判断日時 |
| data_sources | 参照データソース |

---

## 24. 開発優先順位

### 24.1 MVP実装順

1. リポジトリ初期化、README、docs配置
2. DBスキーマ作成
3. 現場登録API・画面
4. 作業予定API・画面
5. Open-Meteo接続
6. WBGTデータ参照または取り込み
7. 簡易判定エンジン
8. ダッシュボード
9. 判断メモ登録
10. データソース状態画面

### 24.2 その後の拡張順

1. 気象庁防災情報XML連携
2. 川の防災情報リンク管理
3. 河川観測所マスタ
4. 水防災オープンデータ連携検証
5. 閾値管理画面
6. 監査ログ
7. 認証・権限
8. 通知連携
9. SharePoint / DeskNet's Neo連携
10. 衛星・研究データ連携

---

## 25. README初期案

```md
# Civil-Weather-Water-Decision

Construction Weather & Water Decision Support  
気象・河川・施工判断支援システム

## Purpose
公開気象データ、河川情報、暑さ指数などを統合し、土木建設現場の作業判断を支援するWebアプリケーションです。

## Important Notice
本システムは施工判断を支援するものであり、作業実施・中止・延期を自動決定するものではありません。最終判断は現場責任者が、公式情報、施工計画、安全基準、現地状況を踏まえて行ってください。

## Initial Target Work Types
- コンクリート打設
- クレーン作業
- 河川内作業
- 土工
- 舗装
- 熱中症対策

## Main Data Sources
- 気象庁
- 国土交通省 川の防災情報
- 水防災オープンデータ
- Open-Meteo
- 環境省 WBGT
- NASA POWER
- JAXA G-Portal / Earth API
```

---

## 26. 受入テスト観点

| 観点 | 合格基準 |
|---|---|
| 現場登録 | サンプル現場3件以上を登録できる |
| 予報取得 | 登録現場の気温・雨量・風速が表示される |
| WBGT表示 | 熱中症画面でWBGTまたは代替表示が確認できる |
| 作業判断 | 作業種別ごとに注意理由が表示される |
| 判断履歴 | 判断メモが保存・検索できる |
| データ品質 | 欠測・遅延時に確認不能表示になる |
| 公式リンク | 気象庁・川の防災情報等の確認導線がある |
| 権限 | 管理者以外が閾値変更できない |
| ログ | 判定実行・判断登録・設定変更が記録される |

---

## 27. 運用手順概要

### 27.1 日次運用

1. データソース状態画面を確認する。
2. 当日作業の中止検討・確認不能を確認する。
3. 必要に応じて現場へ確認依頼する。
4. 判断履歴の登録漏れを確認する。

### 27.2 荒天時運用

1. ダッシュボードを荒天監視モードにする。
2. 河川内作業、クレーン作業、土工を優先確認する。
3. 公式情報への確認リンクを開き、最新発表を確認する。
4. データ遅延がある場合は、現地確認・発注者情報・自治体情報を優先する。
5. 判断結果を履歴に残す。

### 27.3 夏期運用

1. WBGT重点監視を有効化する。
2. 厳重警戒以上の現場を抽出する。
3. 作業時間変更、休憩計画、水分補給、声掛け体制を確認する。
4. 熱中症対策の判断履歴を残す。

---

## 28. 今後の高度化候補

| 項目 | 内容 |
|---|---|
| 類似現場検索 | 過去の気象条件と判断履歴から類似事例を検索 |
| AI調査メモ | 公式情報と取得データから現場向け注意メモを生成 |
| 工程表連携 | 作業予定を工程表・Excelから取り込み |
| 施工実績分析 | 中止・延期判断と実績を比較し、閾値を改善 |
| 河川カメラ連携 | 公式カメラ画像への導線、将来AI解析 |
| GIS連携 | 流域、標高、河川距離、ハザードマップとの連携 |
| モバイルPWA | 現場スマホ・タブレット向け簡易表示 |
| オフライン閲覧 | 直近取得データのキャッシュ表示 |

---

## 29. 実装時の注意事項

- 判定結果に「作業可能」と断定表示しない。
- 「通常」は「主要注意条件なし」という意味に留める。
- 外部APIレスポンスをそのまま信頼せず、必ず正規化・品質フラグ付けを行う。
- データ取得元・取得時刻・更新時刻を隠さない。
- 実在現場名をモック画面に出さない。
- APIキーやSecretをGitHubに置かない。
- 画面上の文言は、現場利用者が一目で理解できる表現にする。

---

## 30. まとめ

本詳細設計では、気象・河川・暑さ指数データを収集し、現場作業ごとの注意レベルと判断根拠を提示するための構成、DB、API、画面、バッチ、判定エンジン、ログ、運用を定義した。

最初のMVPでは、Open-Meteo、気象庁参照、WBGT、判断メモ、ダッシュボードに絞るとよい。その後、河川データ、閾値管理、通知、認証・監査へ段階的に拡張することで、現場に説明しやすく、IT・DX部門でも運用しやすいシステムに育てられる。
