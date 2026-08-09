# 河川観測アーキテクチャ設計（#29 T2-01 / #31 T2-03）

## 1. 目的と現状

プロジェクト名の中核である「河川・水位」の自動取得を、段階的に実装するための設計。

2026-08-09 時点の到達点:

- ✅ 観測所マスタ（`observation_stations`）
- ✅ 現場との多対多紐付け（`site_stations`。上流/最寄り/参照 の区分）
- ✅ 実測値の保存・時系列API（`river_observations`。手動入力）
- ✅ API応答とUIで「自動取得は未接続」を明示
- ✅ 判定エンジンへの実測値組み込み（#112: 水位トレンド・上流雨量・STALE→レベル3）
- ✅ デモ自動取得プロバイダ（`DEMO-RIVER`・10分間隔・`data_source_statuses` 反映）
- ❌ 公式自動取得プロバイダ（水防災オープンデータ提供サービス等）の接続

## 2. 設計原則

1. **公式・有償データサービスを正とする**。川の防災情報（river.go.jp）のHTML/内部JSONの
   スクレイピングは「ブラウザ閲覧前提」のため本番自動取得には使わない。
   本番候補は「水防災オープンデータ提供サービス」（一般財団法人 河川情報センター、実費相当の有償）とする。
2. **欠測・遅延を「安全」と誤認させない**。`quality` に OK/MISSING/STALE/ERROR を保持し、
   判定エンジンは実測が古い・欠測の場合はレベル3（確認不能）へ導く。
3. **観測所は独立マスタ**。PoC版 `stations`（site直結）とは分離し、複数現場への共有・
   地点移設・コード変更を追跡できるようにする。
4. **手動入力は補助**。PoC運用では現場管理者・技術管理者が目視値や公式ページ値を入力できるが、
   入力値であることは `source=MANUAL` と `recorded_by` で常に明示する。

## 3. データモデル

### observation_stations（観測所マスタ）

| カラム | 内容 |
| --- | --- |
| id | 内部ID（OS+連番） |
| source_id | 提供元（MANUAL / SUIBOSAI-OPEN 等） |
| station_code | 提供元の公式観測所コード |
| name / agency / basin_name | 名称・管理機関・河川名 |
| kind | water / rain / water_rain |
| latitude / longitude | 座標（任意） |
| status | active / inactive（移設・廃止は無効化で表現） |

一意制約: `(source_id, station_code)`。地点移設でコードが変わった場合は新規行＋旧行を
inactive にし、`site_stations` を張り替える運用とする（履歴は残る）。

### site_stations（現場紐付け）

| カラム | 内容 |
| --- | --- |
| id | 内部ID（SS+連番） |
| site_id / station_id | 多対多（FK） |
| rel | upstream（上流）/ nearest（最寄り）/ reference（参照） |
| sort_order | 表示順 |

一意制約: `(site_id, station_id)`。1現場あたり最大8件（API側で制限）。

### river_observations（実測値）

| カラム | 内容 |
| --- | --- |
| id | 内部ID（RO+連番） |
| station_id | FK |
| observed_at | 観測時刻（JST ISO） |
| water_level_m / rainfall_mm_h | 水位・雨量（少なくとも一方） |
| quality | OK / MISSING / STALE / ERROR |
| source | MANUAL または提供元ID |
| recorded_at / recorded_by / note | 記録日時・記録者・備考 |

インデックス: `(station_id, observed_at)`。

## 4. API

| メソッド・パス | 権限 | 内容 |
| --- | --- | --- |
| GET /api/observation-stations | 認証 | 観測所一覧（kind/site_id 絞り込み） |
| POST /api/observation-stations | admin/tech_manager | 観測所登録 |
| PUT /api/observation-stations/{id} | admin/tech_manager | 更新（status=inactive で無効化） |
| DELETE /api/observation-stations/{id} | admin | 削除（紐付け・実測がある場合は409） |
| GET /api/sites/{site_id}/observation-stations | 認証 | 現場の観測所＋最新値＋`automatic=false` |
| POST /api/sites/{site_id}/observation-stations | admin/tech_manager | 観測所を現場へ紐付け |
| DELETE /api/sites/{site_id}/observation-stations/{station_id} | admin/tech_manager | 紐付け解除 |
| GET /api/sites/{site_id}/river-observations | 認証 | 直近の実測値（新しい順, limit 1〜500） |
| POST /api/sites/{site_id}/river-observations | admin/tech_manager | 手動実測値の登録 |
| PUT /api/river-observations/{id} | admin/tech_manager | 誤入力の訂正 |
| DELETE /api/river-observations/{id} | admin | 実測値の削除 |

すべての書き込みは監査ログと同一トランザクション（#63 方式）。

## 5. 自動取得プロバイダ（将来実装）

