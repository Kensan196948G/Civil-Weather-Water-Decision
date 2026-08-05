"""#118: Entra ID OIDC（Authorization Code + PKCE）のテスト。

- OIDC 有効/無効ステータス
- authorize → Entra へ302＋state/nonce/PKCE クッキー
- callback → コード交換・id_token検証・auto-provision・アプリJWT発行
- state 不一致・OIDC無効時の拒否
- AUTH_MODE=oidc 時のアプリ内ログイン無効化
- グループ→ロールマッピング
"""
import hashlib

from sqlalchemy import delete, func, select

from app.core.config import settings
from app.core.db import SessionLocal
from app.models import AuditLog, RevokedToken, User
from app.services import oidc as oidc_mod


def _max_audit_id() -> int:
    db = SessionLocal()
    try:
        return db.scalar(select(func.max(AuditLog.id))) or 0
    finally:
        db.close()


def _cleanup(uid: str, baseline_audit: int) -> None:
    db = SessionLocal()
    try:
        db.execute(delete(RevokedToken).where(RevokedToken.user_id == uid))
        db.execute(delete(User).where(User.id == uid))
        db.execute(delete(AuditLog).where(AuditLog.id > baseline_audit))
        db.commit()
    finally:
        db.close()


def _enable_oidc(monkeypatch):
    monkeypatch.setattr(settings, "auth_mode", "oidc")
    monkeypatch.setattr(settings, "oidc_issuer_url", "https://login.example/tenant/v2.0")
    monkeypatch.setattr(settings, "oidc_client_id", "client-test")
    monkeypatch.setattr(settings, "oidc_client_secret", "secret-test")
    monkeypatch.setattr(settings, "oidc_group_role_site_manager", "grp-site")


def test_group_mapping(monkeypatch):
    monkeypatch.setattr(settings, "oidc_group_role_site_manager", "grp-site")
    assert oidc_mod.map_groups_to_role(["grp-site"]) == "site_manager"
    assert oidc_mod.map_groups_to_role(["unknown-group"]) == "viewer"


def test_status_disabled_by_default(client):
    body = client.get("/api/auth/oidc/status").json()
    assert body["enabled"] is False and body["mode"] == "app"


def test_authorize_and_callback_flow(client, monkeypatch):
    baseline = _max_audit_id()
    _enable_oidc(monkeypatch)

    async def fake_discovery(c):
        return {"issuer": "https://login.example/tenant/v2.0",
                "authorization_endpoint": "https://login.example/authorize",
                "token_endpoint": "https://login.example/token",
                "jwks_uri": "https://login.example/jwks"}

    async def fake_exchange(code, verifier, redirect_uri, c):
        assert code == "auth-code-1"
        return {"id_token": "id-token-test"}

    async def fake_verify(id_token, nonce, c):
        assert id_token == "id-token-test"
        return {"email": "oidc.user@example.com", "name": "OIDC 太郎",
                "groups": ["grp-site"], "nonce": nonce}

    monkeypatch.setattr(oidc_mod, "discovery", fake_discovery)
    monkeypatch.setattr(oidc_mod, "exchange_code", fake_exchange)
    monkeypatch.setattr(oidc_mod, "verify_id_token", fake_verify)

    try:
        r = client.get("/api/auth/oidc/authorize?redirect=/", follow_redirects=False)
        assert r.status_code == 302
        assert r.headers["location"].startswith("https://login.example/authorize?")
        assert "code_challenge=" in r.headers["location"]
        assert "nonce=" in r.headers["location"]
        cookie = r.cookies.get("oidc_state")
        assert cookie
        payload = oidc_mod.read_state_token(cookie)

        r2 = client.get(
            f"/api/auth/oidc/callback?code=auth-code-1&state={payload['state']}",
            follow_redirects=False)
        assert r2.status_code == 302, r2.text
        loc = r2.headers["location"]
        assert "#oidc_token=" in loc
        app_token = loc.split("#oidc_token=", 1)[1]
        assert app_token

        me = client.get("/api/auth/me",
                        headers={"Authorization": f"Bearer {app_token}"}).json()
        assert me["username"] == "oidc.user@example.com"
        assert me["role"] == "site_manager"
    finally:
        _cleanup("OIDC-" + hashlib.sha256(
            "oidc.user@example.com".encode("utf-8")).hexdigest()[:8], baseline)


def test_callback_rejects_wrong_state(client, monkeypatch):
    baseline = _max_audit_id()
    _enable_oidc(monkeypatch)
    monkeypatch.setattr(oidc_mod, "discovery",
                        lambda c: _fake_discovery())
    try:
        r = client.get("/api/auth/oidc/authorize?redirect=/", follow_redirects=False)
        assert r.status_code == 302
        r2 = client.get("/api/auth/oidc/callback?code=x&state=wrong-state",
                        follow_redirects=False)
        assert r2.status_code == 403
    finally:
        _cleanup("", baseline)


def test_oidc_disabled_endpoints_404(client, monkeypatch):
    monkeypatch.setattr(settings, "auth_mode", "app")
    assert client.get("/api/auth/oidc/authorize", follow_redirects=False).status_code == 404
    assert client.get("/api/auth/oidc/callback?code=x&state=y",
                      follow_redirects=False).status_code == 404


def test_app_login_disabled_in_oidc_mode(client, monkeypatch):
    _enable_oidc(monkeypatch)
    r = client.post("/api/auth/login", json={"username": "admin", "password": "admin123"})
    assert r.status_code == 403


async def _fake_discovery(c=None):
    return {"issuer": "https://login.example/tenant/v2.0",
            "authorization_endpoint": "https://login.example/authorize",
            "token_endpoint": "https://login.example/token",
            "jwks_uri": "https://login.example/jwks"}
