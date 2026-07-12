# 🔧 systemd 常駐化（適用済みユニットの正本）

📌 このディレクトリの `*.service` は **2026-07-12 に実機 `/etc/systemd/system/` へ適用済み**の実体コピー（Issue #77）。
汎用テンプレートは `deploy/*.service.example` を参照。実機とこのディレクトリの内容がズレたら本ディレクトリを更新すること。

## 📋 構成

| ユニット | 役割 | ポート/接続先 |
|---|---|---|
| `cwwd-backend.service` | FastAPI/uvicorn（Neon PostgreSQL 接続） | `0.0.0.0:55019` |
| `cwwd-frontend.service` | ClaudeDesign 静的サーバ（`serve.py`、`PORT` で固定） | `0.0.0.0:34979` |
| `cwwd-tunnel.service` | cloudflared（`~/.cloudflared/config-cwwd.yml`） | `https://cwwd.mirai-dx-platform.com` |

- 依存順序: `cwwd-tunnel` は `cwwd-backend` / `cwwd-frontend` に `After=` / `Wants=`（OS 起動時に3点セットで自動起動）
- バインドは `0.0.0.0` のため、DHCP で LAN IP が変わってもポート番号は不変（例: `http://<LAN-IP>:34979/?api=http://<LAN-IP>:55019`）

## 🚀 適用手順（更新時）

```bash
sudo cp deploy/systemd/cwwd-*.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now cwwd-backend cwwd-frontend cwwd-tunnel
```

## ✅ 検証（2026-07-12 実測）

```bash
systemctl is-active cwwd-backend cwwd-frontend cwwd-tunnel   # → active ×3
curl -s http://127.0.0.1:55019/health                        # → {"status":"ok",...}
curl -s -o /dev/null -w "%{http_code}" http://<LAN-IP>:34979/ # → 200
curl -s -o /dev/null -w "%{http_code}" https://cwwd.mirai-dx-platform.com/  # → 302 (Cloudflare Access ログインへ)
```

⚠️ 公開 URL の 302 は Cloudflare Access（アプリ `cwwd` / ポリシー `CWWD`）による正常なエッジ防御。
許可メンバーのみログイン後にアプリへ到達できる。

## 🔁 運用メモ

- ログ確認: `journalctl -u cwwd-backend -f`（frontend / tunnel も同様）
- ポート変更時: unit と `~/.cloudflared/config-cwwd.yml` の **両方** を更新（`--config` 明示必須の罠は `deploy/cloudflared-setup-steps.md` 参照）
- backend は `backend/.env` を `EnvironmentFile` として読む（`DATABASE_URL`=Neon。本番ハードニングは `docs/deploy.md` チェックリスト参照）
