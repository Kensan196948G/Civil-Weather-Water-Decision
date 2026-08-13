"""判断履歴の検索・類似参照・PDF帳票の検証。

/api/decision-logs の絞り込み（site_id/work_type/q/action）、
/api/decision-logs/similar のスコア順・権限境界、
/api/decision-logs/export.pdf の出力内容を確認する。
"""
from __future__ import annotations

from sqlalchemy import func, select

from app.core.db import SessionLocal
from app.models import AuditLog, User, UserSiteAccess


def _auth(client, username, password="pass1234"):
    r = client.post("/api/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


def _max_audit_id() -> int:
    db = SessionLocal()
    try:
        return db.scalar(select(func.max(AuditLog.id))) or 0
    finally:
        db.close()


def test_filters(client):
    h = _auth(client, "admin", "admin123")
    all_logs = client.get("/api/decision-logs", headers=h).json()
    assert len(all_logs) >= 20  # seed は14日分の履歴を投入
    s01 = client.get("/api/decision-logs", params={"site_id": "S01"}, headers=h).json()
    assert s01 and all(x["siteId"] == "S01" for x in s01)
    cancels = client.get("/api/decision-logs", params={"action": "cancel"}, headers=h).json()
    assert cancels and all(x["action"] == "cancel" for x in cancels)
    river = client.get("/api/decision-logs",
                       params={"work_type": "河川内作業"}, headers=h).json()
    assert river and all(x["workType"] == "河川内作業" for x in river)
    # キーワード検索（現場名・メモ・記録者を横断）
    q = client.get("/api/decision-logs", params={"q": "打設"}, headers=h).json()
    assert q and all(
        "打設" in (x["comment"] + x["site"] + x["workType"] + x["by"]) for x in q)
    # 権限外の現場指定は空を返す（架空現場ID）
    assert client.get("/api/decision-logs",
                      params={"site_id": "NOPE"}, headers=h).json() == []


def test_similar_scoring_and_rbac(client):
    h = _auth(client, "admin", "admin123")
    r = client.get("/api/decision-logs/similar",
                   params={"site_id": "S01", "work_type": "河川内作業"}, headers=h)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body
    scores = [x["score"] for x in body]
    assert scores == sorted(scores, reverse=True)
    assert body[0]["siteId"] == "S01"
    assert any("同一現場" in x["matchReasons"] for x in body)
    # 条件未指定は400
    assert client.get("/api/decision-logs/similar", headers=h).status_code == 400
    # 閲覧ロールでも利用できる（読み取りAPI）
    h_viewer = _auth(client, "viewer")
    assert client.get("/api/decision-logs/similar",
                      params={"work_type": "土工"}, headers=h_viewer).status_code == 200


def test_similar_respects_site_access(client):
    """現場単位権限: S01 のみ許可されたユーザーには他現場の類似判断が見えない。"""
    baseline = _max_audit_id()
    admin = _auth(client, "admin", "admin123")
    created = "limited.viewer"
    try:
        r = client.post("/api/admin/users", json={
            "username": created, "display_name": "限定 閲覧", "role": "viewer",
            "password": "pass1234"}, headers=admin)
        assert r.status_code == 201, r.text
        uid = r.json()["id"]
        assert client.post("/api/admin/user-site-access", json={
            "user_id": uid, "site_id": "S01", "role": "site_viewer"},
            headers=admin).status_code == 201
        h = _auth(client, created)
        logs = client.get("/api/decision-logs", headers=h).json()
        assert logs and all(x["siteId"] == "S01" for x in logs)
        similar = client.get("/api/decision-logs/similar",
                             params={"work_type": "河川内作業"}, headers=h).json()
        assert similar
        assert all(x["siteId"] == "S01" for x in similar)
    finally:
        db = SessionLocal()
        try:
            u = db.scalar(select(User).where(User.username == created))
            if u:
                for row in db.scalars(select(UserSiteAccess).where(
                        UserSiteAccess.user_id == u.id)):
                    db.delete(row)
                db.flush()  # 子行(現場権限)を先に確定してからユーザーを削除（FK制約）
                db.delete(u)
            for row in db.scalars(select(AuditLog).where(AuditLog.id > baseline)):
                db.delete(row)
            db.commit()
        finally:
            db.close()


def test_pdf_export(client):
    baseline = _max_audit_id()
    h = _auth(client, "admin", "admin123")
    r = client.get("/api/decision-logs/export.pdf", headers=h)
    assert r.status_code == 200, r.text[:200]
    assert r.headers["content-type"].startswith("application/pdf")
    assert "attachment" in r.headers["content-disposition"]
    body = r.content
    assert body.startswith(b"%PDF-")
    assert len(body) > 1000  # 埋め込みフォント＋表の実体がある
    # 監査が記録される
    db = SessionLocal()
    try:
        actions = [x.action for x in db.scalars(select(AuditLog).where(
            AuditLog.id > baseline).order_by(AuditLog.id)).all()]
        assert "pdf_export" in actions, actions
    finally:
        db.close()
