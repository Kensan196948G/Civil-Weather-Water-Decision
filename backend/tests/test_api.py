"""API 結合テスト（取得→判定→表示の経路。詳細設計 §18.2 TC-001〜010 相当）。"""

import json
import time

from conftest import login_token


SECURITY_HEADERS = {
    "x-content-type-options": "nosniff",
    "x-frame-options": "DENY",
    "referrer-policy": "no-referrer",
    "permissions-policy": "geolocation=(), microphone=(), camera=()",
    "strict-transport-security": "max-age=31536000",
    "cross-origin-opener-policy": "same-origin",
    "cross-origin-resource-policy": "same-site",
    "x-permitted-cross-domain-policies": "none",
    "x-download-options": "noopen",
    "content-security-policy": (
        "default-src 'none'; base-uri 'none'; object-src 'none'; "
        "frame-ancestors 'none'; form-action 'none'"
    ),
}


def assert_security_headers(response):
    for key, expected in SECURITY_HEADERS.items():
        assert response.headers[key] == expected
    assert response.headers["cache-control"] == "no-store"


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200 and r.json()["status"] == "ok"


def test_health_security_headers(client):
    r = client.get("/health")
    assert_security_headers(r)


def test_local_api_docs_remain_available_without_api_csp(client):
    r = client.get("/docs")
    assert r.status_code == 200
    assert "swagger" in r.text.lower()
    assert "content-security-policy" not in r.headers


def test_readyz_ok(client):
    r = client.get("/readyz")
    assert r.status_code == 200
    assert_security_headers(r)
    body = r.json()
    assert body["status"] == "ok"
    assert body["checks"]["database"] is True
    assert body["checks"]["migrations"] is True
    assert body["checks"]["tables"] is True
    assert "DATABASE_URL" not in r.text
    assert "postgresql://" not in r.text
    assert "sqlite:///" not in r.text
    assert "JWT_SECRET" not in r.text
    assert "SETTINGS_ENCRYPTION_KEY" not in r.text
    assert "details" not in body


def test_readyz_not_ready_returns_503(client, monkeypatch):
    from app.core import readiness

    monkeypatch.setattr(readiness, "check_readiness", lambda: {
        "status": "not_ready",
        "app": "Civil-Weather-Water-Decision",
        "env": "local",
        "checks": {"database": {"ok": False}},
    })
    r = client.get("/readyz")
    assert r.status_code == 503
    assert_security_headers(r)
    assert r.json()["status"] == "not_ready"


def test_security_headers_on_401_and_422(client):
    unauthenticated = client.get("/api/sites", headers={"Authorization": ""})
    assert unauthenticated.status_code == 401
    assert_security_headers(unauthenticated)

    invalid_payload = client.post("/api/decisions/evaluate", json={"site_id": "S01"})
    assert invalid_payload.status_code == 422
    assert_security_headers(invalid_payload)


def test_security_headers_on_500():
    from fastapi.testclient import TestClient

    from app.main import app

    route_path = "/__test_security_headers_500"
    if not any(getattr(route, "path", None) == route_path for route in app.routes):
        @app.get(route_path)
        def _raise_for_security_header_test():
            raise RuntimeError("test-only failure")

    with TestClient(app, raise_server_exceptions=False) as c:
        r = c.get(route_path)
    assert r.status_code == 500
    assert_security_headers(r)


def test_readyz_migration_mismatch_is_not_ready(client, monkeypatch):
    from app.core import readiness

    class FakeScript:
        def get_heads(self):
            return ["not-the-current-head"]

    monkeypatch.setattr(readiness.ScriptDirectory, "from_config", lambda _cfg: FakeScript())
    body = readiness.check_readiness()
    assert body["status"] == "not_ready"
    assert body["checks"]["database"] is True
    assert body["checks"]["migrations"] is False
    assert "not-the-current-head" not in str(body)


def test_readyz_table_probe_failure_is_not_ready(client, monkeypatch):
    from sqlalchemy import text

    from app.core import readiness

    monkeypatch.setattr(readiness, "_TABLE_PROBES", (text("SELECT * FROM missing_readyz_table"),))
    body = readiness.check_readiness()
    assert body["status"] == "not_ready"
    assert body["checks"]["database"] is True
    assert body["checks"]["tables"] is False
    assert "missing_readyz_table" not in str(body)


def test_ops_readiness_detail_admin(client):
    r = client.get("/api/admin/ops/readiness-detail")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["checks"]["database"] is True
    assert body["details"]["database"]["dialect"] in ("sqlite", "postgresql")
    assert "current" in body["details"]["migrations"]
    assert "head" in body["details"]["migrations"]
    assert "DATABASE_URL" not in r.text
    assert "JWT_SECRET" not in r.text
    assert "SETTINGS_ENCRYPTION_KEY" not in r.text
    assert "postgresql://" not in r.text
    assert "sqlite:///" not in r.text


def test_ops_readiness_detail_allows_tech_manager(client):
    token = login_token(client, "tanaka")
    r = client.get("/api/admin/ops/readiness-detail",
                   headers={"Authorization": "Bearer " + token})
    assert r.status_code == 200


