# 50年確率波 極値解析（デモ・シミュレーション版）設計

## 目的

エピック #72 段7（極値統計による再現期間波高）のデモ実装。NOWPHAS 長期観測データの
蓄積が完了するまで、地点IDから決定的に生成した年最大波高（30年分）へ
Gumbel / Weibull 分布を当てはめ、50年・100年再現期間波高を画面表示する。

## 方針

- **デモであることを常に明示する**（`dataType=synthetic`・警告文・画面注記）。
  設計条件の決定には使用できない。
- 地点ごとに決定的なシミュレーションデータを生成するため、再実行しても結果が変わらない
  （デモ・回帰テストが安定）。
- 実測ベースへの切替時は、同一API応答形のまま NOWPHAS 年最大波高テーブル
  （Neon PostgreSQL）を参照する実装へ差し替える。

## 実装

### backend

- `backend/app/services/extreme.py`
  - `_annual_maxima`: 緯度から想定波浪規模を算出し、決定的な年最大波高30年分を生成
  - Gumbel: モーメント法（位置 loc・尺度 scale）で当てはめ、逆関数で再現期間波高
  - Weibull（2母数）: 確率プロットの線形回帰で shape/scale を推定
  - 当てはまり誤差（RMSE）の小さい方を `primaryMethod` として採用
- `GET /api/marine/return-periods`（認証必須）
  - 応答: `source=DEMO-EXTREME` / `sites[]`（h50・h100・両手法のパラメータ・警告）

### frontend

- `frontend/design/data-adapter.js`
  - メニュー「50年確率波」を正式画面化（準備中表示を撤去）
  - 全国マップ（H50 の階級色: ~3m / 3-5m / 5m~）＋地点別テーブル（H50/H100・手法・
    Gumbel/Weibull パラメータ）＋手法説明・デモ警告を表示

## 完了条件

- [x] Gumbel / Weibull の当てはめと H50/H100 算出（単体テストあり）
- [x] 認証付きAPI `GET /api/marine/return-periods`（APIテストあり）
- [x] 画面・マップ・テーブルへの詳細出力
- [x] デモ・シミュレーションであることの明示
- [ ] NOWPHAS 実測データの蓄積（Neon PostgreSQL）と実測ベースへの切替（#72 段5-7）
