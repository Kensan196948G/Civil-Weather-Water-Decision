"""API 結合テスト（取得→判定→表示の経路。詳細設計 §18.2 TC-001〜010 相当）。"""


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200 and r.json()["status"] == "ok"


def test_readyz(client):
    # テスト環境は seed.init_db() が起動時に Alembic head まで適用するため決定的に ok になる
    r = client.get("/readyz")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["checks"] == {
        "database": True, "migrations": True, "tables": True, "config": True,
    }


def test_readiness_detail_requires_admin_or_tech_manager(client):
    from tests.conftest import login_token
    token = login_token(client, "viewer")
    r = client.get("/api/admin/ops/readiness-detail",
                    headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 403


def test_readiness_detail_admin(client):
    r = client.get("/api/admin/ops/readiness-detail")
    assert r.status_code == 200
    body = r.json()
    assert body["checks"]["database"] is True
    assert "details" in body


def test_ops_status_snapshot_missing_file(client, monkeypatch, tmp_path):
    from app.core.config import settings
    # 本番機には実ファイルが存在し得るため、テスト専用の未作成パスへ差し替えて環境非依存にする
    monkeypatch.setattr(settings, "ops_status_json_path", str(tmp_path / "ops-status.json"))
    r = client.get("/api/admin/ops/status-snapshot")
    assert r.status_code == 503
    assert "ops status snapshot unavailable" in r.json()["detail"]


def test_ops_status_snapshot_ok(client, monkeypatch, tmp_path):
    import json
    from app.core.config import settings
    snapshot_path = tmp_path / "ops-status.json"
    snapshot_path.write_text(json.dumps({
        "snapshot_utc": "2026-07-13T00:00:00Z",
        "services": [{"unit": "cwwd-backend.service", "active_state": "active",
                       "result": "success", "n_restarts": "0"}],
        "timers": [],
        "failed_units": [],
        "failed_units_count": 0,
        "status": "ok",
    }), encoding="utf-8")
    monkeypatch.setattr(settings, "ops_status_json_path", str(snapshot_path))
    r = client.get("/api/admin/ops/status-snapshot")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["snapshot"]["services"][0]["unit"] == "cwwd-backend.service"


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


def test_marine_national(client):
    r = client.get("/api/marine/national")
    assert r.status_code == 200
    body = r.json()
    assert len(body["sites"]) == 16
    for s in body["sites"]:
        assert s["lat"] is not None and s["lon"] is not None  # 全国地図用座標
        assert "waveHeight" in s and "wavePeriod" in s and "swellHeight" in s
        assert "levelLabel" in s and "reasons" in s
        assert s["tide"] is None  # 気象庁潮位は未接続（実態を隠さない）
    assert body["source"]["marine"] == "DS-OPEN-METEO-MARINE"


def test_marine_evaluate_decision(client):
    r = client.post("/api/decisions/evaluate",
                    json={"site_id": "S12", "work_type": "marine",
                          "start": "2026-06-20T08:00", "end": "2026-06-20T12:00"})
    assert r.status_code == 200
    body = r.json()
    # モックの最大有義波高2.4m ≧ wave_stop 2.0m → 中止検討
    assert body["overall_level"] == 2
    assert any(x["reason_code"] == "wave_stop" for x in body["reasonsRaw"])
    assert body["waveHeight"] == 2.4  # 海上作業画面の表示用に波高も応答へ含める


def test_evaluate_decision(client):
    r = client.post("/api/decisions/evaluate",
                    json={"site_id": "S01", "work_type": "river",
                          "start": "2026-06-20T08:00", "end": "2026-06-20T12:00"})
    assert r.status_code == 200
    body = r.json()
    assert body["overall_label"] in ("通常", "注意", "中止検討", "確認不能")
    assert isinstance(body["reasons"], list)


def test_evaluate_persists_result(client):
    r = client.post("/api/decisions/evaluate",
                    json={"site_id": "S01", "work_type": "river",
                          "start": "2026-06-20T08:00", "end": "2026-06-20T12:00"})
    assert r.status_code == 200
    rid = r.json()["resultId"]
    assert rid.startswith("DR")
    got = client.get(f"/api/decision-results/{rid}")
    assert got.status_code == 200
    body = got.json()
    assert body["overall_label"] in ("通常", "注意", "中止検討", "確認不能")
    assert body["siteId"] == "S01" and body["workType"] == "river"
    for reason in body["reasons"]:
        assert "reason_code" in reason  # 生出力(理由コード)が保存される
    # 判断メモへ結果IDを紐付け
    log = client.post("/api/decision-logs", json={
        "site_id": "S01", "work_type": "河川内作業", "level": 2, "action": "cancel",
        "comment": "結果紐付け", "decision_result_id": rid})
    assert log.status_code == 200


def test_decision_result_not_found(client):
    assert client.get("/api/decision-results/DR99999").status_code == 404


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
    assert len(rows) == 11  # 9種 + Open-Meteo Marine + NOWPHAS（2026-08-12 統合）
    ids = {d["id"] for d in rows}
    assert {"DS-JMA-CSV", "DS-JAXA", "DS-NOAA"} <= ids
    assert "DS-OPEN-METEO-MARINE" in ids
    assert "DS-NOWPHAS" in ids
    assert any(d["status"] == "Error" for d in rows)
    assert any(d["status"] == "Warning" for d in rows)


def test_run_collectors_returns_river_demo_status(client, monkeypatch):
    """手動再取得がプローブ結果と河川デモ収集結果を返す（ネット非依存）。"""
    from app.services.data_collectors import source_probe

    async def fake_probe(db):
        return {"DS-OPEN-METEO": {"status": "OK", "ms": 1, "ok": True, "error": None}}

    monkeypatch.setattr(source_probe, "probe_all", fake_probe)
    r = client.post("/api/data-collectors/run")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["probed"]["DS-OPEN-METEO"] == "OK"
    assert "river" in body


def test_notifications_endpoint(client):
    r = client.get("/api/notifications")
    assert r.status_code == 200
    body = r.json()
    assert "count" in body and "notifications" in body
    for n in body["notifications"]:
        for k in ("id", "kind", "severity", "title", "message", "disclaimer"):
            assert k in n


def test_work_types(client):
    rows = client.get("/api/work-types").json()
    assert len(rows) == 7  # 6種 + marine（海上作業）
    assert {r["id"] for r in rows} == {
        "river", "concrete", "earthwork", "pavement", "crane", "heat", "marine"}


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


def test_create_site_rejects_html(client):
    # 名称に HTML 危険文字 → 422（XSS 多層防御）
    assert client.post("/api/sites", json={
        "name": "<img src=x onerror=alert(1)>", "latitude": 35, "longitude": 139,
        "work_type": "crane"}).status_code == 422


def test_update_and_deactivate_site(client):
    sid = client.post("/api/sites", json={
        "name": "更新対象", "latitude": 35.1, "longitude": 139.1, "work_type": "earthwork"}).json()["id"]
    assert client.put(f"/api/sites/{sid}", json={"manager": "新担当"}).status_code == 200
    assert client.get(f"/api/sites/{sid}").json()["manager"].startswith("新担当")
    assert client.delete(f"/api/sites/{sid}").status_code == 200
    # 無効化後はダッシュボードから消える
    dash = client.get("/api/dashboard/site-risk").json()
    assert not any(s["id"] == sid for s in dash["sites"])
