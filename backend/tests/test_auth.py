"""認証・RBAC・監査ログのテスト（設計§12/§13）。"""
from datetime import datetime, timedelta, timezone
import time

import jwt
from sqlalchemy import select
from conftest import login_token
from app.api import auth as auth_api
from app.core.config import settings
from app.core.db import SessionLocal
from app.core.security import decode_token
from app.models import AuditLog, LoginAttempt, RevokedToken


def _attempt_key(username: str, ip: str = "testclient") -> str:
    return auth_api._login_attempt_key(username, ip)[0]


def test_login_ok(client):
    r = client.post("/api/auth/login", json={"username": "admin", "password": "admin123"})
    assert r.status_code == 200
    assert r.json()["user"]["role"] == "admin" and r.json()["token"]
    assert decode_token(r.json()["token"])["jti"]


def test_login_token_contains_unique_jti(client):
    a = client.post("/api/auth/login", json={"username": "viewer", "password": "pass1234"}).json()["token"]
    b = client.post("/api/auth/login", json={"username": "viewer", "password": "pass1234"}).json()["token"]
    pa, pb = decode_token(a), decode_token(b)

    assert pa["sub"] == pb["sub"] == "U05"
    assert pa["jti"] and pb["jti"] and pa["jti"] != pb["jti"]
    assert pa["exp"] and pb["exp"]


def test_login_bad_password(client):
    assert client.post("/api/auth/login",
                       json={"username": "admin", "password": "wrong"}).status_code == 401


def test_requires_auth(client):
    # 無効トークン → 401（既定の管理者ヘッダを上書き）
    assert client.get("/api/sites", headers={"Authorization": "Bearer invalid"}).status_code == 401


