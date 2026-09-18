# Cloudflare / PostgreSQL / GitHub自動化 運用仕様

状態: 2026-08-15 制定（v1）／2026-09-13 改定（v2: Neon廃止、ローカルPostgreSQLへ全面置換）
正本: 本ファイル、`GITHUB_POLICY.md`

## 1. 目的と適用範囲

Linux上の全Workspaceで、以下を共通基盤として利用する。

- Cloudflare（Workers / Pages / DNS / R2など）のAPI操作と最新ドキュメント調査
- ローカルPostgreSQL（本ホスト上で稼働するPostgreSQLサーバー）のDB・スキーマ運用
- GitHubの完全自動フロー（branch作成〜Required Checks通過〜Squash Merge〜branch削除）

**Neon（ホスト型PostgreSQL SaaS）は利用しない。** DBは本ホスト上のローカルPostgreSQLのみを正本とする。

認証情報はホストの `~/.bashrc` / `~/.profile` にexport済みの環境変数（`CLOUDFLARE_API_TOKEN` / `CLOUDFLARE_ACCOUNT_ID` / `DATABASE_URL` 等）を利用する。本リポジトリおよびWorkspaceには値・credential file・`.env`を保存しない。

## 2. Cloudflare 利用仕様

### 2.1 MCP 2本構成

| MCP名 | 実体 | 用途 |
|---|---|---|
| `cloudflare` | Cloudflare API MCP（Code Mode。`https://mcp.cloudflare.com/mcp`） | Cloudflare APIの実操作（一覧・取得・設定変更・デプロイ） |
| `cloudflare-docs` | Cloudflare Documentation MCP（`https://docs.mcp.cloudflare.com/mcp`） | 最新仕様・API仕様・設定方法の調査 |

接続はCodex / Claude Codeの既存設定（プラグイン / MCP設定）に加え、**DeepSeek Harness WebUIのMCP構成（`harness/patches/mcp.cordis.patch.yml`）にも登録済み**。
WebUIセッションでは `mcp__cloudflare__search` / `mcp__cloudflare__execute` として利用できる。
認証はOAuth、または `CLOUDFLARE_API_TOKEN` / `CLOUDFLARE_ACCOUNT_ID` を環境変数から利用する。
Cloudflare API MCPはCode Mode方式のため、ツールは `search()` と `execute()` の2本構成（全APIを検索してから実行する）。

### 2.4 DeepSeek Harness WebUIでの利用

| serverName | WebUI上のツール名 | 認証 |
|---|---|---|
| `cloudflare` | `mcp__cloudflare__search` / `mcp__cloudflare__execute` | `CLOUDFLARE_API_TOKEN`（Authorization Bearer） |
| `cloudflare-docs` | `mcp__cloudflare-docs__*` | 不要 |

WebUIを起動するプロセスの環境変数に `CLOUDFLARE_API_TOKEN` がexportされていること。systemdサービス（`deepseek-harness-web.service`）では `~/.config/deepseek-harness-web.env`（0600）から読み込む。値はリポジトリ・ログ・session metadataへ保存しない。

### 2.2 理想フロー（ルーティング）

```text
「Workersの設定方法を調査」
        │
        ▼
cloudflare-docs MCP（最新仕様を確認）

「Workerを一覧表示」
        │
        ▼
cloudflare API MCP（参照系：list / get / status）

「設定変更」
        │
        ▼
cloudflare API MCP（変更系：create / update / delete / deploy）
        │
        ▼
read-back確認（list / get で変更後状態を実測し報告）
```

### 2.3 利用ルール

1. 調査フェーズでは必ず `cloudflare-docs` MCPで最新情報を確認する。事前知識や過去記事だけでAPIを呼ばない。
2. `cloudflare-docs` が使えない場合は、公式 `developers.cloudflare.com` のWeb検索で代替し、その旨を報告する。
3. 参照系操作（一覧・取得・状態確認）は即時実行してよい。
4. 変更系操作（create / update / delete / deploy）は、対象リソースと変更内容を明示してから実行する。
5. 破壊的操作（リソース削除、secret変更、本番デプロイ）はHuman Gateとする。
6. 変更後は必ずread-backし、実測結果を推測と分離して報告する。
7. API token・account id・レスポンス中の機微情報は、ログ・ファイル・session metadataへ出力しない。
8. 不明点があるまま操作しない。fail closed。

