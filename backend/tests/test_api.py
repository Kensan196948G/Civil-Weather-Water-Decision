"""API 結合テスト（取得→判定→表示の経路。詳細設計 §18.2 TC-001〜010 相当）。"""


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200 and r.json()["status"] == "ok"


def test_dashboard_site_risk(client):
    r = client.get("/api/dashboard/site-risk")
    assert r.status_code == 200
    data = r.json()
    assert len(data["summary"]) == 4
    assert len(data["sites"]) == 6
    for s in data["sites"]:
        assert s["levelLabel"] in ("通常", "注意", "中止検討", "確認不能")
        assert "reasons" in s and "updated" in s


def test_sites_and_detail(client):
    assert len(client.get("/api/sites").json()) == 6
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
    assert len(rows) == 6
    assert any(d["status"] == "Error" for d in rows)
    assert any(d["status"] == "Warning" for d in rows)
