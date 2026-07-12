"""設定画面バックエンドAPIのテスト（#80, エピック#72段8）。

検証観点:
- GET 初期状態（AI未設定・データ保存期間の既定値）
- AI APIキーの暗号化保存とマスク表示（平文が応答・監査に漏れないこと）
- データ保存期間の境界検証・未知キー拒否・真偽値拒否
- RBAC（admin限定。非adminは403）
- AI疎通テスト（httpxをモックし ok/401/ネットワーク例外を検証。500にしない）
- AI設定の解除（DELETE）
- 監査の同一トランザクション性（audit_add失敗で設定変更ごとロールバック）
"""
import httpx
import pytest

from tests.conftest import login_token

_SECRET = "sk-ant-secret-abcd1234wxyz"  # テスト専用のダミー鍵（実鍵ではない）


@pytest.fixture(autouse=True)
def _clean_settings():
    """テスト用SQLiteはセッション内で共有されるため、各テスト前後に設定を空へ戻す。"""
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

    _wipe()
    yield
    _wipe()


def _install_fake_httpx(monkeypatch, *, status=200, payload=None, exc=None):
    """httpx.AsyncClient を差し替え、Anthropic 疎通確認をネット非依存にする。"""
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
            return _Resp()

    monkeypatch.setattr(httpx, "AsyncClient", _Client)


# ---------- GET 初期状態 ----------
def test_get_settings_initial_defaults(client):
    r = client.get("/api/admin/settings")
    assert r.status_code == 200
    body = r.json()
    assert body["ai"] == {"configured": False, "masked": None}
    assert body["data_retention_days"] == 365
    assert body["notify"] == {}
    assert body["user_prefs"] == {}


# ---------- AI APIキー 保存・マスク ----------
def test_put_ai_key_saves_and_masks(client):
    r = client.put("/api/admin/settings", json={"ai_api_key": _SECRET})
    assert r.status_code == 200
    assert r.json()["ai"] == {"configured": True, "masked": "****wxyz"}

    got = client.get("/api/admin/settings").json()
    assert got["ai"] == {"configured": True, "masked": "****wxyz"}


def test_ai_key_plaintext_never_in_responses(client):
    put = client.put("/api/admin/settings", json={"ai_api_key": _SECRET})
    get = client.get("/api/admin/settings")
    # 平文キー全体・中間部分は応答本文に現れない（末尾4桁のマスクのみ許容）
    for resp in (put, get):
        assert _SECRET not in resp.text
        assert "abcd1234" not in resp.text


# ---------- データ保存期間 境界・型検証 ----------
def test_put_data_retention_boundaries(client):
    assert client.put("/api/admin/settings", json={"data_retention_days": 30}).status_code == 200
    assert client.put("/api/admin/settings", json={"data_retention_days": 3650}).status_code == 200
    assert client.get("/api/admin/settings").json()["data_retention_days"] == 3650
    # 範囲外
    assert client.put("/api/admin/settings", json={"data_retention_days": 29}).status_code == 422
    assert client.put("/api/admin/settings", json={"data_retention_days": 3651}).status_code == 422


def test_put_rejects_unknown_key(client):
    r = client.put("/api/admin/settings", json={"foobar": 1})
    assert r.status_code == 422


def test_put_data_retention_rejects_boolean(client):
    # JSON真偽値はPydanticのint型で1/0に化けるため境界で明示拒否
    assert client.put("/api/admin/settings",
                      json={"data_retention_days": True}).status_code == 422


def test_put_empty_body_is_422(client):
    assert client.put("/api/admin/settings", json={}).status_code == 422


def test_put_ai_key_empty_is_422(client):
    # 空値での解除は受け付けない（解除は DELETE を使う）
    assert client.put("/api/admin/settings", json={"ai_api_key": "   "}).status_code == 422


# ---------- 通知設定・ユーザー設定 ----------
def test_put_notify_and_user_prefs(client):
    r = client.put("/api/admin/settings", json={
        "notify": {"slack": True, "min_level": 2},
        "user_prefs": {"display_name": "管理者太郎", "theme": "light"},
    })
    assert r.status_code == 200
    got = client.get("/api/admin/settings").json()
    assert got["notify"] == {"slack": True, "min_level": 2}
    assert got["user_prefs"] == {"display_name": "管理者太郎", "theme": "light"}


def test_partial_update_leaves_other_fields(client):
    client.put("/api/admin/settings", json={"data_retention_days": 100})
    client.put("/api/admin/settings", json={"user_prefs": {"lang": "ja"}})
    got = client.get("/api/admin/settings").json()
    assert got["data_retention_days"] == 100  # 別フィールドの更新で消えない
    assert got["user_prefs"] == {"lang": "ja"}


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


def test_ai_test_no_key_configured(client, monkeypatch):
    _install_fake_httpx(monkeypatch, status=200)
    r = client.post("/api/admin/settings/ai/test")  # 未設定・body無し
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is False
    assert "設定されて" in body["error"]


# ---------- AI設定 解除（DELETE） ----------
def test_delete_ai_key(client):
    client.put("/api/admin/settings", json={"ai_api_key": _SECRET})
    assert client.get("/api/admin/settings").json()["ai"]["configured"] is True
    r = client.delete("/api/admin/settings/ai")
    assert r.status_code == 200
    assert r.json()["ai"] == {"configured": False, "masked": None}
    assert client.get("/api/admin/settings").json()["ai"]["configured"] is False


def test_delete_ai_key_is_idempotent(client):
    # 未設定でもエラーにしない
    r = client.delete("/api/admin/settings/ai")
    assert r.status_code == 200
    assert r.json()["ai"]["configured"] is False


# ---------- 監査 ----------
def test_settings_update_audited_without_secret(client):
    client.put("/api/admin/settings", json={"ai_api_key": _SECRET, "data_retention_days": 400})
    rows = client.get("/api/admin/audit-logs").json()
    updates = [x for x in rows if x["action"] == "settings_update"]
    assert updates, "settings_update が監査に記録される"
    # 監査メッセージに平文キーが載らない（キー名＋末尾4桁マスクのみ）
    for x in rows:
        msg = x.get("message") or ""
        assert _SECRET not in msg
        assert "abcd1234" not in msg


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
