"""設定画面バックエンドAPIのテスト（#80, エピック#72段8 + 対抗レビュー対応）。

検証観点:
- GET 初期状態（AI未設定・通知既定・データ保存期間の既定値）
- AI APIキーの暗号化保存とマスク表示（平文が応答・監査に漏れないこと）
- 暗号鍵の強度ガード（弱鍵では保存拒否／専用鍵があれば許可。#80 high-2）
- 検証エラー(422)応答が投入値（秘密値）を反射しないこと（#80 high-1）
- notify/user_prefs の厳格スキーマ（未知キー・不正型・dotted は422。#80 medium-1）
- データ保存期間の境界・型検証（int型そのもの以外は拒否。#80 low-2）
- RBAC（admin限定。非adminは403）
- AI疎通テスト（httpxをモックし ok/401/ネットワーク例外を検証。500にしない）
- AI疎通テストのレート制限・監査（#80 medium-2）
- AI設定の解除（DELETE）
- 監査の同一トランザクション性・秘密/マスク非記載（#80 low-1）
"""
import httpx
import pytest

from tests.conftest import login_token

_SECRET = "sk-ant-secret-abcd1234wxyz"  # テスト専用のダミー鍵（実鍵ではない）


@pytest.fixture(autouse=True)
def _clean_settings():
    """テスト用SQLiteはセッション内で共有されるため、各テスト前後に設定を空へ戻す。
    ai/test のプロセス内レート制限状態もリセットし、テスト間の持ち越しを防ぐ。"""
    from sqlalchemy import delete
    from sqlalchemy.exc import OperationalError

    from app.core.db import SessionLocal
    from app.models import AppSetting

    def _wipe():
        try:
            with SessionLocal() as db:
                db.execute(delete(AppSetting))
                db.commit()
        except OperationalError:
            pass  # 初回client起動前でテーブル未作成なら何もしない
        # ai/test レート制限（プロセス内メモリ）をリセット
        from app.api import routes as routes_mod
        routes_mod._ai_test_calls.clear()

    _wipe()
    yield
    _wipe()


def _install_fake_httpx(monkeypatch, *, status=200, payload=None, exc=None):
    """httpx.AsyncClient を差し替え、AI疎通確認をネット非依存にする。

    最後のリクエスト（url / headers）は _fake_ai_calls に記録し、DeepSeek 既定と
    Anthropic 指定の認証方式を検証できるようにする。
    """
    _fake_ai_calls.clear()

    class _Resp:
        status_code = status

        def json(self):
            return {"data": []} if payload is None else payload

    class _Client:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc_info):
            return False

        async def get(self, url, headers=None):
            if exc is not None:
                raise exc
            _fake_ai_calls.append({"url": url, "headers": dict(headers or {})})
            return _Resp()

    monkeypatch.setattr(httpx, "AsyncClient", _Client)


_fake_ai_calls: list[dict] = []


# ---------- GET 初期状態 ----------
def test_get_settings_initial_defaults(client):
    r = client.get("/api/admin/settings")
    assert r.status_code == 200
    body = r.json()
    assert body["ai"] == {"configured": False, "masked": None, "provider": "deepseek"}
    assert body["data_retention_days"] == 365
    # notify は常に2フラグを bool で返す（未設定時は既定 false/false）
    assert body["notify"] == {"slack_enabled": False, "teams_enabled": False}
    assert body["user_prefs"] == {}


# ---------- AI APIキー 保存・マスク ----------
def test_put_ai_key_saves_and_masks(client):
    r = client.put("/api/admin/settings", json={"ai_api_key": _SECRET})
    assert r.status_code == 200
    assert r.json()["ai"] == {"configured": True, "masked": "****wxyz",
                              "provider": "deepseek"}

    got = client.get("/api/admin/settings").json()
    assert got["ai"] == {"configured": True, "masked": "****wxyz", "provider": "deepseek"}


