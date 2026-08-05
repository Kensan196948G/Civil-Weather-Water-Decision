# 現場単位権限（role × site × action）基本設計

## 1. 現状と課題

- 現在のRBACは5ロール（admin/tech_manager/site_manager/safety/viewer）のみで、
  `User.site_id` は持つがAPIは使用していない。
- 協力会社が「現場A」しか見られないことを保証できない（外部評価 高リスク）。
- 行レベル権限（RLS）またはアプリ層フィルタの導入が必要。

## 2. 設計方針

### モデル

```text
user_site_access
  id          PK
  user_id     FK users.id
  site_id     FK sites.id
  role        String  # site_viewer / site_editor / site_decision
  granted_by  String
  created_at  String
  一意制約 (user_id, site_id)
```

### 権限マトリクス（role × action）

| action | admin | tech_manager | site_manager | safety | viewer |
| --- | --- | --- | --- | --- | --- |
| 現場・作業予定の閲覧 | 全 | 全 | 自現場のみ | 全（閲覧） | 割当現場のみ |
| 現場の作成・更新 | ✅ | ✅ | — | — | — |
| 作業予定の登録・更新 | ✅ | ✅ | 自現場 | — | — |
| 判定実行・判断記録 | ✅ | ✅ | 自現場 | 自現場 | — |
| 閾値・設定変更 | ✅ | — | — | — | — |
| 監査ログ | ✅ | ✅ | 自現場分のみ（将来） | — | — |

`site_manager` は複数現場を割り当てられる（`user_site_access` 複数行）。
未割当の現場へアクセスすると 403 を返す。

### 実装方針

1. **アプリ層フィルタを第一層**（現行アーキテクチャに最小コストで導入）:
   `get_db` 後の共通依存 `get_accessible_sites(user)` を追加し、サイト系クエリへ
   `Site.id.in_(accessible)` を付与。write は `user_site_access.role` で action 別に判定。
2. **PostgreSQL RLS は将来層**: 直接SQL・バッチ・レポートからの漏れを防ぐため、
   本番安定後に `user_site_access` を使った RLS ポリシーへ移行（アプリ層と二重防御）。
3. 協力会社ユーザーは `viewer`（＋ `site_editor` 上限）とし、会社単位の一括割当機能を
   管理画面へ追加する。

## 3. API追加（案）

| メソッド・パス | 権限 | 内容 |
| --- | --- | --- |
| GET /api/admin/user-site-access | admin | 割当一覧 |
| POST /api/admin/user-site-access | admin | ユーザー×現場×ロール割当 |
| DELETE /api/admin/user-site-access/{id} | admin | 割当解除 |
| GET /api/me/sites | 認証 | 自分がアクセス可能な現場一覧（UI初期化用） |

## 4. 監査と運用

- 割当・解除は監査ログに記録（`user_site_access_grant/revoke`）
- 割当は `(user_id, site_id)` で一意、ロール変更は更新で対応
- 退職・異動時は Entra 連携（`entra-id-oidc.md`）のグループ同期で一括解除

## 5. 完了条件

- [ ] 割当外の現場APIアクセスが403になる（APIテストで担保）
- [ ] 現場一覧・ダッシュボード・判定・判断履歴がアクセス可能現場のみ返る
- [ ] 協力会社ユーザーの受け入れテスト（現場Aユーザーが現場Bを閲覧できない）
- [ ] 割当監査・解除運用が文書化され、Entra グループ同期と接続可能
