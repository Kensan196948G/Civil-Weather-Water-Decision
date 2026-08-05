# 河川観測アーキテクチャ設計（#29 T2-01 / #31 T2-03）

## 1. 目的と現状

プロジェクト名の中核である「河川・水位」の自動取得を、段階的に実装するための設計。

2026-08 時点の到達点:

- ✅ 観測所マスタ（`observation_stations`）
- ✅ 現場との多対多紐付け（`site_stations`。上流/最寄り/参照 の区分）
- ✅ 実測値の保存・時系列API（`river_observations`。手動入力）
- ✅ API応答とUIで「自動取得は未接続」を明示
- ❌ 自動取得プロバイダ（水防災オープンデータ提供サービス等）の接続
- ❌ 判定エンジンへの実測値組み込み（`water_level_trend` の自動導出）

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

## 6. 判定エンジンへの組み込み（将来実装）

現在は `sites.river_state`（手動）を `water_level_trend` へ渡している。自動取得導入後は:

1. 現場に紐付く最寄り・上流観測所の最新実測を取得
2. 最新2点の水位から rising/stable を判定（差分と時間差から上昇率を計算）
3. 上流雨量が閾値 `upstream_rain` 以上なら upstream_rain ルールを発火
4. 観測が閾値時間（例: 30分）より古い・欠測なら `missing` に river を追加しレベル3
5. `source_river` を提供元IDに、理由の `observed_value` を実測値にする

## 7. 完了条件（DoD）

### #29 河川観測所マスタ・現場紐付け

- [x] 観測所マスタ CRUD＋一意制約＋無効化
- [x] 現場紐付け（upstream/nearest/reference）と重複・上限制御
- [x] RBAC（書き込み admin/tech_manager、削除 admin）と監査
- [ ] 自動取得プロバイダの接続
- [ ] 観測所マスタの初期データ投入（対象河川・観測所コードの現地調査）

### #31 河川観測取り込み・時系列API・画面

- [x] 実測値の保存・時系列API（手動入力）
- [x] 河川観測画面（自動取得未接続の明示＋手動登録）
- [ ] 自動取得ジョブと `data_source_statuses` への反映
- [ ] 判定エンジンへの実測値組み込み（水位トレンド・上流雨量・STALE→レベル3）
- [ ] 水位基準線（氾濫注意/避難判断/氾濫危険）の表示
- [ ] 上流雨量の到達時間表示（流域・距離・流速からの概算）

## 8. リスク

- 河川情報センターの有償サービス契約・API仕様の確定が遅れると自動取得の実装が進まない
- 観測所コードは都道府県管理分と国管理分で体系が異なり、マスタ整備に現地調査が必要
- 地点移設・コード変更時の運用ルール（新旧行の扱い）を現場と合意する必要がある
