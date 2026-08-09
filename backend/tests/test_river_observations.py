"""#29/#31 T2-01/T2-03: 河川観測所マスタ・現場紐付け・実測値の検証。

- 観測所マスタ CRUD と source_id+station_code の一意性
- 現場への紐付け（rel: upstream/nearest/reference）と重複・上限
- 手動実測値の作成・時系列一覧・修正・削除
- RBAC（書き込みは admin/tech_manager、観測所削除は admin）
- 監査の同一トランザクション（#63 と同方式）
- API 応答で「自動取得は未接続」を明示（外部評価 P0: 未実装の誤表示防止）

共有テストDBを汚さないよう、作成した行と増えた監査行は各テスト末尾で削除する。
"""
import pytest
from sqlalchemy import delete, func, select

from app.api import routes as routes_mod
from app.core.db import SessionLocal
from app.models import AuditLog, ObservationStation, RiverObservation, SiteStation


def _boom(*args, **kwargs):
    raise RuntimeError("simulated audit failure (#29/#31 test)")


def _token(client, username, password="pass1234"):
    r = client.post("/api/auth/login", json={"username": username, "password": password})
    return r.json().get("token")


def _auth_headers(client, username, password="pass1234"):
    return {"Authorization": f"Bearer {_token(client, username, password)}"}


def _max_audit_id() -> int:
    db = SessionLocal()
    try:
        return db.scalar(select(func.max(AuditLog.id))) or 0
    finally:
        db.close()


def _cleanup(station_ids, link_ids, obs_ids, baseline_audit: int) -> None:
    db = SessionLocal()
    try:
        if obs_ids:
            db.execute(delete(RiverObservation).where(RiverObservation.id.in_(obs_ids)))
        if link_ids:
            db.execute(delete(SiteStation).where(SiteStation.id.in_(link_ids)))
        if station_ids:
            db.execute(delete(ObservationStation).where(ObservationStation.id.in_(station_ids)))
        db.execute(delete(AuditLog).where(AuditLog.id > baseline_audit))
        db.commit()
    finally:
        db.close()


STATION = {
    "source_id": "MANUAL", "station_code": "TEST-001", "name": "テスト川 水位観測所",
    "agency": "テスト県", "basin_name": "テスト川", "kind": "water",
    "latitude": 35.5, "longitude": 139.5,
}


def _create_station(client, headers=None, **overrides):
    r = client.post("/api/observation-stations", json={**STATION, **overrides},
                    headers=headers or {})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _link(client, site_id, station_id, headers=None, **overrides):
    r = client.post(f"/api/sites/{site_id}/observation-stations",
                    json={"station_id": station_id, "rel": "nearest", **overrides},
                    headers=headers or {})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _record(client, site_id, station_id, headers=None, **overrides):
    r = client.post(f"/api/sites/{site_id}/river-observations",
                    json={"station_id": station_id, "water_level_m": 2.35, **overrides},
                    headers=headers or {})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def test_station_crud_and_duplicate_rejected(client):
    baseline = _max_audit_id()
    sid = _create_station(client)
    try:
        rows = client.get("/api/observation-stations").json()
        row = next(x for x in rows if x["id"] == sid)
        assert row["name"] == "テスト川 水位観測所"
        assert row["sourceId"] == "MANUAL" and row["stationCode"] == "TEST-001"

        # 同じ sourceId + stationCode は 409
        r = client.post("/api/observation-stations", json=STATION)
        assert r.status_code == 409, r.text

        # PUT で更新でき、kind の範囲外は 422
        r = client.put(f"/api/observation-stations/{sid}",
                       json={"name": "テスト川 改修後 水位観測所", "basin_name": "テスト川 上流"})
        assert r.status_code == 200, r.text
        row = next(x for x in client.get("/api/observation-stations").json() if x["id"] == sid)
        assert row["name"] == "テスト川 改修後 水位観測所"

        r = client.put(f"/api/observation-stations/{sid}", json={"kind": "wave"})
        assert r.status_code == 422
    finally:
        _cleanup([sid], [], [], baseline)


