"""#112: 河川実測の判定エンジン組み込みの統合テスト。

- 最寄り/上流観測所の手動入力値で upstream_rain / water_level_rising が発火
- 判定理由に出典・時刻・実測値が記録される
- 30分以上古い・欠測・ERROR は missing=river → レベル3（確認不能）
- 観測所・実測がない河川現場は安全側（確認不能）に倒す
"""
from datetime import datetime, timedelta

import pytest
from sqlalchemy import delete, func, select

from app.core.db import SessionLocal
from app.models import AuditLog, ObservationStation, RiverObservation, Site, SiteStation
from app.services import assessment


def _now(offset_minutes: int = 0) -> str:
    return (datetime.now(assessment.JST) + timedelta(minutes=offset_minutes)
            ).strftime("%Y-%m-%dT%H:%M:%S+09:00")


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
    "source_id": "MANUAL", "station_code": "R112-001", "name": "テスト川 水位観測所",
    "agency": "テスト県", "basin_name": "テスト川", "kind": "water",
    "latitude": 35.7, "longitude": 139.5,
}


def _create_station(client, **overrides):
    r = client.post("/api/observation-stations", json={**STATION, **overrides})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _link(client, site_id, station_id, rel="nearest", sort_order=0):
    r = client.post(f"/api/sites/{site_id}/observation-stations",
                    json={"station_id": station_id, "rel": rel, "sort_order": sort_order})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _record(client, site_id, station_id, **overrides):
    payload = {"station_id": station_id, "water_level_m": 2.0, **overrides}
    r = client.post(f"/api/sites/{site_id}/river-observations", json=payload)
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _card(client, site_id="S05"):
    r = client.get(f"/api/sites/{site_id}")
    assert r.status_code == 200, r.text
    return r.json()


def test_upstream_rain_and_rising_from_manual_observations(client):
    """最寄り2点の水位上昇＋上流雨量で判定が変化し、理由に出典・時刻・実測値が残る。"""
    baseline = _max_audit_id()
    stations, links, obs_ids = [], [], []
    try:
        nearest = _create_station(client, station_code="R112-N", name="テスト川 最寄り",
                                  kind="water")
        upstream = _create_station(client, station_code="R112-U", name="テスト川 上流",
                                   kind="rain")
        stations += [nearest, upstream]
        links.append(_link(client, "S05", nearest, rel="nearest", sort_order=1))
        links.append(_link(client, "S05", upstream, rel="upstream", sort_order=2))

        obs_ids.append(_record(client, "S05", upstream, water_level_m=None,
                               rainfall_mm_h=6.0, observed_at=_now(0)))
        obs_ids.append(_record(client, "S05", nearest, water_level_m=2.00,
                               observed_at=_now(-20)))
        obs_ids.append(_record(client, "S05", nearest, water_level_m=2.30,
                               observed_at=_now(-10)))

        card = _card(client)
        assert card["riverState"] == "rising"
        assert card["riverSource"] == "MANUAL"
        codes = [r["reason_code"] for r in card["reasonsRaw"]]
        assert "upstream_rain" in codes and "water_level_rising" in codes
        assert card["level"] == 1

        up = next(r for r in card["reasonsRaw"] if r["reason_code"] == "upstream_rain")
        assert up["source_id"] == "MANUAL"
        assert up["observed_value"] == "上流雨量 6.0mm/h"
        rising = next(r for r in card["reasonsRaw"] if r["reason_code"] == "water_level_rising")
        assert rising["source_id"] == "MANUAL"
        assert "水位 2.3m" in rising["observed_value"] and "上昇" in rising["observed_value"]
        assert "2.3m" in card["river"]
    finally:
        _cleanup(stations, links, obs_ids, baseline)


def test_stale_observation_is_unavailable(client):
    """30分以上古い実測は欠測扱いとし、レベル3（確認不能）へ導く。"""
    baseline = _max_audit_id()
    stations, links, obs_ids = [], [], []
    try:
        nearest = _create_station(client, station_code="R112-STALE")
        stations.append(nearest)
        links.append(_link(client, "S05", nearest))
        obs_ids.append(_record(client, "S05", nearest, water_level_m=1.5,
                               observed_at=_now(-40)))

        card = _card(client)
        assert card["riverState"] == "stale"
        assert card["level"] == 3
        assert any(r["reason_code"] == "missing_river" for r in card["reasonsRaw"])
        assert "更新遅延" in card["river"]
    finally:
        _cleanup(stations, links, obs_ids, baseline)