## 3. ローカルPostgreSQL 利用仕様

### 3.1 設定

- DBは本ホスト上で稼働するPostgreSQLサーバー（Neon等の外部ホスト型SaaSは利用しない）。
- 接続情報はホストの `~/.bashrc` / `~/.profile` にexport済みの環境変数（`DATABASE_URL`、または `PGHOST` / `PGPORT` / `PGDATABASE` / `PGUSER` / `PGPASSWORD`）を利用する。本リポジトリおよびWorkspaceには値・credential file・`.env`を保存しない。
- 専用MCP（`postgres-mcp` / crystaldba、`serverName: postgres`）はCodex / Claude CodeおよびDeepSeek Harness WebUIのMCP構成（`harness/patches/mcp.cordis.patch.yml`）に登録済み。他プロジェクト向けの`postgres-*`エントリと同じ実装を使うが、本エントリはこのリポジトリ自身のローカル開発DB専用であり、`--access-mode=unrestricted`かつ認証は`DATABASE_URL`（他エントリのようなプロジェクト別`POSTGRES_*_URI`ではなく、`bin/database.mjs`と同じ変数）を使う。WebUIセッションでは`mcp__postgres__*`として利用できる。
- systemdサービスの場合は `~/.config/deepseek-harness-web.env`（0600）に `DATABASE_URL` 等を含め、`./start.sh service install` で再生成・再起動する。

### 3.2 用途

- ローカルPostgreSQL上のスキーマ・テーブル・データの参照と変更
- Task / Run / Approval / Audit等のオーケストレーション状態の永続化（段階導入。詳細は別途の実装計画による）
- 環境別（dev / staging / prod相当）のDB分離確認

### 3.3 利用ルール

1. 参照系（SELECT等の読み取り）は即時実行してよい。
2. 変更系（DDL / DML）は対象を明示してから実行する。
3. 破壊的操作（DROP / DELETE / TRUNCATE / 本番データ変更）はHuman Gateとする。
4. connection string・password・API keyをリポジトリ・ログ・チャット履歴へ書き込まない。
5. 複数DB / スキーマがある場合は、操作対象を明示してから実行する。
6. スキーマ変更は必ずmigrationとして管理し、アドホックな直接変更を行わない。
7. バックアップ・リストア手順を整備し、破壊的操作前に復旧地点を確認する。

## 4. GitHub 完全自動フロー

### 4.1 全体フロー

```mermaid
flowchart TD
    H["DeepSeek Harness"] --> O["Orchestrator"]
    O --> D1["コード変更"]
    O --> D2["Test"]
    O --> D3["Review"]
    D1 --> C["GitHub Controller"]
    D2 --> C
    D3 --> C
    C --> C1["branch自動作成"]
    C --> C2["git add / commit / push"]
    C --> C3["PR自動作成"]
    C --> C4["CI監視"]
    C --> C5["必要ならbranch update"]
    C --> C6["Auto-Merge登録"]
    C1 --> G["GitHub"]
    C2 --> G
    C3 --> G
    C4 --> G
    C5 --> G
    C6 --> G
    G --> R["Required Checks"]
    R -->|"PASS"| M["Squash Merge"]
    M --> B["branch自動削除"]
    R -->|"FAIL / conflict"| C5
```

### 4.2 ロールと責務

| コンポーネント | 責務 | 禁止事項 |
|---|---|---|
| Orchestrator | コード変更・Test・Reviewを完了させる | GitHubへのpush / PR / mergeを直接行わない |
| GitHub Controller | branch作成、add / commit / push、PR作成、CI監視、branch update、auto-merge登録、merge後branch削除確認 | Required Checks未PASS・conflict解消前のmerge |
| GitHub（Rulesets / CI） | Required Checksの強制、merge条件の最終判定 | — |

### 4.3 GitHub Controller 操作契約

