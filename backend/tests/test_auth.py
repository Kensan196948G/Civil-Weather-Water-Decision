"""認証・RBAC・監査ログのテスト（設計§12/§13）。"""
from conftest import login_token


def test_login_ok(client):
    r = client.post("/api/auth/login", json={"username": "admin", "password": "admin123"})
    assert r.status_code == 200
    assert r.json()["user"]["role"] == "admin" and r.json()["token"]


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
