"""STALE（更新遅延）縮退のテスト（詳細設計 §5.2/§5.3、Issue #33）。

取得失敗時に前回良品データへ縮退し、判定には「更新遅延＝確認不能要因」を明示する。
"""
import pytest

from app.models import Site
from app.services import assessment
from app.services.data_collectors import open_meteo
from app.services.decision_engine import Reading, evaluate

OM_SAMPLE = {
    "timezone": "Asia/Tokyo",
    "hourly": {
        "time": ["2026-06-20T08:00", "2026-06-20T09:00"],
        "temperature_2m": [28.0, 30.5], "precipitation": [0.0, 0.5],
        "wind_speed_10m": [3.0, 4.0], "wind_gusts_10m": [5.0, 6.0],
        "relative_humidity_2m": [70, 65], "weather_code": [1, 1],
    },
}


def _site() -> Site:
    return Site(id="S98", site_code="CW-ST", name="STALEテスト現場", loc="X市",
                latitude=35.7, longitude=139.7, work_type="earthwork",
                project_type="公共", river_work_flag=False, river_state="none",
                river_note="", flood_info=False, manager="試験")


def test_evaluate_stale_becomes_unavailable_reason():
    r = Reading(precip_mm_h=0.5, temp_c=25.0, wind_ms=3.0, stale_weather=True)
    d = evaluate("earthwork", r)
    codes = [x["reason_code"] for x in d["reasons"]]
    assert "stale_weather" in codes
    assert d["overall_level"] == 3  # 軽微条件のみなら確認不能へ
    assert "欠測/遅延" in d["data_quality_summary"] or "遅延" in d["data_quality_summary"]


def test_evaluate_stale_does_not_mask_known_risk():
    # 既知の中止検討リスク（豪雨）があるときは、それが優先される（§5.3の合成規則）
    r = Reading(precip_mm_h=10.0, temp_c=25.0, stale_weather=True)
    d = evaluate("earthwork", r)
    assert d["overall_level"] == 2
    assert "stale_weather" in [x["reason_code"] for x in d["reasons"]]


@pytest.mark.asyncio
async def test_cached_fetch_falls_back_to_last_good(monkeypatch):
    assessment.clear_cache()
    calls = {"n": 0}

    async def fake_om(lat, lon, **kw):
        calls["n"] += 1
        if calls["n"] == 1:
            norm = open_meteo.normalize(OM_SAMPLE)
            norm.update(status="OK", fetched_at="2026-06-20T08:00:00Z", error=None)
            return norm
        return {"source_id": "DS-OPEN-METEO", "points": [],
                "fetched_at": "2026-06-20T09:00:00Z", "status": "ERROR", "error": "boom"}

    monkeypatch.setattr(open_meteo, "fetch_forecast", fake_om)

    first = await assessment._cached_fetch(35.7, 139.7, "S98")
    assert first["status"] == "OK"

    monkeypatch.setattr(assessment, "_TTL", 0)  # キャッシュ失効を強制して再取得させる
    second = await assessment._cached_fetch(35.7, 139.7, "S98")
    assert second["status"] == "STALE"
    assert len(second["points"]) == 2                      # 前回良品の点を保持
    assert second["fetched_at"] == "2026-06-20T08:00:00Z"  # 鮮度が古いことを隠さない
    assert second["error"] == "boom"
    assessment.clear_cache()


@pytest.mark.asyncio
async def test_cached_fetch_no_last_good_stays_error(monkeypatch):
    assessment.clear_cache()

    async def always_fail(lat, lon, **kw):
        return {"source_id": "DS-OPEN-METEO", "points": [],
                "fetched_at": "2026-06-20T09:00:00Z", "status": "ERROR", "error": "down"}

    monkeypatch.setattr(open_meteo, "fetch_forecast", always_fail)
    data = await assessment._cached_fetch(35.7, 139.7, "S98")
    assert data["status"] == "ERROR"  # 良品履歴が無ければ縮退せず ERROR のまま
    assert data["points"] == []
    assessment.clear_cache()


@pytest.mark.asyncio
async def test_assess_site_stale_flow(monkeypatch):
    """OK→失効→失敗の流れで、カードが STALE 状態＋確認不能理由を返す。"""
    assessment.clear_cache()
    calls = {"n": 0}

    async def fake_om(lat, lon, **kw):
        calls["n"] += 1
        if calls["n"] == 1:
            norm = open_meteo.normalize(OM_SAMPLE)
            norm.update(status="OK", fetched_at="2026-06-20T08:00:00Z", error=None)
            return norm
        return {"source_id": "DS-OPEN-METEO", "points": [],
                "fetched_at": "2026-06-20T09:00:00Z", "status": "ERROR", "error": "boom"}

    monkeypatch.setattr(open_meteo, "fetch_forecast", fake_om)
    site = _site()

    card1 = await assessment.assess_site(site, warnmap={})
    assert card1["weatherStatus"] == "OK"

    monkeypatch.setattr(assessment, "_TTL", 0)
    card2 = await assessment.assess_site(site, warnmap={})
    assert card2["weatherStatus"] == "STALE"
    assert card2["tempHi"] == 30.5  # 前回良品の値で参考表示
    assert "stale_weather" in [x["reason_code"] for x in card2["reasonsRaw"]]
    assert card2["level"] == 3
    assessment.clear_cache()