def test_station_validation(client):
    baseline = _max_audit_id()
    try:
        bads = [
            {**STATION, "station_code": ""},
            {**STATION, "name": "<script>"},
            {**STATION, "kind": "wave"},
            {**STATION, "latitude": 99.0},
            {**STATION, "longitude": -181.0},
        ]
        for payload in bads:
            r = client.post("/api/observation-stations", json=payload)
            assert r.status_code == 422, f"{payload} は拒否されるべき: {r.status_code}"
    finally:
        _cleanup([], [], [], baseline)


def test_site_link_and_latest_observation(client):
    baseline = _max_audit_id()
    sid, lid = None, None
    obs_ids = []
    try:
        sid = _create_station(client)
        lid = _link(client, "S01", sid, rel="upstream", sort_order=1)
        data = client.get("/api/sites/S01/observation-stations").json()
        assert data["automatic"] is False
        assert "未接続" in data["provider"]
        item = next(x for x in data["stations"] if x["id"] == sid)
        assert item["rel"] == "upstream" and item["sortOrder"] == 1
        assert item["latest"] is None

        # 同じ観測所の再紐付けは 409、存在しない観測所は 404
        r = client.post("/api/sites/S01/observation-stations",
                        json={"station_id": sid, "rel": "reference"})
        assert r.status_code == 409, r.text
        r = client.post("/api/sites/S01/observation-stations",
                        json={"station_id": "OS999", "rel": "reference"})
        assert r.status_code == 404, r.text

        # 実測値を登録すると latest に反映される
        oid = _record(client, "S01", sid, observed_at="2026-08-05T09:30:00+09:00",
                      water_level_m=1.82, note="現地目視")
        obs_ids.append(oid)
        data = client.get("/api/sites/S01/observation-stations").json()
        item = next(x for x in data["stations"] if x["id"] == sid)
        assert item["latest"]["waterLevelM"] == 1.82
        assert item["latest"]["source"] == "MANUAL"

        # 時系列一覧は新しい順
        oid2 = _record(client, "S01", sid, observed_at="2026-08-05T10:00:00+09:00",
                       water_level_m=2.05)
        obs_ids.append(oid2)
        series = client.get("/api/sites/S01/river-observations?limit=5").json()
        assert series["automatic"] is False
        assert [o["id"] for o in series["observations"]] == [oid2, oid]
    finally:
        _cleanup([sid] if sid else [], [lid] if lid else [], obs_ids, baseline)


def test_observation_validation_and_unlinked_rejected(client):
    baseline = _max_audit_id()
    sid, lid, oid = None, None, None
    try:
        sid = _create_station(client)
        # 未紐付けの観測所への入力は 422
        r = client.post("/api/sites/S02/river-observations",
                        json={"station_id": sid, "water_level_m": 1.0})
        assert r.status_code == 422, r.text

        lid = _link(client, "S01", sid)
        # 水位・雨量とも無しは 422
        r = client.post("/api/sites/S01/river-observations",
                        json={"station_id": sid, "note": "値なし"})
        assert r.status_code == 422, r.text
        # 品質・範囲外は 422
        r = client.post("/api/sites/S01/river-observations",
                        json={"station_id": sid, "water_level_m": 999.0})
        assert r.status_code == 422, r.text
        r = client.post("/api/sites/S01/river-observations",
                        json={"station_id": sid, "rainfall_mm_h": -1.0})
        assert r.status_code == 422, r.text
        # 雨量のみも登録可能
        oid = _record(client, "S01", sid, water_level_m=None, rainfall_mm_h=4.5)
        row = next(o for o in client.get("/api/sites/S01/river-observations").json()
                   ["observations"] if o["id"] == oid)
        assert row["rainfallMmH"] == 4.5 and row["waterLevelM"] is None
    finally:
        _cleanup([sid] if sid else [], [lid] if lid else [],
                 [oid] if oid else [], baseline)


