# /safe-auto-merge

GitHub Token を利用して open PR を安全に処理するコマンドです。

このコマンドを選んだら、次の方針で進めてください。

- PR は `gh pr merge --auto --squash` で自動マージを予約する。マージの条件は Required Checks の全成功と merge conflict がないことだけとし、人間の Y/N・選択・Approve を待たない（main/default branch 宛も同じ）。`--admin` による迂回は禁止する。
- Release・本番デプロイ・秘密情報の変更・不可逆な削除は、コードのマージとは別に Human Gate とする（正本: 中央ポリシー `GITHUB_POLICY.md` v2）。
- `GITHUB_TOKEN` / `GH_TOKEN` は `gh` CLI にだけ使い、値を表示・保存しない。
- force push、history rewrite、直接 push はしない。

実行手順:

1. `gh auth status` と `gh repo view --json defaultBranchRef` を確認する。
2. `gh pr list --state open` で対象 PR を列挙する。
3. 各 PR の `baseRefName`, `isDraft`, `mergeable`, `mergeStateStatus`, `statusCheckRollup`, `headRefOid` を確認する。
4. Draft でなく merge conflict がない PR は `gh pr merge <number> --auto --squash --delete-branch` で自動マージを予約する（Required Checks の全成功後に GitHub がマージする）。
5. 予約できない場合（既に CLEAN で即時マージ可能など）は、`gh pr checks <number>` で必須チェックの全成功を確認し、確認時点の head SHA を指定して `gh pr merge <number> --squash --delete-branch --match-head-commit <sha>` を実行する。

自動マージ条件:

- `isDraft=false`
- merge conflict がない（`mergeable=MERGEABLE`）
- Required Checks がすべて成功（失敗・取消があれば原因を修正して再 push し、マージしない）

最後に `merged`, `auto-merge enabled`, `skipped`, `failed-checks` に分類して報告してください。
