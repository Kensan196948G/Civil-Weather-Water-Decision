# /design-sync-check

Claude Design に同期する前に、現在のリポジトリのデザインシステム準備状況を点検するコマンドです。

実行方針:

- ファイルを変更せず、まず監査レポートを出す。
- UI 実装前なら `/design-sync` の前提が揃っているか確認する。
- UI 実装後なら design system 準拠、visual regression、accessibility の検証観点を出す。

確認項目:

- design tokens
  - color palette
  - typography scale
  - spacing scale
  - radius / shadow / elevation
  - CSS variables または theme 定義
- component library
  - button / input / select / modal / navigation / card / table
  - variant / size / state / disabled / loading
  - reuse すべき既存 component
- styling system
  - Tailwind config
  - CSS Modules / global CSS / design token files
  - UI framework dependency
- design references
  - Storybook / component catalog
  - screenshots / visual snapshots
  - existing high-quality pages
- verification
  - lint / typecheck / build
  - unit / component tests
  - Playwright or screenshot checks
  - accessibility checks

出力形式:

```text
Design Sync Readiness
- Status: ready | partial | blocked
- Design system sources:
- Reusable components:
- Missing or weak areas:
- Recommended /design-sync scope:
- Implementation guardrails:
- Verification commands:
```

不足がある場合は、`/design-sync` 実行前に必要最小限の改善案を提示してください。