def test_ai_key_plaintext_never_in_responses(client):
    put = client.put("/api/admin/settings", json={"ai_api_key": _SECRET})
    get = client.get("/api/admin/settings")
    # 平文キー全体・中間部分は応答本文に現れない（末尾4桁のマスクのみ許容）
    for resp in (put, get):
        assert _SECRET not in resp.text
        assert "abcd1234" not in resp.text


# ---------- 暗号鍵の強度ガード（#80 high-2） ----------
def test_put_ai_key_rejected_when_encryption_weak(client, monkeypatch):
    from app.core import crypto
    from app.core.config import _DEFAULT_JWT_SECRET

    # 専用鍵なし + JWT_SECRET 既定 → 実用強度なし → 保存拒否
    monkeypatch.setattr(crypto.settings, "settings_encryption_key", "")
    monkeypatch.setattr(crypto.settings, "jwt_secret", _DEFAULT_JWT_SECRET)
    assert crypto.encryption_is_strong() is False

    r = client.put("/api/admin/settings", json={"ai_api_key": _SECRET})
    assert r.status_code == 422
    assert _SECRET not in r.text  # メッセージに秘密値を含まない
    assert "SETTINGS_ENCRYPTION_KEY" in (r.json().get("detail") or "")


def test_put_ai_key_allowed_with_dedicated_key(client, monkeypatch):
    from app.core import crypto
    from app.core.config import _DEFAULT_JWT_SECRET

    # JWT_SECRET が弱くても、専用鍵 SETTINGS_ENCRYPTION_KEY があれば保存できる
    monkeypatch.setattr(crypto.settings, "jwt_secret", _DEFAULT_JWT_SECRET)
    assert crypto.encryption_is_strong() is True
    r = client.put("/api/admin/settings", json={"ai_api_key": _SECRET})
    assert r.status_code == 200
    assert r.json()["ai"]["configured"] is True


# ---------- 検証エラー応答の秘密値非反射（#80 high-1） ----------
def test_validation_error_no_secret_reflection_put(client):
    leak = "sk-ant-SECRETLEAK-zzzz9999"
    # 誤ったキー名で秘密値を送っても、422応答に投入値は反射されない
    r = client.put("/api/admin/settings", json={"ai_api_key_typo": leak})
    assert r.status_code == 422
    assert leak not in r.text


def test_validation_error_no_secret_reflection_ai_test(client):
    leak = "sk-ant-SECRETLEAK-zzzz9999"
    # api_key に想定外の型（dict）で秘密値を紛れ込ませても反射されない
    r = client.post("/api/admin/settings/ai/test", json={"api_key": {"nested": leak}})
    assert r.status_code == 422
    assert leak not in r.text


# ---------- データ保存期間 境界・型検証 ----------
def test_put_data_retention_boundaries(client):
    assert client.put("/api/admin/settings", json={"data_retention_days": 30}).status_code == 200
    assert client.put("/api/admin/settings", json={"data_retention_days": 3650}).status_code == 200
    assert client.get("/api/admin/settings").json()["data_retention_days"] == 3650
    # 範囲外
    assert client.put("/api/admin/settings", json={"data_retention_days": 29}).status_code == 422
    assert client.put("/api/admin/settings", json={"data_retention_days": 3651}).status_code == 422


def test_put_data_retention_rejects_non_int_types(client):
    # int型そのもの以外は拒否（真偽値 / 文字列"100" / 浮動小数100.0）。#80 low-2
    assert client.put("/api/admin/settings", json={"data_retention_days": True}).status_code == 422
    assert client.put("/api/admin/settings", json={"data_retention_days": "100"}).status_code == 422
    assert client.put("/api/admin/settings", json={"data_retention_days": 100.0}).status_code == 422


def test_put_rejects_unknown_key(client):
    r = client.put("/api/admin/settings", json={"foobar": 1})
    assert r.status_code == 422


