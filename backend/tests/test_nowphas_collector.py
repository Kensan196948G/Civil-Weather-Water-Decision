"""NOWPHAS コレクタのテスト（httpx.MockTransport でネット非依存）。"""
from __future__ import annotations

import asyncio
import time

import httpx

from app.services.data_collectors import nowphas

STATION_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<NowphasWeb><PointSetup>
<point code="221" area="3" name="KeihinYokohama" lat="35.4669" lon="139.6381" />
<point code="222" area="3" name="Tokyo" lat="35.6450" lon="139.7700" />
</PointSetup></NowphasWeb>"""

MAP_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<DataMap time20min="202608122300">
  <mapdata code="221">
    <yugiha>0.55</yugiha><shiyuki>4.6</shiyuki><namimuki>E</namimuki>
  </mapdata>
  <mapdata code="222">
    <yugiha>99999</yugiha><shiyuki>99999</shiyuki><namimuki></namimuki>
  </mapdata>
</DataMap>"""

TIDE_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<DataMap datatime="202608122320">
  <mapdata code="221"><choui>17</choui><tenmon>14</tenmon><hensa>3</hensa></mapdata>
</DataMap>"""


def _handler(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    if "choui_mapxml" in path:
        return httpx.Response(200, content=TIDE_XML)
    if "mapxml" in path:
        return httpx.Response(200, content=MAP_XML)
    return httpx.Response(200, content=STATION_XML)


def _run(coro):
    return asyncio.run(coro)


def test_parse_and_num():
    assert nowphas._num("0.55") == 0.55
    assert nowphas._num("99999") is None
    assert nowphas._num("") is None
    assert nowphas._compass_to_deg("E") == 90.0
    assert nowphas._compass_to_deg("NNE") == 22.5
    assert nowphas._parse_jst_stamp("202608122300") == "2026-08-12T14:00:00+00:00"


def test_nearest_station():
    stations = [
        nowphas.Station("A", "A港", 35.0, 140.0),
        nowphas.Station("B", "B港", 36.0, 141.0),
    ]
    assert nowphas.nearest_station(stations, 35.1, 140.1, max_km=200.0).code == "A"
    assert nowphas.nearest_station(stations, 33.0, 137.0, max_km=100.0) is None


def test_fetch_nowphas_ok(monkeypatch):
    # キャッシュを無効化して確実に上流へ問い合わせる
    monkeypatch.setattr(nowphas, "_STATION_CACHE", (0.0, []))
    monkeypatch.setattr(nowphas, "_SAMPLE_CACHE", (0.0, {}))

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(_handler)) as client:
            return await nowphas.fetch_nowphas(35.45, 139.65, client=client)

    data = _run(run())
    assert data["status"] == "OK"
    assert data["source_id"] == "DS-NOWPHAS"
    assert len(data["points"]) == 1
    p = data["points"][0]
    assert p["wave_height_m"] == 0.55
    assert p["wave_period_s"] == 4.6
    assert p["wave_direction_deg"] == 90.0
    assert p["tide_level_m"] == 0.17  # 17 cm → m
    assert p["quality_flag"] == "OK"
    assert p["station"]["code"] == "221"
    assert p["station"]["name"] == "KeihinYokohama"


def test_fetch_nowphas_missing_flag(monkeypatch):
    monkeypatch.setattr(nowphas, "_STATION_CACHE", (0.0, []))
    monkeypatch.setattr(nowphas, "_SAMPLE_CACHE", (0.0, {}))

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(_handler)) as client:
            return await nowphas.fetch_nowphas(35.65, 139.75, client=client)

    data = _run(run())
    # 東京（222）は 99999 欠測 → NO_STATION（Open-Meteo フォールバック対象）
    assert data["status"] == "NO_STATION"
    assert data["points"] == []


def test_fetch_nowphas_error_is_not_hidden(monkeypatch):
    monkeypatch.setattr(nowphas, "_STATION_CACHE", (0.0, []))
    monkeypatch.setattr(nowphas, "_SAMPLE_CACHE", (0.0, {}))

    def err_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503)

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(err_handler)) as client:
            return await nowphas.fetch_nowphas(35.45, 139.65, client=client)

    data = _run(run())
    assert data["status"] == "ERROR"
    assert data["error"]


def test_is_fresh_guard(monkeypatch):
    from datetime import datetime, timedelta, timezone

    old = (datetime.now(timezone.utc) - timedelta(hours=3)).isoformat()
    assert nowphas._is_fresh({"time": old}) is False
    fresh = datetime.now(timezone.utc).isoformat()
    assert nowphas._is_fresh({"time": fresh}) is True


def test_window_reading_compatible_with_marine():
    """NOWPHAS points が marine.window_reading の入力形状と互換であること。"""
    from app.services.data_collectors import marine

    points = [
        {"time": "2026-08-12T14:00:00+00:00", "wave_height_m": 0.55,
         "wave_period_s": 4.6, "wave_direction_deg": 90.0,
         "swell_wave_height_m": None, "swell_wave_period_s": None,
         "swell_wave_direction_deg": None, "sea_surface_temp_c": None,
         "quality_flag": "OK"},
    ]
    wr = marine.window_reading(points)
    assert wr["wave_height_m"] == 0.55
    assert wr["wave_period_s"] == 4.6
    assert wr["missing"] == {"swell"}


def test_cached_marine_prefers_nowphas(monkeypatch):
    """NOWPHAS OK なら Open-Meteo を呼ばず NOWPHAS を採用する。"""
    from app.services import assessment
    from app.services.data_collectors import nowphas as np

    calls = {"nowphas": 0, "marine": 0}

    async def fake_nowphas(lat, lon, **kw):
        calls["nowphas"] += 1
        return {"source_id": np.SOURCE_ID, "points": [
            {"time": "2026-08-12T14:00:00+00:00", "wave_height_m": 0.55,
             "wave_period_s": 4.6, "wave_direction_deg": 90.0,
             "swell_wave_height_m": None, "swell_wave_period_s": None,
             "swell_wave_direction_deg": None, "sea_surface_temp_c": None,
             "quality_flag": "OK"},
        ], "fetched_at": "2026-08-12T14:00:00Z", "status": "OK", "error": None}

    async def fake_marine(lat, lon, **kw):
        calls["marine"] += 1
        raise AssertionError("Open-Meteo should not be called when NOWPHAS is OK")

    monkeypatch.setattr(np, "fetch_nowphas", fake_nowphas)
    monkeypatch.setattr(assessment.marine, "fetch_marine", fake_marine)
    assessment.clear_cache()

    data = _run(assessment._cached_marine(35.45, 139.65, "test-site"))
    assert data["source_id"] == "DS-NOWPHAS"
    assert calls["nowphas"] == 1
    assert calls["marine"] == 0
    assessment.clear_cache()
