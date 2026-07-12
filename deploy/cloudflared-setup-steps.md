# Cloudflare Tunnel セットアップ手順（人間実行）

対象: `cwwd.mirai-dx-platform.com`（Civil-Weather-Water-Decision）

CTO（Claude Code）はこの手順を**代行実行しません**。`docs/deploy.md`「公開ドメイン」節および
CLAUDE.md 8.6節の人間決裁境界により、Tunnel作成・DNSルーティングは人間実行と定義されているためです。
このファイルは実行者向けのコピペ手順書です。

## 0. 前提確認

- `cloudflared tunnel list` は認証済み・実行確認済み（2026-07-12時点で8トンネル確認、`cwwd` 系は未作成）。
- 兄弟プロジェクトの実態は `docs/deploy.md` が記す「Tunnel 1本+複数サブドメイン」ではなく、
  プロジェクトごとの**個別Tunnel**（例: `arcsphere-civil-twin`, `ocsrc-riskchecker`）。
  本手順もこの実態に合わせ、**cwwd 専用の新規Tunnel作成**を前提とする。
  既存の共有Tunnelに相乗りする方針に変える場合は、該当Tunnelの `credentials-file` と
  ingress設定を把握した上で手順1・4を読み替えること。

### 0.1 Cloudflare API による再確認（読み取り専用、2026-07-12）

Cloudflare API MCP（`cloudflare-api` プラグイン）でも上記をダブルチェック済み。以降の手順で
参照する実値は以下の通り（Tunnel作成・DNS routing自体は引き続き人間実行）:

| 項目 | 値 |
|---|---|
| Zone名 | `mirai-dx-platform.com` |
| Zone ID | `e375e651e49a40801a305b89e297bff0` |
| 既存Tunnel数 | 8件（名前衝突なし。`cwwd*` は0件） |
| 既存DNSレコード数 | 10件、全て `<subdomain>.mirai-dx-platform.com` → `<tunnel-id>.cfargotunnel.com` のCNAME |
| DNSレコードの `proxied` | 全件 `true`（オレンジクラウド）。新規作成分も揃えること（`cloudflared tunnel route dns` は既定でこの設定になる） |
| `cwwd.mirai-dx-platform.com` の既存レコード | なし（名前衝突なし確認済み） |

Zone IDはAPI経由で直接DNSレコードを確認・トラブルシュートする際に使う
（例: `GET /zones/e375e651e49a40801a305b89e297bff0/dns_records`）。手順4のCLIコマンドは
Zone IDを暗黙的に解決するため、通常は指定不要。

## 1. Tunnel作成

```bash
cloudflared tunnel create cwwd-civil-weather-water
```

- 出力される `<TUNNEL_ID>` と credentials ファイルパス（既定 `~/.cloudflared/<TUNNEL_ID>.json`）を控える。

## 2. backend / frontend のポート固定

現状は両サーバーとも起動のたびに空きポートが動的採番される（衝突回避のための意図的な設計。
`backend/README.md` 参照）。systemd 常駐化する場合は固定ポートに切り替える。

- backend: `python3 -m uvicorn app.main:app --host 127.0.0.1 --port <BACKEND_PORT>`
- frontend: `PORT=<FRONTEND_PORT> python3 serve.py`

他プロジェクトのポート使用状況と衝突しない値を選ぶこと（8000はCivilPDF-DX使用中で使用不可、
本セッションでは backend=55019 / frontend=34979 が一時的に空いていたがこれは固定割当ではない）。

## 3. cloudflared-config.yml の本番化

```bash
cp deploy/cloudflared-config.yml.example ~/.cloudflared/config-cwwd.yml
# <TUNNEL_ID> / <service-user> / <BACKEND_PORT> / <FRONTEND_PORT> を実値に置換
```

## 4. DNSルーティング

```bash
cloudflared tunnel route dns cwwd-civil-weather-water cwwd.mirai-dx-platform.com
```

## 5. 動作確認（フォアグラウンド一時起動）

```bash
cloudflared tunnel --config ~/.cloudflared/config-cwwd.yml run cwwd-civil-weather-water
# 別ターミナルから
curl -I https://cwwd.mirai-dx-platform.com/
curl -I https://cwwd.mirai-dx-platform.com/api/health   # パス分割する場合
```

問題なければ Ctrl+C で停止し、常駐化へ進む。

## 6. systemd 常駐化

```bash
# backend / frontend
sudo cp deploy/civil-weather-water-backend.service.example \
    /etc/systemd/system/civil-weather-water-backend.service
sudo cp deploy/civil-weather-water-frontend.service.example \
    /etc/systemd/system/civil-weather-water-frontend.service
# ファイル内の <service-user> / <BACKEND_PORT> / <FRONTEND_PORT> を置換してから
sudo systemctl daemon-reload
sudo systemctl enable --now civil-weather-water-backend
sudo systemctl enable --now civil-weather-water-frontend

# cloudflared 自体もサービス化する場合
sudo cloudflared --config ~/.cloudflared/config-cwwd.yml service install
sudo systemctl enable --now cloudflared
```

## 7. 本番切替の残チェック（docs/deploy.md 参照）

Tunnel公開前に、backend の `.env` を本番値へ（`APP_ENV=production` にすると
`core/config.py` の `_guard_production` バリデータが未設定を起動時エラーで弾く設計）:

- [ ] `APP_ENV=production`
- [ ] `JWT_SECRET` 32バイト以上のランダム値
- [ ] `ENABLE_AUTH=true`
- [ ] `ADMIN_PASSWORD` 設定（local限定のデモユーザーは投入されなくなる）
- [ ] `DATABASE_URL` を PostgreSQL に
- [ ] `CORS_ORIGINS=https://cwwd.mirai-dx-platform.com`（`*` から変更）
- [ ] Codex 対抗レビュー実施済み（認証/認可・DBスキーマ変更を含む場合。CLAUDE.md必須）

## 8. 完了後

`state.json` の `project.deploy_plan.provisioned` を `true` に、`public_url` を追記。
（この更新はCTOが実施可能 — ファイル内容の記録のみで実運用への影響がないため）