def test_put_empty_body_is_422(client):
    assert client.put("/api/admin/settings", json={}).status_code == 422


def test_put_ai_key_empty_is_422(client):
    # 空値での解除は受け付けない（解除は DELETE を使う）
    assert client.put("/api/admin/settings", json={"ai_api_key": "   "}).status_code == 422


# ---------- 通知設定（厳格スキーマ・nested・#80 medium-1） ----------
def test_put_notify_nested(client):
    r = client.put("/api/admin/settings",
                   json={"notify": {"slack_enabled": True, "teams_enabled": False}})
    assert r.status_code == 200
    assert r.json()["notify"] == {"slack_enabled": True, "teams_enabled": False}
    got = client.get("/api/admin/settings").json()
    assert got["notify"] == {"slack_enabled": True, "teams_enabled": False}


def test_put_notify_rejects_unknown_subkey(client):
    # notify は slack_enabled/teams_enabled のみ（extra=forbid）
    assert client.put("/api/admin/settings",
                      json={"notify": {"bogus": True}}).status_code == 422


def test_put_notify_rejects_non_bool(client):
    # bool へ強制できない値（配列）は 422
    assert client.put("/api/admin/settings",
                      json={"notify": {"slack_enabled": [1, 2]}}).status_code == 422


def test_put_rejects_dotted_keys(client):
    # 契約は nested。トップレベルの dotted key は extra=forbid で 422
    assert client.put("/api/admin/settings",
                      json={"notify.slack_enabled": True}).status_code == 422


# ---------- ユーザー設定（現時点で {} のみ受理・#80 medium-1） ----------
def test_put_user_prefs_empty_ok_and_rejects_keys(client):
    assert client.put("/api/admin/settings", json={"user_prefs": {}}).status_code == 200
    assert client.get("/api/admin/settings").json()["user_prefs"] == {}
    # 許可キーなし → 任意キーは 422（生 dict 保存＝保存型XSSの入口を作らない）
    assert client.put("/api/admin/settings",
                      json={"user_prefs": {"theme": "dark"}}).status_code == 422


def test_put_matches_frontend_general_save_payload(client):
    # フロント cwSaveAppSettingsGeneral の送信形（notify nested + data_retention_days）を検証
    r = client.put("/api/admin/settings", json={
        "notify": {"slack_enabled": True, "teams_enabled": False},
        "data_retention_days": 400,
    })
    assert r.status_code == 200
    body = r.json()
    assert body["notify"] == {"slack_enabled": True, "teams_enabled": False}
    assert body["data_retention_days"] == 400
    assert "ai" in body  # PUT 応答は GET と同形（res.body.ai をフロントが参照する）


def test_partial_update_leaves_other_fields(client):
    client.put("/api/admin/settings", json={"data_retention_days": 100})
    client.put("/api/admin/settings",
               json={"notify": {"slack_enabled": True, "teams_enabled": True}})
    got = client.get("/api/admin/settings").json()
    assert got["data_retention_days"] == 100  # 別フィールドの更新で消えない
    assert got["notify"] == {"slack_enabled": True, "teams_enabled": True}


# ---------- RBAC（admin限定） ----------
def test_settings_require_admin(client):
    for username in ("yamada", "tanaka"):  # site_manager / tech_manager いずれも不可
        token = login_token(client, username)
        h = {"Authorization": "Bearer " + token}
        assert client.get("/api/admin/settings", headers=h).status_code == 403
        assert client.put("/api/admin/settings", headers=h,
                          json={"data_retention_days": 100}).status_code == 403
        assert client.post("/api/admin/settings/ai/test", headers=h).status_code == 403
        assert client.delete("/api/admin/settings/ai", headers=h).status_code == 403


