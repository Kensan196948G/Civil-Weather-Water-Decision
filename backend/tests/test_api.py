"""API 結合テスト（取得→判定→表示の経路。詳細設計 §18.2 TC-001〜010 相当）。"""


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200 and r.json()["status"] == "ok"


def test_dashboard_site_risk(client):
    r = client.get("/api/dashboard/site-risk")
    assert r.status_code == 200
    data = r.json()
    assert len(data["summary"]) == 4
    assert len(data["sites"]) == 16  # 全国16現場
    for s in data["sites"]:
        assert s["levelLabel"] in ("通常", "注意", "中止検討", "確認不能")
        assert "reasons" in s and "updated" in s


def test_sites_and_detail(client):
    assert len(client.get("/api/sites").json()) == 16
    detail = client.get("/api/sites/S01").json()
    assert detail["name"].startswith("北川")
    assert "plans" in detail and "history" in detail
    stations = client.get("/api/sites/S01/stations").json()
    assert any(st["type"] == "river" for st in stations)


def test_site_not_found(client):
    assert client.get("/api/sites/NOPE").status_code == 404


def test_weather_timeseries(client):
    r = client.get("/api/weather/timeseries", params={"site_id": "S03"})
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "OK" and len(body["points"]) >= 1


def test_evaluate_decision(client):
    r = client.post("/api/decisions/evaluate",
                    json={"site_id": "S01", "work_type": "river",
                          "start": "2026-06-20T08:00", "end": "2026-06-20T12:00"})
    assert r.status_code == 200
    body = r.json()
    assert body["overall_label"] in ("通常", "注意", "中止検討", "確認不能")
    assert isinstance(body["reasons"], list)


def test_decision_log_create_and_list(client):
    before = len(client.get("/api/decision-logs").json())
    r = client.post("/api/decision-logs",
                    json={"site_id": "S01", "work_type": "河川内作業", "level": 2,
                          "action": "cancel", "comment": "テスト記録"})
    assert r.status_code == 200 and r.json()["status"] == "recorded"
    after = client.get("/api/decision-logs").json()
    assert len(after) == before + 1
    # アクション絞り込み
    cancels = client.get("/api/decision-logs", params={"action": "cancel"}).json()
    assert all(h["action"] == "cancel" for h in cancels)


def test_export_csv(client):
    r = client.get("/api/decision-logs/export.csv")
    assert r.status_code == 200
    assert "text/csv" in r.headers["content-type"]
    assert "decision_log_id" in r.text


def test_data_sources(client):
    rows = client.get("/api/dashboard/data-sources").json()
    assert len(rows) == 9
    ids = {d["id"] for d in rows}
    assert {"DS-JMA-CSV", "DS-JAXA", "DS-NOAA"} <= ids
    assert any(d["status"] == "Error" for d in rows)
    assert any(d["status"] == "Warning" for d in rows)


def test_work_types(client):
    rows = client.get("/api/work-types").json()
    assert len(rows) == 6
    assert {r["id"] for r in rows} == {"river", "concrete", "earthwork", "pavement", "crane", "heat"}


def test_create_site(client):
    before = len(client.get("/api/sites").json())
    r = client.post("/api/sites", json={
        "name": "テスト新設 現場", "loc": "T市 試験地区",
        "latitude": 35.5, "longitude": 139.5, "work_type": "crane", "manager": "試験"})
    assert r.status_code == 201
    body = r.json()
    assert body["status"] == "created" and body["id"].startswith("S")
    after = client.get("/api/sites").json()
    assert len(after) == before + 1
    # ダッシュボード（ライブ判定）にも出現
    dash = client.get("/api/dashboard/site-risk").json()
    assert any(s["id"] == body["id"] for s in dash["sites"])


def test_create_site_validation(client):
    assert client.post("/api/sites", json={
        "name": "x", "latitude": 35, "longitude": 139, "work_type": "INVALID"}).status_code == 422
    assert client.post("/api/sites", json={
        "name": "", "latitude": 35, "longitude": 139, "work_type": "crane"}).status_code == 422
    assert client.post("/api/sites", json={
        "name": "y", "latitude": 999, "longitude": 139, "work_type": "crane"}).status_code == 422


def test_update_and_deactivate_site(client):
    sid = client.post("/api/sites", json={
        "name": "更新対象", "latitude": 35.1, "longitude": 139.1, "work_type": "earthwork"}).json()["id"]
    assert client.put(f"/api/sites/{sid}", json={"manager": "新担当"}).status_code == 200
    assert client.get(f"/api/sites/{sid}").json()["manager"].startswith("新担当")
    assert client.delete(f"/api/sites/{sid}").status_code == 200
    # 無効化後はダッシュボードから消える
    dash = client.get("/api/dashboard/site-risk").json()
    assert not any(s["id"] == sid for s in dash["sites"])
