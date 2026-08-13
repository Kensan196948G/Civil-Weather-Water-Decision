"""ユーザー管理API（/api/admin/users）の検証。

管理者専用RBAC、作成/更新/無効化/削除、自己ロックアウト防止、
最後の有効な管理者の保護、パスワード・email・ロールのバリデーション、
監査記録を確認する。共有テストDBを汚さないよう、作成したユーザーと
テスト中に増えた監査行はクリーンアップする。
"""
from __future__ import annotations

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.api import routes as routes_mod
from app.core.db import SessionLocal
from app.models import AuditLog, User


def _auth(client, username, password="pass1234"):
    r = client.post("/api/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


def _cleanup_users(usernames, baseline_audit: int) -> None:
    db = SessionLocal()
    try:
        for row in db.scalars(select(User).where(User.username.in_(usernames))):
            db.delete(row)
        for row in db.scalars(select(AuditLog).where(AuditLog.id > baseline_audit)):
            db.delete(row)
        db.commit()
    finally:
        db.close()


def _max_audit_id() -> int:
    db = SessionLocal()
    try:
        from sqlalchemy import func
        return db.scalar(select(func.max(AuditLog.id))) or 0
    finally:
        db.close()


def test_list_users_admin_only(client):
    h = _auth(client, "admin", "admin123")
    r = client.get("/api/admin/users", headers=h)
    assert r.status_code == 200
    usernames = {u["username"] for u in r.json()}
    assert {"admin", "tanaka", "yamada", "takahashi", "viewer"} <= usernames
    roles = {u["role"] for u in r.json()}
    assert roles == {"admin", "tech_manager", "site_manager", "safety", "viewer"}
    # 非管理者は全操作403
    for username in ("tanaka", "yamada", "takahashi", "viewer"):
        h2 = _auth(client, username)
        assert client.get("/api/admin/users", headers=h2).status_code == 403
        assert client.post("/api/admin/users", json={
            "username": "x", "display_name": "X", "password": "pass1234"}, headers=h2
        ).status_code == 403
        assert client.put("/api/admin/users/U02", json={"role": "viewer"},
                          headers=h2).status_code == 403
        assert client.delete("/api/admin/users/U02", headers=h2).status_code == 403


def test_create_user_full_cycle(client):
    baseline = _max_audit_id()
    h = _auth(client, "admin", "admin123")
    payload = {
        "username": "demo.suzuki2", "display_name": "鈴木 デモ（現場管理者）",
        "email": "suzuki.demo@example.com", "role": "site_manager",
        "department": "現場", "password": "demo-pass-123",
    }
    try:
        r = client.post("/api/admin/users", json=payload, headers=h)
        assert r.status_code == 201, r.text
        uid = r.json()["id"]
        listed = {u["id"]: u for u in client.get("/api/admin/users", headers=h).json()}
        assert listed[uid]["username"] == "demo.suzuki2"
        assert listed[uid]["role"] == "site_manager"
        # 新パスワードでログインできる
        assert client.post("/api/auth/login", json={
            "username": "demo.suzuki2", "password": "demo-pass-123"}).status_code == 200
        # 重複usernameは409
        assert client.post("/api/admin/users", json=payload, headers=h).status_code == 409
        # パスワード再設定とロール変更
        r = client.put(f"/api/admin/users/{uid}", json={
            "role": "tech_manager", "password": "new-pass-456"}, headers=h)
        assert r.status_code == 200, r.text
        assert client.post("/api/auth/login", json={
            "username": "demo.suzuki2", "password": "new-pass-456"}).status_code == 200
        # 無効化 → ログイン不可
        assert client.put(f"/api/admin/users/{uid}", json={"is_active": False},
                          headers=h).status_code == 200
        assert client.post("/api/auth/login", json={
            "username": "demo.suzuki2", "password": "new-pass-456"}).status_code == 401
        # 削除
        assert client.delete(f"/api/admin/users/{uid}", headers=h).status_code == 200
        assert client.get("/api/admin/users", headers=h).status_code == 200
    finally:
        _cleanup_users(["demo.suzuki2"], baseline)


def test_create_user_validation(client):
    h = _auth(client, "admin", "admin123")
    base = {"display_name": "検証", "password": "pass1234"}
    # 不正ロール
    assert client.post("/api/admin/users", json={**base, "username": "v1",
                        "role": "superadmin"}, headers=h).status_code == 422
    # 短いパスワード
    assert client.post("/api/admin/users", json={**base, "username": "v1",
                        "password": "short"}, headers=h).status_code == 422
    # 不正email
    assert client.post("/api/admin/users", json={**base, "username": "v1",
                        "email": "not-an-email"}, headers=h).status_code == 422
    # 不正username（全角・空白）
    assert client.post("/api/admin/users", json={**base, "username": "あああ"},
                       headers=h).status_code == 422
    # 余計なフィールドは拒否（extra=forbid）
    assert client.post("/api/admin/users", json={**base, "username": "v1",
                        "evil": "x"}, headers=h).status_code == 422


def test_self_lockout_and_last_admin_protection(client):
    baseline = _max_audit_id()
    h = _auth(client, "admin", "admin123")
    try:
        # 自分自身の降格・無効化は不可
        assert client.put("/api/admin/users/U01", json={"role": "viewer"},
                          headers=h).status_code == 400
        assert client.put("/api/admin/users/U01", json={"is_active": False},
                          headers=h).status_code == 400
        assert client.delete("/api/admin/users/U01", headers=h).status_code == 400
        # 最後の有効な管理者（U01 のみ）は降格・無効化できない（ガード単体で検証）
        db = SessionLocal()
        try:
            u01 = db.get(User, "U01")
            other_admin = User(id="U99", username="other-admin", display_name="他管理者",
                               role="admin", is_active=True)
            with pytest.raises(HTTPException) as exc:
                routes_mod._guard_user_change(db, other_admin, u01, "viewer", False)
            assert exc.value.status_code == 400
        finally:
            db.close()
    finally:
        _cleanup_users([], baseline)


def test_user_ops_are_audited(client):
    baseline = _max_audit_id()
    h = _auth(client, "admin", "admin123")
    try:
        r = client.post("/api/admin/users", json={
            "username": "audit.demo", "display_name": "監査 デモ",
            "password": "pass1234"}, headers=h)
        assert r.status_code == 201
        uid = r.json()["id"]
        client.put(f"/api/admin/users/{uid}", json={"department": "総務"}, headers=h)
        client.delete(f"/api/admin/users/{uid}", headers=h)
        db = SessionLocal()
        try:
            rows = db.scalars(select(AuditLog).where(
                AuditLog.id > baseline).order_by(AuditLog.id)).all()
            actions = [x.action for x in rows]
            assert actions[-3:] == ["user_create", "user_update", "user_delete"], actions
        finally:
            db.close()
    finally:
        _cleanup_users(["audit.demo"], baseline)