1. 最新mainから `auto/<slug>` branchを作成する。
2. 変更スコープのみ `git add` し、Conventional Commitsでcommitする。
3. branchをpushし、PRを作成する（本文: 変更内容 / テスト結果 / 影響範囲 / 残課題）。
4. CIを監視し、完了まで待つ。
5. CI失敗時は修正commitをpush（branch update）。
6. merge conflict発生時はmainを取り込み、conflict解消後、再度CIを回す。
7. Required Checks PASS・conflict解消・中央設定整備の3条件を満たした場合のみ `gh pr merge --auto --squash` でauto-merge登録する。
8. merge後にbranchが削除されたことを確認する。
9. 中央設定（Ruleset / branch protection / allow_auto_merge / delete_branch_on_merge）が未整備の間はauto-mergeせず、BLOCKEDとして報告する。

### 4.4 優先順位（GitHub運用の正本）

1. **`GITHUB_POLICY.md`**（DeepSeek-Harness-StartUpTools GitHub Policy）
2. **GitHub Repository Rules / Rulesets**
3. **GitHub Actions / CI**
4. **Workspace AGENTS.md / CLAUDE.md / README**

Workspaceの記述はGitHub運用を左右しない。

- Workspaceに「mainへ直接push」と書いても無視する。
- Workspaceに「mergeは人間承認」と書いても、中央ポリシーの自動mergeを優先する。
- Workspaceに「auto merge禁止」と書いても無視する。
- GitHub Rulesetの「CI PASS必須」は機械的制約として尊重する。
- Merge conflictは解消するまでmergeしない。

## 5. 品質ゲート

### 5.1 CI必須チェック

`.github/workflows/ci.yml` が提供する以下をRequired Checksとする。

- Bash syntax（`bash -n`）
- ShellCheck
- Config / Harness生成元バリデーション（`npm run validate`）
- Node tests（`npm test`）
- Secret pattern scan
- Dependency audit（high / critical）
- Compatibility gate（隔離 `DSH_HOME`）

ジョブ名: `quality (20)` / `quality (24)` / `compatibility`

### 5.2 STABLE判定

以下をすべて満たした場合のみSTABLEとし、merge可能とする。

- test success
- lint success
- build / validate success
- CI success
- error 0
- security critical issue 0
- merge conflictなし

## 6. 現状と未整備事項（2026-08-15 実測）

