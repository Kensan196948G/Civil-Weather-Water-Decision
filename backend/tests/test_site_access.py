"""#117: 現場単位権限（user_site_access）のテスト。

- デモユーザーへのシード割当（site_manager/safety/viewer）
- 未割当ユーザーは現場API・判定・判断記録へ403
- 割当後に現場閲覧・作業予定作成が可能になる
- admin API の割当（grant/update/revoke）とRBAC
"""
from sqlalchemy import delete, func, select

from app.core.db import SessionLocal
from app.core.security import hash_password
from app.models import AuditLog, Site, User, UserSiteAccess, WorkPlan


def _login(client, username, password):
    r = client.post("/api/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return r.json()["token"]


def _headers(client, username, password="pass1234"):
    return {"Authorization": f"Bearer {_login(client, username, password)}"}


def _max_audit_id() -> int:
    db = SessionLocal()
    try:
        return db.scalar(select(func.max(AuditLog.id))) or 0
    finally:
        db.close()


def _create_user(db, uid, username, role="viewer"):
    user = User(id=uid, username=username, display_name=f"テスト {username}",
                email=f"{username}@example.com", role=role, department="テスト",
                password_hash=hash_password("pass1234"), is_active=True,
                created_at="2026-08-05 00:00:00", updated_at="2026-08-05 00:00:00")
    db.add(user)
    db.commit()
    return user


def _cleanup(uid, site_ids, baseline_audit, delete_site=True,
             delete_user=True, grant_ids=None) -> None:
    db = SessionLocal()
    try:
        if grant_ids:
            db.execute(delete(UserSiteAccess).where(UserSiteAccess.id.in_(grant_ids)))
        if uid and delete_user:
            db.execute(delete(UserSiteAccess).where(UserSiteAccess.user_id == uid))
            db.execute(delete(User).where(User.id == uid))
        if site_ids and delete_site:
            db.execute(delete(WorkPlan).where(WorkPlan.site_id.in_(site_ids)))
            db.execute(delete(Site).where(Site.id.in_(site_ids)))
        db.execute(delete(AuditLog).where(AuditLog.id > baseline_audit))
        db.commit()
    finally:
        db.close()


def test_seed_grants_for_demo_users(client):
    r = client.get("/api/me/sites")
    assert r.status_code == 200
    sites = r.json()
    assert len(sites) >= 16  # admin は全現場
    assert all(s["role"] == "full" for s in sites)

    h = _headers(client, "yamada")
    me = client.get("/api/me/sites", headers=h).json()
    assert len(me) == 16 and all(s["role"] == "site_decision" for s in me)

    h = _headers(client, "viewer")
    me = client.get("/api/me/sites", headers=h).json()
    assert len(me) == 16 and all(s["role"] == "site_viewer" for s in me)


def test_unassigned_viewer_is_denied_until_grant(client):
    baseline = _max_audit_id()
    db = SessionLocal()
    try:
        _create_user(db, "UX01", "viewer01")
    finally:
        db.close()
    try:
        h = _headers(client, "viewer01")
        sites = client.get("/api/sites", headers=h).json()
        assert sites == []  # 未割当は閲覧できる現場なし
        assert client.get("/api/sites/S01", headers=h).status_code == 403
        assert client.get("/api/dashboard/site-risk", headers=h).json()["sites"] == []
        assert client.post("/api/decisions/evaluate", headers=h,
                           json={"site_id": "S01", "work_type": "river",
                                 "start": "2026-06-20T08:00", "end": "2026-06-20T12:00"}
                           ).status_code == 403
        assert client.post("/api/decision-logs", headers=h,
                           json={"site_id": "S01", "work_type": "河川内作業",
                                 "action": "monitor", "comment": "不可"}).status_code == 403

        # admin が site_viewer 割当 → 閲覧のみ可能
        r = client.post("/api/admin/user-site-access",
                        json={"user_id": "UX01", "site_id": "S01", "role": "site_viewer"})
        assert r.status_code == 201, r.text
        assert client.get("/api/sites/S01", headers=h).status_code == 200
        logs = client.get("/api/decision-logs", headers=h).json()
        assert all(entry["siteId"] == "S01" for entry in logs)
        # viewer は判定実行不可（site_viewer ロールでは decision 権限なし）
        assert client.post("/api/decisions/evaluate", headers=h,
                           json={"site_id": "S01", "work_type": "river",
                                 "start": "2026-06-20T08:00", "end": "2026-06-20T12:00"}
                           ).status_code == 403
    finally:
        _cleanup("UX01", [], baseline)


def test_site_manager_write_requires_assignment(client):
    baseline = _max_audit_id()
    site_id = None
    grant_id = None
    try:
        r = client.post("/api/sites", json={
            "name": "権限テスト現場", "latitude": 35.2, "longitude": 139.2,
            "work_type": "earthwork", "manager": "試験"})
        assert r.status_code == 201, r.text
        site_id = r.json()["id"]

        h = _headers(client, "yamada")
        # 新規現場は未割当 → 作業予定作成403
        assert client.post("/api/work-plans", headers=h, json={
            "site_id": site_id, "work_type": "earthwork", "title": "不可テスト",
            "planned_start": "2026-06-20T08:00", "planned_end": "2026-06-20T12:00",
        }).status_code == 403

        # site_editor 割当 → 作成可能
        r = client.post("/api/admin/user-site-access",
                        json={"user_id": "U03", "site_id": site_id, "role": "site_editor"})
        assert r.status_code == 201, r.text
        r = client.post("/api/work-plans", headers=h, json={
            "site_id": site_id, "work_type": "earthwork", "title": "許可テスト",
            "planned_start": "2026-06-20T08:00", "planned_end": "2026-06-20T12:00",
        })
        assert r.status_code == 201, r.text

        # 割当解除 → 403
        rows = client.get("/api/admin/user-site-access").json()
        aid = next(x["id"] for x in rows if x["siteId"] == site_id and x["userId"] == "U03")
        grant_id = aid
        assert client.delete(f"/api/admin/user-site-access/{aid}").status_code == 200
        assert client.post("/api/work-plans", headers=h, json={
            "site_id": site_id, "work_type": "earthwork", "title": "解除後不可",
            "planned_start": "2026-06-20T08:00", "planned_end": "2026-06-20T12:00",
        }).status_code == 403
    finally:
        _cleanup("U03", [site_id] if site_id else [], baseline,
                 delete_site=site_id is not None, delete_user=False,
                 grant_ids=[grant_id] if grant_id else [])


def test_grant_api_rbac_and_duplicate_update(client):
    baseline = _max_audit_id()
    db = SessionLocal()
    try:
        _create_user(db, "UX02", "viewer02")
    finally:
        db.close()
    try:
        # admin のみ管理可能（tech_manager/viewer は403）
        h = _headers(client, "tanaka")
        assert client.post("/api/admin/user-site-access", headers=h,
                           json={"user_id": "UX02", "site_id": "S02",
                                 "role": "site_viewer"}).status_code == 403
        r = client.post("/api/admin/user-site-access",
                        json={"user_id": "UX02", "site_id": "S02", "role": "site_viewer"})
        assert r.status_code == 201, r.text
        # 同一 (user,site) は更新
        r = client.post("/api/admin/user-site-access",
                        json={"user_id": "UX02", "site_id": "S02", "role": "site_editor"})
        assert r.status_code == 201, r.text
        assert r.json()["status"] == "updated"
        rows = client.get("/api/admin/user-site-access").json()
        row = next(x for x in rows if x["userId"] == "UX02" and x["siteId"] == "S02")
        assert row["role"] == "site_editor"
        assert client.delete(f"/api/admin/user-site-access/{row['id']}").status_code == 200
        # ロール不正は422
        assert client.post("/api/admin/user-site-access",
                           json={"user_id": "UX02", "site_id": "S02",
                                 "role": "superuser"}).status_code == 422
    finally:
        _cleanup("UX02", [], baseline)