```text
ObservationProvider (interface)
  ├─ ManualProvider      … 現行（API経由の手動入力）
  ├─ DemoRiverProvider   … デモ・シミュレーション（2026-08-09 実装。決定的な水位・雨量を
  │                         10分間隔で自動投入。`source=DEMO-RIVER`、UI/APIに「デモ」と明示）
  ├─ SuibosaiOpenProvider … 水防災オープンデータ提供サービス（本番候補・有償契約）
  └─ (将来) PrefectureProvider … 都道府県が公開するAPI/XML
```

プロバイダは次を返す契約とする。

```python
{
  "station_code": str,
  "observed_at": "YYYY-MM-DDTHH:MM:SS+09:00",
  "water_level_m": float | None,
  "rainfall_mm_h": float | None,
  "quality": "OK" | "MISSING" | "STALE" | "ERROR",
  "raw": {...}  # 監査用の生データ（機微情報なし）
}
```

取得方式の推奨:

- スケジューラ（APScheduler）のジョブとして5〜10分間隔で実行し、`source_id` ごとに
  最終取得成功・失敗連続回数・応答時間を `data_source_statuses` へ記録する。
- 取得失敗・遅延時は `quality=STALE/ERROR` の行を保存し、判定エンジンは「確認不能」へ導く。
- 契約・利用条件・コストの確定までは接続しない（Open-Meteo と同様に法務確認が必要）。

### デモ自動取得（2026-08-09 追加）

- `backend/app/services/data_collectors/river_collector.py` に実装。
- デモ観測所（`source_id=DEMO-RIVER`・`station_code=DEMO-Sxx-*`）を seed とスケジューラで
  冪等に整備し、河川現場（S01/S05/S07/S09/S13）へ上流・最寄りで紐付け。
- 10分粒度の決定的なシミュレーション値（水位・雨量）を upsert し、`DS-RIVER-DEMO` を OK に更新。
- API応答は `automatic=true` かつ provider に「デモ自動取得（DEMO-RIVER・シミュレーション）。
  公式の水防災オープンデータ提供サービスは未接続」と明示。画面にも同旨を表示する。
- 公式サービス接続後は、デモプロバイダを無効化（`RIVER_DEMO_ENABLED=false`）して切替える。

## 6. 判定エンジンへの組み込み（#112: 実装済み）

`assess_site()` は呼び出し側から渡された DB セッションで、現場に紐付く最寄り/上流
観測所（`site_stations`）の最新実測（`river_observations`）を参照する。

1. 現場に紐付く最寄り・上流観測所の最新実測を取得（`rel=nearest` / `rel=upstream`）
2. 最新2点の水位から rising/stable を判定（差分と時間差から上昇率を計算）
   - 上昇率 0.2m/h 以上で `rising`、それ以外は `stable`（`RIVER_RISING_RATE_M_H`）
3. 上流雨量が閾値 `upstream_rain` 以上なら `upstream_rain` ルールを発火
   （上流観測所が無い場合は最寄り観測所の雨量を補完。`source_river` は提供元ID）
4. 観測が30分（`RIVER_MAX_AGE_SECONDS`）より古い・欠測・品質ERRORなら
   `missing` に `river` を追加しレベル3（確認不能）
5. `source_river` を提供元IDに、理由の `observed_value` を実測値（水位・上昇率・雨量）に

観測所・実測が未設定の河川現場は、手動 `river_state` が無い場合は安全側に「確認不能」とする。
`sites.river_state` は手動入力の後方互換として、自動トレンドが導出できない場合のみ使用する。

## 7. 完了条件（DoD）

### #29 河川観測所マスタ・現場紐付け

- [x] 観測所マスタ CRUD＋一意制約＋無効化
- [x] 現場紐付け（upstream/nearest/reference）と重複・上限制御
- [x] RBAC（書き込み admin/tech_manager、削除 admin）と監査
- [x] デモ自動取得プロバイダ（DEMO-RIVER）とデモ観測所の初期投入
- [ ] 公式自動取得プロバイダの接続（有償契約）
- [ ] 公式観測所マスタの初期データ投入（対象河川・観測所コードの現地調査）

### #31 河川観測取り込み・時系列API・画面

- [x] 実測値の保存・時系列API（手動入力）
- [x] 河川観測画面（全国マップ・デモ自動取得明示・最新値・手動登録）
- [x] 判定エンジンへの実測値組み込み（水位トレンド・上流雨量・STALE→レベル3）
- [x] デモ自動取得ジョブと `data_source_statuses` への反映（DS-RIVER-DEMO）
- [ ] 水位基準線（氾濫注意/避難判断/氾濫危険）の表示
- [ ] 上流雨量の到達時間表示（流域・距離・流速からの概算）

## 8. リスク

- 河川情報センターの有償サービス契約・API仕様の確定が遅れると自動取得の実装が進まない
- 観測所コードは都道府県管理分と国管理分で体系が異なり、マスタ整備に現地調査が必要
- 地点移設・コード変更時の運用ルール（新旧行の扱い）を現場と合意する必要がある