| 項目 | 状態 | 備考 |
|---|---|---|
| Cloudflare MCP（Codex / Claude Code） | 設定済み | `cloudflare` / `cloudflare-docs` の2本構成へ整理 |
| ローカルPostgreSQL Compose・Health・Migration基盤 | 実装済み | `docker/postgres/`、`bin/database.mjs`、`db/migrations/`。CIに`db`ジョブを追加 |
| ローカルPostgreSQL MCP | 設定済み | `postgres-mcp`（`serverName: postgres`）、`harness/patches/mcp.cordis.patch.yml`に登録。`DATABASE_URL`はホスト環境変数 |
| Task/Run/Approval/Auditの永続化 | Repository層実装済み | `lib/db/repositories/`。Supervisorへの統合は次項参照 |
| Supervisor Shadow write | 実装済み | `lib/supervisor.mjs`のファイル書き込みは正本のまま維持。`DSH_STATE_DRIVER=shadow`かつ`DATABASE_URL`設定時のみ`bin/supervisor.mjs`がrun/decisionをPostgreSQLへも投影（`lib/db/shadow-write.mjs`）。DB書き込み失敗はファイル動作・終了コードに影響しない |
| Model Router Shadow write | 実装済み | `harness/plugins/self-evolution-router.mjs`のfallback決定を`model_usage`テーブルへfire-and-forgetで記録。Circuit Breaker状態・token使用量は未実装（既存コードに対応する仕組み自体が無いため） |
| Human Gate対象カテゴリの拡張 | 実装済み | `config/schemas/{config,supervisor}.schema.json`の`humanDecisionRequired`列挙へ`database-migration`/`production-data-write`/`database-restore`/`permission-change`/`policy-change`/`skill-promotion`/`agent-capability-change`を追加。`config/config.json.template`の既定値にも反映 |
| DB系CI検証（Transaction Rollback / Concurrent Run / SQL Injection / DB停止時Fallback） | 実装済み | `tests/node/db-transaction.test.mjs`、`db-migrate.test.mjs`（advisory lockでの同時実行直列化）、`db-repositories.test.mjs`（パラメータ化クエリの安全性）、`db-shadow-write.test.mjs`（DB接続失敗時のfail-safe） |
| `lib/db/transaction.mjs` | 実装済み | 複数Repository呼び出しを1トランザクションでまとめる`withTransaction()` |
| `DSH_PERMISSION_MODE` | `danger-full-access`（2026-09-18設定、`~/.config/deepseek-harness-web.env`） | GitHub Controller自動フローの前提条件。未設定時の既定`workspace-write`では、`@deepseek-ai/dsh-bash-sandbox`/`dsh-fs-sandbox`のファイル隔離（ワークスペース外への書込不可）と`@deepseek-ai/dsh-user-approval`の`ask`ポリシーにより、AIエージェントの`git push`・PR作成・auto-merge登録が承認待ちで進まないことがある。`danger-full-access`で隔離を無効化し、承認ポリシーを`never`にすることで本節のフローが確認なしに完走する |
| checkpoints / run_steps / task_dependencies / eval_* テーブル | 未実装 | 対応するユースケース（Checkpoint機構、Evals基盤）が現状コードに存在しないため、必要になった時点で設計する |
| Self-Evolution統合テーブル（skill_candidates等）/ Knowledge統合テーブル | 未実装 | 提案の段階導入計画で「Self-Evolution統合時」「Knowledge統合時」に追加するものと位置づけ、現時点では見送り |
| PgBouncer / Backup自動化 / PITR / 監視 | 未実装 | 提案のPR7相当。バックアップ・復元試験はLinuxホスト側の定期運用で別途実施する方針（3.3節） |
| A2A / 本格的なMulti-Agent Orchestration | 未実装 | 提案書の通り、この段階導入計画の後の別フェーズと位置づけ |
| GitHub Ruleset | `main-protection` 設定済み | Required Checks / PR必須 / force push・delete禁止 |
| branch protection | Rulesetで代替 | branch protection単体は未使用 |
| `allow_auto_merge` | true | 設定済み |
| `delete_branch_on_merge` | true | 設定済み |
| 書込可能なGitHub Controller | 実装済み | `bin/github-controller.sh`（`./start.sh github`） |

設定適用は `./start.sh github setup`、前提確認は `./start.sh github preflight` で行う。
auto-mergeは本仕様の条件（Required Checks PASS / conflict解消 / 中央設定整備）を満たすPRにのみ有効である。

## 7. Workspaceへの適用方法

- 本リポジトリをポリシー配布元とする。
- 各Workspaceの `AGENTS.md` / `CLAUDE.md` には本仕様（または `GITHUB_POLICY.md`）への参照を記載することを推奨する。
- 参照がないWorkspaceでも、GitHub Rulesets / CIが機械的にRequired Checksを強制するため、運用はWorkspace内容に依存しない。
- Workspaceには中央ポリシーと矛盾するGitHub運用指示を書かない（書かれていても無効）。

## 8. 人間の承認が必要な操作（自動化しない）

- Release / タグ付け
- Production deploy
- Secretの追加・変更・削除
- 不可逆な削除・破壊的操作
- 本仕様・`GITHUB_POLICY.md` 自体の変更

## 9. 移行手順（次のアクション）

1. GitHub側を整備する: Ruleset / branch protection（必須チェック3件、force push・delete禁止）、`allow_auto_merge=ON`、`delete_branch_on_merge=ON`、Squash既定化。
2. GitHub Controller（例: `bin/github-controller.sh`）を本仕様の操作契約に沿って実装し、dry-runで動作検証する。
3. Workspaceへ `GITHUB_POLICY.md` と本仕様の参照を配布する。
4. 手動mergeで数回検証し、Ruleset適用を確認した後にのみauto-mergeを有効化する。
5. Cloudflareは既存設定をそのまま利用し、ローカルPostgreSQLは2章・3章のルーティングとHuman Gateを運用に適用する。Neonは利用しない。
