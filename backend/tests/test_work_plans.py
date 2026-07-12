"""作業予定 API（#16 T1-05・詳細設計§7 /api/work-plans）の結合テスト。"""
from conftest import login_token


def test_list_work_plans(client):
    rows = client.get("/api/work-plans").json()
    assert len(rows) == 5  # seed.py WP01〜WP05
    assert {"id", "siteId", "workType", "plannedStart", "plannedEnd", "status"} <= rows[0].keys()


def test_list_work_plans_filter_by_site(client):
    rows = client.get("/api/work-plans", params={"site_id": "S01"}).json()
    assert len(rows) == 2  # WP01, WP02
    assert all(r["siteId"] == "S01" for r in rows)


def test_list_work_plans_daily_view(client):
    # FR-014: 日別表示。seed は全件 2026-06-20 の予定
    rows = client.get("/api/work-plans", params={"date": "2026-06-20"}).json()
    assert len(rows) == 5
    assert client.get("/api/work-plans", params={"date": "2099-01-01"}).json() == []


def test_get_work_plan_detail(client):
    r = client.get("/api/work-plans/WP01")
    assert r.status_code == 200
    body = r.json()
    assert body["siteId"] == "S01" and body["workType"] == "river"


def test_get_work_plan_not_found(client):
    assert client.get("/api/work-plans/WP99").status_code == 404


def test_create_work_plan(client):
    r = client.post("/api/work-plans", json={
        "site_id": "S02", "work_type": "earthwork", "title": "追加盛土工",
        "planned_start": "2026-07-01T08:00", "planned_end": "2026-07-01T12:00",
        "contractor": "△△土建"})
    assert r.status_code == 201
    pid = r.json()["id"]
    detail = client.get(f"/api/work-plans/{pid}").json()
    assert detail["title"] == "追加盛土工" and detail["status"] == "planned"


def test_create_work_plan_site_not_found(client):
    r = client.post("/api/work-plans", json={
        "site_id": "NOPE", "work_type": "earthwork",
        "planned_start": "2026-07-01T08:00", "planned_end": "2026-07-01T12:00"})
    assert r.status_code == 404


def test_create_work_plan_invalid_work_type(client):
    r = client.post("/api/work-plans", json={
        "site_id": "S02", "work_type": "not-a-work-type",
        "planned_start": "2026-07-01T08:00", "planned_end": "2026-07-01T12:00"})
    assert r.status_code == 422


def test_create_work_plan_bad_time_order(client):
    r = client.post("/api/work-plans", json={
        "site_id": "S02", "work_type": "earthwork",
        "planned_start": "2026-07-01T12:00", "planned_end": "2026-07-01T08:00"})
    assert r.status_code == 422


def test_create_work_plan_bad_iso_datetime(client):
    r = client.post("/api/work-plans", json={
        "site_id": "S02", "work_type": "earthwork",
        "planned_start": "not-a-date", "planned_end": "2026-07-01T12:00"})
    assert r.status_code == 422


def test_create_work_plan_rejects_html(client):
    r = client.post("/api/work-plans", json={
        "site_id": "S02", "work_type": "earthwork", "title": "<script>alert(1)</script>",
        "planned_start": "2026-07-01T08:00", "planned_end": "2026-07-01T12:00"})
    assert r.status_code == 422


def test_viewer_cannot_create_work_plan(client):
    tok = login_token(client, "viewer")
    r = client.post("/api/work-plans", json={
        "site_id": "S02", "work_type": "earthwork",
        "planned_start": "2026-07-01T08:00", "planned_end": "2026-07-01T12:00"},
        headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 403


def test_update_work_plan_status(client):
    r = client.put("/api/work-plans/WP05", json={"status": "done"})
    assert r.status_code == 200
    assert client.get("/api/work-plans/WP05").json()["status"] == "done"


def test_update_work_plan_invalid_status(client):
    assert client.put("/api/work-plans/WP05", json={"status": "not-a-status"}).status_code == 422


def test_update_work_plan_not_found(client):
    assert client.put("/api/work-plans/WP99", json={"status": "done"}).status_code == 404


def test_evaluate_work_plan(client):
    r = client.post("/api/work-plans/WP03/evaluate")
    assert r.status_code == 200
    body = r.json()
    assert body["workPlanId"] == "WP03"
    assert body["overall_label"] in ("通常", "注意", "中止検討", "確認不能")
    got = client.get(f"/api/decision-results/{body['resultId']}")
    assert got.status_code == 200 and got.json()["siteId"] == "S03"


def test_evaluate_work_plan_not_found(client):
    assert client.post("/api/work-plans/WP99/evaluate").status_code == 404