# ---------- AI 疎通テスト ----------
def test_ai_test_ok_with_body_key(client, monkeypatch):
    _install_fake_httpx(monkeypatch, status=200, payload={"data": [
        {"id": "claude-opus-4-8"}, {"id": "claude-sonnet-5"},
        {"id": "claude-haiku-4-5"}, {"id": "claude-extra"},
    ]})
    r = client.post("/api/admin/settings/ai/test", json={"api_key": _SECRET})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["models"] == ["claude-opus-4-8", "claude-sonnet-5", "claude-haiku-4-5"]


def test_ai_test_defaults_to_deepseek(client, monkeypatch):
    """既定プロバイダは DeepSeek（#72 段8: Claude→DeepSeek 変更指示）。"""
    _install_fake_httpx(monkeypatch, status=200, payload={"data": [
        {"id": "deepseek-chat"}, {"id": "deepseek-reasoner"}]})
    r = client.post("/api/admin/settings/ai/test", json={"api_key": _SECRET})
    assert r.status_code == 200
    assert r.json()["ok"] is True
    assert r.json()["models"] == ["deepseek-chat", "deepseek-reasoner"]
    assert _fake_ai_calls, "外部疎通が実行される"
    assert _fake_ai_calls[-1]["url"] == "https://api.deepseek.com/models"
    assert _fake_ai_calls[-1]["headers"].get("Authorization") == "Bearer " + _SECRET


def test_ai_test_provider_anthropic_uses_x_api_key(client, monkeypatch):
    """provider=anthropic 指定時は従来どおり x-api-key + anthropic-version で疎通。"""
    _install_fake_httpx(monkeypatch, status=200, payload={"data": [{"id": "claude-x"}]})
    r = client.post("/api/admin/settings/ai/test",
                    json={"api_key": _SECRET, "provider": "anthropic"})
    assert r.status_code == 200
    assert r.json()["ok"] is True
    assert _fake_ai_calls[-1]["url"] == "https://api.anthropic.com/v1/models"
    assert _fake_ai_calls[-1]["headers"].get("x-api-key") == _SECRET


def test_ai_provider_persisted_and_validated(client):
    r = client.put("/api/admin/settings", json={"ai_provider": "anthropic"})
    assert r.status_code == 200
    assert r.json()["ai"]["provider"] == "anthropic"
    assert client.get("/api/admin/settings").json()["ai"]["provider"] == "anthropic"
    # 未知プロバイダは422
    assert client.put("/api/admin/settings",
                      json={"ai_provider": "openai"}).status_code == 422


def test_ai_test_401_returns_ok_false(client, monkeypatch):
    _install_fake_httpx(monkeypatch, status=401)
    r = client.post("/api/admin/settings/ai/test", json={"api_key": "bad-key"})
    assert r.status_code == 200  # HTTP自体は成功。判定は本文の ok で返す
    body = r.json()
    assert body["ok"] is False
    assert "認証失敗" in body["error"]


def test_ai_test_network_error_returns_ok_false(client, monkeypatch):
    _install_fake_httpx(monkeypatch, exc=httpx.ConnectError("simulated network failure"))
    r = client.post("/api/admin/settings/ai/test", json={"api_key": _SECRET})
    assert r.status_code == 200  # ネットワーク例外でも500にしない
    assert r.json()["ok"] is False


def test_ai_test_uses_stored_key_when_no_body(client, monkeypatch):
    client.put("/api/admin/settings", json={"ai_api_key": _SECRET})
    _install_fake_httpx(monkeypatch, status=200, payload={"data": [{"id": "claude-opus-4-8"}]})
    r = client.post("/api/admin/settings/ai/test")  # body無し → 保存済みキーを使用
    assert r.status_code == 200
    assert r.json() == {"ok": True, "models": ["claude-opus-4-8"]}
    assert _fake_ai_calls[-1]["url"] == "https://api.deepseek.com/models"


def test_ai_test_no_key_configured(client, monkeypatch):
    _install_fake_httpx(monkeypatch, status=200)
    r = client.post("/api/admin/settings/ai/test")  # 未設定・body無し
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is False
    assert "未設定" in body["error"]