def test_viewer_can_read(client):
    tok = login_token(client, "viewer")
    r = client.get("/api/dashboard/site-risk", headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 200


def test_viewer_cannot_create_site(client):
    tok = login_token(client, "viewer")
    r = client.post("/api/sites", headers={"Authorization": f"Bearer {tok}"},
                    json={"name": "不可テスト", "latitude": 35, "longitude": 139, "work_type": "crane"})
    assert r.status_code == 403


def test_site_manager_records_but_cannot_create_site(client):
    tok = login_token(client, "yamada")
    h = {"Authorization": f"Bearer {tok}"}
    # 判断メモは可
    assert client.post("/api/decision-logs", headers=h,
                       json={"site_id": "S01", "work_type": "河川内作業",
                             "action": "monitor", "comment": "現場管理者の記録"}).status_code == 200
    # 現場登録は不可（管理者/技術管理者のみ）
    assert client.post("/api/sites", headers=h,
                       json={"name": "x", "latitude": 35, "longitude": 139,
                             "work_type": "crane"}).status_code == 403


def test_audit_logged(client):
    # 管理者で現場登録 → 監査ログに記録される
    client.post("/api/sites", json={"name": "監査テスト現場", "latitude": 35.4,
                                     "longitude": 139.4, "work_type": "crane"})
    logs = client.get("/api/admin/audit-logs").json()
    actions = {row["action"] for row in logs}
    assert "site_create" in actions
    assert "login" in actions


def test_viewer_cannot_view_audit(client):
    tok = login_token(client, "viewer")
    assert client.get("/api/admin/audit-logs",
                      headers={"Authorization": f"Bearer {tok}"}).status_code == 403


def test_decision_log_records_authenticated_user(client):
    # 記録者はクライアント指定でなく認証ユーザーから導出される（なりすまし防止 #8）
    tok = login_token(client, "yamada")
    r = client.post("/api/decision-logs", headers={"Authorization": f"Bearer {tok}"},
                    json={"site_id": "S01", "work_type": "河川内作業", "action": "monitor",
                          "comment": "本人記録", "decided_by": "なりすまし太郎"})
    assert r.status_code == 200
    lid = r.json()["id"]
    logs = client.get("/api/decision-logs").json()
    entry = next(x for x in logs if x["id"] == lid)
    assert "山田" in entry["by"] and "なりすまし" not in entry["by"]


def test_login_lockout(client):
    # 同一ユーザー名+IP で連続失敗するとロック（試行回数制限 #4）
    for _ in range(5):
        client.post("/api/auth/login", json={"username": "attacker", "password": "x"})
    r = client.post("/api/auth/login", json={"username": "attacker", "password": "x"})
    assert r.status_code == 429
    with SessionLocal() as db:
        row = db.get(LoginAttempt, _attempt_key("attacker"))
        assert row.fail_count == 5
        assert row.locked_until and row.locked_until > time.time()


def test_login_lockout_is_per_client_ip(client):
    headers_a = {"CF-Connecting-IP": "203.0.113.10"}
    headers_b = {"CF-Connecting-IP": "203.0.113.11"}
    for _ in range(5):
        client.post("/api/auth/login", json={"username": "admin", "password": "wrong"},
                    headers=headers_a)

    assert client.post("/api/auth/login", json={"username": "admin", "password": "admin123"},
                       headers=headers_a).status_code == 429
    assert client.post("/api/auth/login", json={"username": "admin", "password": "admin123"},
                       headers=headers_b).status_code == 200


def test_successful_login_clears_login_attempt_row(client):
    headers = {"CF-Connecting-IP": "203.0.113.20"}
    for _ in range(2):
        client.post("/api/auth/login", json={"username": "viewer", "password": "wrong"},
                    headers=headers)

    key = _attempt_key("viewer", "203.0.113.20")
    with SessionLocal() as db:
        assert db.get(LoginAttempt, key).fail_count == 2

    r = client.post("/api/auth/login", json={"username": "viewer", "password": "pass1234"},
                    headers=headers)
    assert r.status_code == 200
    with SessionLocal() as db:
        assert db.get(LoginAttempt, key) is None


def test_expired_login_lock_resets_failure_window(client, monkeypatch):
    base = 1_783_900_000.0
    key, username, ip_hash = auth_api._login_attempt_key("expired-lock", "203.0.113.30")
    with SessionLocal() as db:
        db.add(LoginAttempt(key=key, username=username, ip_hash=ip_hash, fail_count=5,
                            first_failed_at=base, last_failed_at=base,
                            locked_until=base + auth_api._LOCK_SECONDS,
                            updated_at=base))
        db.commit()

    monkeypatch.setattr(auth_api, "_now_ts", lambda: base + auth_api._LOCK_SECONDS + 1)
    r = client.post("/api/auth/login", json={"username": "expired-lock", "password": "wrong"},
                    headers={"CF-Connecting-IP": "203.0.113.30"})
    assert r.status_code == 401
    with SessionLocal() as db:
        row = db.get(LoginAttempt, key)
        assert row.fail_count == 1
        assert row.locked_until is None


def test_login_failure_attempt_and_audit_persist_together(client):
    before = time.time()
    r = client.post("/api/auth/login", json={"username": "audit-lock", "password": "wrong"})
    assert r.status_code == 401
    with SessionLocal() as db:
        row = db.get(LoginAttempt, _attempt_key("audit-lock"))
        audit = db.scalar(select(AuditLog)
                          .where(AuditLog.action == "login_failed")
                          .where(AuditLog.message == "username=audit-lock")
                          .order_by(AuditLog.id.desc()))
        assert row and row.fail_count == 1 and row.updated_at >= before
        assert audit is not None


def test_logout_revokes_current_token(client):
    r = client.post("/api/auth/login", json={"username": "admin", "password": "admin123"})
    tok = r.json()["token"]
    h = {"Authorization": f"Bearer {tok}"}

    assert client.get("/api/auth/me", headers=h).status_code == 200
    out = client.post("/api/auth/logout", headers=h)
    assert out.status_code == 200 and out.json()["revoked"] is True
    assert client.get("/api/auth/me", headers=h).status_code == 401


def test_logout_does_not_revoke_other_sessions(client):
    a = client.post("/api/auth/login", json={"username": "viewer", "password": "pass1234"}).json()["token"]
    b = client.post("/api/auth/login", json={"username": "viewer", "password": "pass1234"}).json()["token"]

    assert client.post("/api/auth/logout", headers={"Authorization": f"Bearer {a}"}).status_code == 200
    assert client.get("/api/auth/me", headers={"Authorization": f"Bearer {a}"}).status_code == 401
    assert client.get("/api/auth/me", headers={"Authorization": f"Bearer {b}"}).status_code == 200


def test_logout_persists_revocation_row(client):
    tok = client.post("/api/auth/login", json={"username": "viewer", "password": "pass1234"}).json()["token"]
    payload = decode_token(tok)

    assert client.post("/api/auth/logout",
                       headers={"Authorization": f"Bearer {tok}"}).status_code == 200

    with SessionLocal() as db:
        row = db.get(RevokedToken, payload["jti"])
        assert row.user_id == payload["sub"]
        assert row.reason == "logout"
        assert row.expires_at == payload["exp"]


def test_revoked_jti_is_rejected_by_dependency(client):
    tok = client.post("/api/auth/login", json={"username": "viewer", "password": "pass1234"}).json()["token"]
    payload = decode_token(tok)
    with SessionLocal() as db:
        db.add(RevokedToken(jti=payload["jti"], user_id=payload["sub"],
                            revoked_at=time.time(), expires_at=payload["exp"],
                            reason="test"))
        db.commit()

    assert client.get("/api/auth/me", headers={"Authorization": f"Bearer {tok}"}).status_code == 401


def test_token_without_jti_is_rejected(client):
    now = datetime.now(timezone.utc)
    token = jwt.encode({
        "sub": "U05",
        "role": "viewer",
        "iat": now,
        "exp": now + timedelta(minutes=5),
    }, settings.jwt_secret, algorithm="HS256")

    assert client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"}).status_code == 401


def test_logout_is_idempotent_for_already_revoked_token(client):
    tok = client.post("/api/auth/login", json={"username": "viewer", "password": "pass1234"}).json()["token"]
    h = {"Authorization": f"Bearer {tok}"}

    assert client.post("/api/auth/logout", headers=h).status_code == 200
    assert client.post("/api/auth/logout", headers=h).status_code == 200
