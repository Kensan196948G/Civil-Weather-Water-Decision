"""#113: 環境省WBGT地点マスタ・全地点予報・現場別最近傍解決のテスト。

- 地点マスタCSV（度＋分表記）の正規化
- 全地点予報CSV（yohou_all）の正規化
- 管理APIによる observation_stations（kind=wbgt）への同期とRBAC
- 現場ごとの最近傍地点・明示リンク優先の解決と評価への反映
"""
import pytest
from sqlalchemy import delete, func, select

from app.core.db import SessionLocal
from app.models import AuditLog, ObservationStation, SiteStation
from app.services import assessment
from app.services.data_collectors import wbgt_env


MASTER_CSV = (
    "\ufeff地方, 振興局, 地点番号, 観測所名, よみがな, ローマ字表記, 所在地, "
    "Latitude, Latitude_3, Longitude, Longitude_4, Start, End, Old, "
    "実測開始日, 実測終了日, 特判除外開始日, 特判除外終了日\n"
    "北海道, 宗谷, 11001, 宗谷岬, そうやみさき, SOYAMISAKI, 稚内市宗谷岬, "
    "45, 31.2, 141, 56.1, 2010-05-01, 9999-99-99, 11001, , , ,\n"
    "東京都, 東京, 44132, 東京, とうきょう, TOKYO, 東京都千代田区, "
    "35, 41.6, 139, 45.0, 2010-05-01, 9999-99-99, 44132, , , ,\n"
)

ALL_CSV = (
    ",,2019073115,2019073118,2019073121\n"
    "11001,2019/07/31 14:25, 250, 230, 220\n"
    "44132,2019/07/31 14:25, 280, 260, 240\n"
)


def _max_audit_id() -> int:
    db = SessionLocal()
    try:
        return db.scalar(select(func.max(AuditLog.id))) or 0
    finally:
        db.close()


def _cleanup_wbgt(codes, baseline_audit, link_ids=None) -> None:
    db = SessionLocal()
    try:
        if link_ids:
            db.execute(delete(SiteStation).where(SiteStation.id.in_(link_ids)))
        db.execute(delete(ObservationStation).where(
            ObservationStation.source_id == wbgt_env.WBGT_STATION_SOURCE_ID,
            ObservationStation.station_code.in_(codes)))
        db.execute(delete(AuditLog).where(AuditLog.id > baseline_audit))
        db.commit()
    finally:
        db.close()


def _login(client, username, password="pass1234"):
    return client.post("/api/auth/login",
                       json={"username": username, "password": password}).json()["token"]


def _create_wbgt(client, code, name, lat, lon):
    r = client.post("/api/observation-stations", json={
        "source_id": wbgt_env.WBGT_STATION_SOURCE_ID, "station_code": code,
        "name": name, "agency": "環境省", "basin_name": "テスト",
        "kind": "wbgt", "latitude": lat, "longitude": lon})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def test_parse_point_master_dms_to_decimal():
    stations = wbgt_env.parse_point_master(MASTER_CSV)
    assert len(stations) == 2
    soya = next(s for s in stations if s["station_code"] == "11001")
    assert soya["name"] == "宗谷岬"
    assert soya["agency"] == "環境省"
    assert soya["latitude"] == pytest.approx(45 + 31.2 / 60, abs=1e-3)
    assert soya["longitude"] == pytest.approx(141 + 56.1 / 60, abs=1e-3)
    tokyo = next(s for s in stations if s["station_code"] == "44132")
    assert tokyo["latitude"] == pytest.approx(35 + 41.6 / 60, abs=1e-3)
    assert tokyo["longitude"] == pytest.approx(139 + 45.0 / 60, abs=1e-3)


def test_parse_forecast_all_csv():
    data = wbgt_env.parse_forecast_all_csv(ALL_CSV)
    assert data["count"] == 2
    points = data["points_by_station"]["44132"]
    assert points[0]["wbgt"] == 28.0
    assert points[1]["time"] == "2019-07-31T18:00"
    assert points[2]["quality_flag"] == "OK"


def test_sync_wbgt_stations_endpoint(client, monkeypatch):
    baseline = _max_audit_id()

    async def fake_fetch(**kw):
        return {"source_id": wbgt_env.SOURCE_ID, "status": "OK", "error": None,
                "stations": wbgt_env.parse_point_master(MASTER_CSV), "count": 2,
                "fetched_at": "2026-08-05T00:00:00Z"}

    monkeypatch.setattr(wbgt_env, "fetch_point_master", fake_fetch)
    try:
        r = client.post("/api/admin/wbgt/stations/sync")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["status"] == "OK" and body["upserted"] == 2

        rows = client.get("/api/observation-stations?kind=wbgt").json()
        codes = {x["stationCode"] for x in rows}
        assert {"11001", "44132"} <= codes

        # 再同期は更新のみ（冪等）
        r2 = client.post("/api/admin/wbgt/stations/sync")
        assert r2.status_code == 200
        assert r2.json()["upserted"] == 0 and r2.json()["updated"] == 2

        # viewer は同期不可
        token = _login(client, "viewer")
        r3 = client.post("/api/admin/wbgt/stations/sync",
                         headers={"Authorization": f"Bearer {token}"})
        assert r3.status_code == 403
    finally:
        _cleanup_wbgt(["11001", "44132"], baseline)


def test_site_uses_explicit_or_nearest_wbgt_station(client, monkeypatch):
    baseline = _max_audit_id()
    stations, links = [], []
    try:
        fuku = _create_wbgt(client, "82001", "福岡WBGT", 33.60, 130.40)
        tokyo = _create_wbgt(client, "44132", "東京WBGT", 35.69, 139.75)
        stations += [fuku, tokyo]

        async def fake_all(**kw):
            return {"source_id": wbgt_env.SOURCE_ID, "status": "OK", "error": None,
                    "points_by_station": {
                        "82001": [{"time": "2026-08-05T09:00", "wbgt": 31.5,
                                   "quality_flag": "OK"}],
                        "44132": [{"time": "2026-08-05T09:00", "wbgt": 26.0,
                                   "quality_flag": "OK"}]},
                    "count": 2, "fetched_at": "2026-08-05T00:00:00Z"}

        monkeypatch.setattr(wbgt_env, "fetch_forecast_all", fake_all)
        assessment.clear_cache()

        # 明示リンク（福岡）が優先される（S15=福岡のheat現場）
        r = client.post("/api/sites/S15/observation-stations",
                        json={"station_id": fuku, "rel": "reference", "sort_order": 1})
        assert r.status_code == 201, r.text
        links.append(r.json()["id"])
        assessment.clear_cache()
        card = client.get("/api/sites/S15").json()
        assert card["wbgtStation"] == "82001"
        assert card["wbgt"] == 31.5
        assert card["wbgtDerived"] is False

        # リンク解除後は最近傍（福岡82001）が選ばれる
        r = client.delete(f"/api/sites/S15/observation-stations/{fuku}")
        assert r.status_code == 200, r.text
        links = []
        assessment.clear_cache()
        card2 = client.get("/api/sites/S15").json()
        assert card2["wbgtStation"] == "82001"
    finally:
        _cleanup_wbgt(["82001", "44132"], baseline, link_ids=links)