def test_ai_test_rate_limited(client, monkeypatch):
    # ユーザー単位 5回/分。6回目は外部呼び出しせず ok:false を返す（#80 medium-2）
    _install_fake_httpx(monkeypatch, status=200, payload={"data": [{"id": "claude-x"}]})
    for _ in range(5):
        assert client.post("/api/admin/settings/ai/test",
                           json={"api_key": _SECRET}).json()["ok"] is True
    sixth = client.post("/api/admin/settings/ai/test", json={"api_key": _SECRET})
    assert sixth.status_code == 200
    body = sixth.json()
    assert body["ok"] is False
    assert "試行回数" in body["error"]


def test_ai_test_is_audited(client, monkeypatch):
    _install_fake_httpx(monkeypatch, status=200, payload={"data": [{"id": "claude-x"}]})
    client.post("/api/admin/settings/ai/test", json={"api_key": _SECRET})
    rows = client.get("/api/admin/audit-logs").json()
    assert any(x["action"] == "ai_key_test" for x in rows), "ai_key_test が監査に記録される"
    # 監査メッセージに鍵値・マスクが載らない
    for x in rows:
        msg = x.get("message") or ""
        assert _SECRET not in msg
        assert "abcd1234" not in msg


# ---------- AI設定 解除（DELETE） ----------
def test_delete_ai_key(client):
    client.put("/api/admin/settings", json={"ai_api_key": _SECRET})
    assert client.get("/api/admin/settings").json()["ai"]["configured"] is True
    r = client.delete("/api/admin/settings/ai")
    assert r.status_code == 200
    assert r.json()["ok"] is True
    assert r.json()["ai"] == {"configured": False, "masked": None, "provider": "deepseek"}
    assert client.get("/api/admin/settings").json()["ai"]["configured"] is False


def test_delete_ai_key_is_idempotent(client):
    # 未設定でもエラーにしない
    r = client.delete("/api/admin/settings/ai")
    assert r.status_code == 200
    assert r.json()["ok"] is True
    assert r.json()["ai"] == {"configured": False, "masked": None, "provider": "deepseek"}


# ---------- 監査 ----------
def test_settings_update_audited_without_secret(client):
    client.put("/api/admin/settings", json={"ai_api_key": _SECRET, "data_retention_days": 400})
    rows = client.get("/api/admin/audit-logs").json()
    updates = [x for x in rows if x["action"] == "settings_update"]
    assert updates, "settings_update が監査に記録される"
    # 監査メッセージに平文キー・末尾4桁マスクいずれも載らない（#80 low-1）
    for x in rows:
        msg = x.get("message") or ""
        assert _SECRET not in msg
        assert "abcd1234" not in msg
        assert "wxyz" not in msg  # マスク末尾4桁も出さない


def test_ai_key_removed_audited(client):
    client.put("/api/admin/settings", json={"ai_api_key": _SECRET})
    client.delete("/api/admin/settings/ai")
    rows = client.get("/api/admin/audit-logs").json()
    assert any(x["action"] == "ai_key_removed" for x in rows)


def test_settings_change_rolls_back_when_audit_fails(client, monkeypatch):
    """監査行の書き込みが失敗したら設定変更ごとロールバック（監査なき変更を残さない）。"""
    from app.api import routes as routes_mod

    def _boom(*a, **kw):
        raise RuntimeError("audit insert failed")

    monkeypatch.setattr(routes_mod, "audit_add", _boom)
    # TestClient は未処理例外を再送出する。commit前に失敗する点が本質
    with pytest.raises(RuntimeError):
        client.put("/api/admin/settings", json={"data_retention_days": 111})
    monkeypatch.undo()

    # 監査失敗時は設定変更が残らない（既定値のまま）
    assert client.get("/api/admin/settings").json()["data_retention_days"] == 365