def test_update_delete_observation(client):
    baseline = _max_audit_id()
    sid, lid, oid = None, None, None
    try:
        sid = _create_station(client)
        lid = _link(client, "S01", sid)
        oid = _record(client, "S01", sid, water_level_m=2.35)
        r = client.put(f"/api/river-observations/{oid}",
                       json={"water_level_m": 2.31, "note": "入力誤り訂正"})
        assert r.status_code == 200, r.text
        row = next(o for o in client.get("/api/sites/S01/river-observations").json()
                   ["observations"] if o["id"] == oid)
        assert row["waterLevelM"] == 2.31
        assert row["note"] == "入力誤り訂正"

        # viewer は削除不可、admin は可
        r = client.delete(f"/api/river-observations/{oid}",
                          headers=_auth_headers(client, "viewer"))
        assert r.status_code == 403, r.text
        r = client.delete(f"/api/river-observations/{oid}")
        assert r.status_code == 200, r.text
        assert not any(o["id"] == oid for o in client.get(
            "/api/sites/S01/river-observations").json()["observations"])
    finally:
        _cleanup([sid] if sid else [], [lid] if lid else [],
                 [oid] if oid else [], baseline)


def test_station_delete_protected_by_link_and_observation(client):
    baseline = _max_audit_id()
    sid, lid, oid = None, None, None
    try:
        sid = _create_station(client)
        lid = _link(client, "S01", sid)
        oid = _record(client, "S01", sid)
        # 紐付け・実測値がある限り削除不可（誤削除防止）
        r = client.delete(f"/api/observation-stations/{sid}")
        assert r.status_code == 409, r.text
        # 削除は admin 限定（tech_manager でも 403）
        r = client.delete(f"/api/observation-stations/{sid}",
                          headers=_auth_headers(client, "tanaka"))
        assert r.status_code == 403, r.text
    finally:
        _cleanup([sid] if sid else [], [lid] if lid else [],
                 [oid] if oid else [], baseline)


def test_river_rbac_boundary(client):
    baseline = _max_audit_id()
    sid, sid2, lid, oid = None, None, None, None
    try:
        sid = _create_station(client)
        for uname in ("yamada", "takahashi", "viewer"):
            r = client.post("/api/observation-stations", json={
                **STATION, "station_code": f"NO-{uname}"},
                headers=_auth_headers(client, uname))
            assert r.status_code == 403, f"{uname} は観測所作成不可であるべき"
            r = client.post("/api/sites/S01/observation-stations",
                            json={"station_id": sid},
                            headers=_auth_headers(client, uname))
            assert r.status_code == 403, f"{uname} は紐付け不可であるべき"
            r = client.post("/api/sites/S01/river-observations",
                            json={"station_id": sid, "water_level_m": 1.0},
                            headers=_auth_headers(client, uname))
            assert r.status_code == 403, f"{uname} は実測値登録不可であるべき"

        # tech_manager は作成・紐付け・実測値登録が可能
        sid2 = _create_station(client, station_code="TECH-OK",
                               headers=_auth_headers(client, "tanaka"))
        lid = _link(client, "S01", sid2, headers=_auth_headers(client, "tanaka"))
        oid = _record(client, "S01", sid2, headers=_auth_headers(client, "tanaka"))
        assert client.get("/api/observation-stations",
                          headers=_auth_headers(client, "viewer")).status_code == 200
        assert client.get("/api/sites/S01/river-observations",
                          headers=_auth_headers(client, "viewer")).status_code == 200
    finally:
        _cleanup([sid] if sid else [], [], [], baseline)
        if sid2:
            _cleanup([sid2], [lid] if lid else [], [oid] if oid else [], baseline)


def test_station_create_atomic_on_audit_failure(client, monkeypatch):
    baseline = _max_audit_id()
    before = client.get("/api/observation-stations").json()
    original = routes_mod.audit_add
    payload = {**STATION, "station_code": "ATOMIC-1"}

    monkeypatch.setattr(routes_mod, "audit_add", _boom)
    with pytest.raises(RuntimeError):
        client.post("/api/observation-stations", json=payload)
    assert client.get("/api/observation-stations").json() == before, \
        "監査失敗時に観測所が残ってはならない（原子性）"

    monkeypatch.setattr(routes_mod, "audit_add", original)
    sid = _create_station(client, station_code="ATOMIC-1")
    try:
        assert any(x["id"] == sid for x in client.get("/api/observation-stations").json())
    finally:
        _cleanup([sid], [], [], baseline)