def test_error_quality_is_unavailable(client):
    """品質ERRORの実測は値があっても欠測扱い（安全誤表示なし）。"""
    baseline = _max_audit_id()
    stations, links, obs_ids = [], [], []
    try:
        nearest = _create_station(client, station_code="R112-ERR")
        stations.append(nearest)
        links.append(_link(client, "S05", nearest))
        obs_ids.append(_record(client, "S05", nearest, water_level_m=2.0,
                               observed_at=_now(0), quality="ERROR"))

        card = _card(client)
        assert card["level"] == 3
        assert any(r["reason_code"] == "missing_river" for r in card["reasonsRaw"])
    finally:
        _cleanup(stations, links, obs_ids, baseline)


@pytest.mark.asyncio
async def test_river_site_without_observation_is_unavailable(monkeypatch):
    """観測所・実測がない河川現場は、安全側に「確認不能」とする。"""
    from app.services.data_collectors import open_meteo

    async def fake_fetch(lat, lon, **kw):
        sample = {
            "timezone": "Asia/Tokyo",
            "hourly": {
                "time": ["2026-08-05T08:00", "2026-08-05T09:00"],
                "temperature_2m": [28.0, 29.0], "precipitation": [0.0, 0.0],
                "wind_speed_10m": [3.0, 3.0], "wind_gusts_10m": [5.0, 5.0],
                "relative_humidity_2m": [70, 68], "weather_code": [1, 1],
            },
        }
        norm = open_meteo.normalize(sample)
        norm.update(status="OK", fetched_at="2026-08-05T08:00:00Z", error=None)
        return norm

    assessment.clear_cache()
    site = Site(id="S90", site_code="CW-NO-RIVER", name="河川観測なし現場", loc="X市",
                latitude=35.7, longitude=139.7, work_type="river",
                project_type="公共", river_work_flag=True, river_state="none",
                river_note="近接なし", flood_info=False, manager="試験")
    card = await assessment.assess_site(site, fetch=fake_fetch, warnmap={})
    assert card["level"] == 3
    assert any(r["reason_code"] == "missing_river" for r in card["reasonsRaw"])
    assert card["riverState"] == "stale"
    assessment.clear_cache()


@pytest.mark.asyncio
async def test_river_manual_state_fallback_keeps_manual_source(monkeypatch):
    """観測所未設定でも手動 river_state は後方互換で尊重し、出典はMANUALを明示する。"""
    from app.services.data_collectors import open_meteo

    async def fake_fetch(lat, lon, **kw):
        sample = {
            "timezone": "Asia/Tokyo",
            "hourly": {
                "time": ["2026-08-05T08:00", "2026-08-05T09:00"],
                "temperature_2m": [28.0, 29.0], "precipitation": [0.0, 0.0],
                "wind_speed_10m": [3.0, 3.0], "wind_gusts_10m": [5.0, 5.0],
                "relative_humidity_2m": [70, 68], "weather_code": [1, 1],
            },
        }
        norm = open_meteo.normalize(sample)
        norm.update(status="OK", fetched_at="2026-08-05T08:00:00Z", error=None)
        return norm

    assessment.clear_cache()
    site = Site(id="S91", site_code="CW-MANUAL-RIVER", name="手動入力現場", loc="X市",
                latitude=35.7, longitude=139.7, work_type="river",
                project_type="公共", river_work_flag=True, river_state="rising",
                river_note="現場確認で上昇中", flood_info=False, manager="試験")
    card = await assessment.assess_site(site, fetch=fake_fetch, warnmap={})
    assert card["riverState"] == "rising"
    assert card["riverSource"] == "MANUAL"
    assert card["level"] == 1
    codes = [r["reason_code"] for r in card["reasonsRaw"]]
    assert "water_level_rising" in codes
    assessment.clear_cache()
