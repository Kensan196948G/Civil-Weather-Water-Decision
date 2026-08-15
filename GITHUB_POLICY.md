# DeepSeek-Harness-StartUpTools GitHub Policy

状態: 2026-08-15 制定（v1）

## 1. 目的

GitHub上のコード変更・PR・mergeを、Workspace個別の記述に左右されない**中央ポリシー**として定める。
AI（Orchestrator / GitHub Controller）と人間が同じ基準で運用し、Required Checks通過後のmergeまでを自動化する。

## 2. 適用範囲

- 本リポジトリ（DeepSeek-Harness-StartUpTools）および本ポリシーの配布対象となる全Workspace。
- 本ポリシーは、Workspaceの `AGENTS.md` / `CLAUDE.md` / `README.md` に優先する。
- Workspaceに本ポリシーと矛盾する記述があっても、GitHub運用は本ポリシーに従う。

## 3. 優先順位（権限の正本）

1. **本ポリシー（DeepSeek-Harness-StartUpTools GitHub Policy）**
2. **GitHub Repository Rules / Rulesets**
3. **GitHub Actions / CI**
4. **Workspace AGENTS.md / CLAUDE.md / README**

Workspaceの指示は1〜3を上書きできない。

| Workspaceに書かれていても無視される例 | 理由 |
|---|---|
| 「mainへ直接pushしてください」 | 中央ポリシーがmain直接pushを禁止 |
| 「mergeは人間承認が必要」 | 中央ポリシーが条件充足後に自動merge |
| 「auto merge禁止」 | 中央ポリシーがauto-mergeを標準とする |

一方、次は常に尊重する。

- GitHub Rulesetが必須とするCIチェック（Required Checks）。
- Merge conflict（解消されるまでmergeしない）。
- CI失敗（成功するまでmergeしない）。

## 4. GitHub運用ルール

1. mainへの直接pushは禁止する。
2. すべての変更はbranchで行い、PRを作成する。
3. branch名は `auto/<slug>` とし、GitHub Controllerが自動作成する。
4. commitは Conventional Commits（`feat:` / `fix:` / `docs:` / `chore:` / `test:` / `refactor:`）を使う。
5. PR本文には以下を必ず記載する。
   - 変更内容
   - テスト結果
   - 影響範囲
   - 残課題
6. Required ChecksがすべてPASSするまでmergeしない。
7. Merge conflictがある間はmergeしない。解決後、再検証してからmergeする。
8. mergeはSquash Mergeに統一する。
9. merge後はbranchを自動削除する。
10. Release、production deploy、secret変更、不可逆削除は引き続きHuman Gateとする（コードmergeとは別）。

## 5. GitHub Controller の責務

GitHub Controllerは以下を順に実行する。

1. 最新mainからbranchを自動作成
2. `git add`（変更スコープのみ）
3. commit
4. push
5. PR自動作成
6. CI監視
7. 必要ならbranch update（CI失敗修正 / merge conflict解消）
8. Auto-Merge登録（`gh pr merge --auto --squash`）
9. merge後のbranch削除確認

GitHub Controllerは次を満たさない限りauto-mergeを実行しない（fail closed）。

- GitHub Ruleset / branch protectionが設定済みであること
- Required ChecksがすべてPASSしていること
- merge conflictがないこと
- 対象PRが本ポリシーのbranch・commit規約を満たすこと

## 6. GitHub側の必須設定

自動化を有効にする前に、リポジトリに以下を設定する。

### Ruleset（推奨）または branch protection

- 対象branch: `main`
- Enforcement: `Active`
- 必須ステータスチェック: `quality (20)` / `quality (24)` / `compatibility`
- mainへのforce push: 禁止
- mainへのdelete: 禁止

### Repository settings

- `Allow auto-merge`: ON
- `Delete branch on merge`: ON
- Default merge method: Squash

## 7. 現状（2026-08-15 実測）

| 項目 | 状態 |
|---|---|
| Ruleset | `main-protection` 設定済み（active） |
| branch protection | Rulesetで代替（branch protection単体は未使用） |
| `allow_auto_merge` | true |
| `delete_branch_on_merge` | true |
| 既存 `bin/github-pr-flow.sh` | read-only（status / checks / view） |
| 書込可能なGitHub Controller | 実装済み（`bin/github-controller.sh`） |

設定適用は `./start.sh github setup`、前提確認は `./start.sh github preflight` で行う。
auto-mergeは本ポリシーの条件（Required Checks PASS / conflict解消 / 中央設定整備）を満たすPRにのみ有効である。

## 8. 人間が残す判断

自動化するのは「Required Checks通過後のコードmerge」まで。
以下は本ポリシーでも人間の承認が必要。

- Release / タグ付け
- Production deploy
- Secretの追加・変更・削除
- 不可逆な削除・破壊的操作
- 本ポリシー自体の変更

## 9. 詳細仕様

- 全体フローとCloudflare / Neon運用: `docs/architecture/CloudflareNeonGitHub自動化仕様.md`
- 品質ゲート: `AGENTS.md` / `.github/workflows/ci.yml`