def test_ops_readiness_detail_denies_non_ops_roles_and_unauthenticated(client):
    for username in ("yamada", "takahashi", "viewer"):
        token = login_token(client, username)
        assert client.get("/api/admin/ops/readiness-detail",
                          headers={"Authorization": "Bearer " + token}).status_code == 403
    assert client.get("/api/admin/ops/readiness-detail",
                      headers={"Authorization": ""}).status_code == 401
    assert client.get("/api/admin/ops/readiness-detail",
                      headers={"Authorization": "Bearer invalid"}).status_code == 401


def _write_ops_status_snapshot(path, *, status="ok"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "snapshot_utc": "2026-07-13T06:00:00Z",
        "services": [{"unit": "cwwd-backend.service", "active_state": "active"}],
        "timers": [{"unit": "cwwd-ops-status.timer", "active_state": "active"}],
        "failed_units": [],
        "failed_units_count": 0,
        "status": status,
        "secret": "do-not-print",
    }))


def test_ops_status_snapshot_admin(client, monkeypatch, tmp_path):
    from app.core.config import settings

    snapshot = tmp_path / "ops-status.json"
    _write_ops_status_snapshot(snapshot)
    monkeypatch.setattr(settings, "ops_status_json_path", str(snapshot))
    monkeypatch.setattr(settings, "ops_status_json_max_age_seconds", 3600)

    r = client.get("/api/admin/ops/status-snapshot")

    assert r.status_code == 200
    assert_security_headers(r)
    body = r.json()
    assert body["status"] == "ok"
    assert body["snapshot"]["status"] == "ok"
    assert body["snapshot"]["services"][0]["unit"] == "cwwd-backend.service"
    assert "secret" not in body["snapshot"]
    assert "do-not-print" not in r.text
    assert body["metadata"]["age_seconds"] >= 0
    assert "DATABASE_URL" not in r.text
    assert "JWT_SECRET" not in r.text
    assert "SETTINGS_ENCRYPTION_KEY" not in r.text
    assert "postgresql://" not in r.text
    assert "sqlite:///" not in r.text


def test_ops_status_snapshot_allows_tech_manager(client, monkeypatch, tmp_path):
    from app.core.config import settings

    snapshot = tmp_path / "ops-status.json"
    _write_ops_status_snapshot(snapshot)
    monkeypatch.setattr(settings, "ops_status_json_path", str(snapshot))
    token = login_token(client, "tanaka")

    r = client.get("/api/admin/ops/status-snapshot",
                   headers={"Authorization": "Bearer " + token})

    assert r.status_code == 200


def test_ops_status_snapshot_denies_non_ops_roles_and_unauthenticated(client, monkeypatch, tmp_path):
    from app.core.config import settings

    snapshot = tmp_path / "ops-status.json"
    _write_ops_status_snapshot(snapshot)
    monkeypatch.setattr(settings, "ops_status_json_path", str(snapshot))

    for username in ("yamada", "takahashi", "viewer"):
        token = login_token(client, username)
        assert client.get("/api/admin/ops/status-snapshot",
                          headers={"Authorization": "Bearer " + token}).status_code == 403
    assert client.get("/api/admin/ops/status-snapshot",
                      headers={"Authorization": ""}).status_code == 401
    assert client.get("/api/admin/ops/status-snapshot",
                      headers={"Authorization": "Bearer invalid"}).status_code == 401


def test_ops_status_snapshot_unavailable_errors_do_not_leak_body(client, monkeypatch, tmp_path):
    from app.core.config import settings

    snapshot = tmp_path / "ops-status.json"
    snapshot.write_text('{"status":"ok","secret":"do-not-print"')
    monkeypatch.setattr(settings, "ops_status_json_path", str(snapshot))

    r = client.get("/api/admin/ops/status-snapshot")

    assert r.status_code == 503
    assert_security_headers(r)
    assert "invalid_json" in r.text
    assert "do-not-print" not in r.text
    assert "secret" not in r.text


def test_ops_status_snapshot_stale_returns_503(client, monkeypatch, tmp_path):
    from app.core.config import settings

    snapshot = tmp_path / "ops-status.json"
    _write_ops_status_snapshot(snapshot)
    old = int(time.time()) - 7200
    snapshot.touch()
    monkeypatch.setattr(settings, "ops_status_json_path", str(snapshot))
    monkeypatch.setattr(settings, "ops_status_json_max_age_seconds", 60)
    # touch after writing would refresh mtime; set it old after path/config setup.
    import os
    os.utime(snapshot, (old, old))

    r = client.get("/api/admin/ops/status-snapshot")

    assert r.status_code == 503
    assert "stale" in r.text


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
    assert_security_headers(r)
    assert "text/csv" in r.headers["content-type"]
    assert "attachment; filename=decision_logs.csv" in r.headers["content-disposition"]
    assert "decision_log_id" in r.text


def test_data_sources(client):
    rows = client.get("/api/dashboard/data-sources").json()
    assert len(rows) == 9
    ids = {d["id"] for d in rows}
    assert {"DS-JMA-CSV", "DS-JAXA", "DS-NOAA"} <= ids
    assert any(d["status"] == "Error" for d in rows)
    assert any(d["status"] == "Warning" for d in rows)


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
