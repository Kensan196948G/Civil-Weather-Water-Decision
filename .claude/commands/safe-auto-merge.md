# /safe-auto-merge

GitHub Token を利用して open PR を安全に処理するコマンドです。

このコマンドを選んだら、次の方針で進めてください。

- main/default branch 宛 PR は、必ず人間へ「マージしますか？ [y/N]」を確認する。
- main 以外の branch 宛 PR は、CI・review・mergeability・危険ファイル gate をすべて通過した場合のみ自動マージする。
- `GITHUB_TOKEN` / `GH_TOKEN` は `gh` CLI にだけ使い、値を表示・保存しない。
- force push、history rewrite、直接 push はしない。

実行手順:

1. `gh auth status` と `gh repo view --json defaultBranchRef` を確認する。
2. `gh pr list --state open` で対象 PR を列挙する。
3. 各 PR の `baseRefName`, `isDraft`, `mergeable`, `mergeStateStatus`, `reviewDecision`, `statusCheckRollup`, `files` を確認する。
4. main/default branch 宛は要約を提示して人間確認を待つ。
5. main 以外は次をすべて満たす場合のみ `gh pr merge <number> --squash --delete-branch` を実行する。

自動マージ gate:

- `isDraft=false`
- `mergeable=MERGEABLE`
- `mergeStateStatus=CLEAN`
- `reviewDecision` が `REVIEW_REQUIRED` / `CHANGES_REQUESTED` ではない
- status checks に失敗・未完了・取消がない
- Critical / High 指摘が残っていない
- 認証・認可、secrets、DB migration/schema、本番 deploy、`.github/workflows/`、branch protection 変更を含まない

最後に `merged`, `auto-merge enabled`, `skipped`, `needs-human` に分類して報告してください。
