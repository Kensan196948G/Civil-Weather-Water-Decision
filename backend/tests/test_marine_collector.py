"""Open-Meteo Marine コレクタのテスト（httpx.MockTransport でネット非依存）。"""
import httpx
import pytest

from app.services.data_collectors import marine

SAMPLE = {
    "timezone": "Asia/Tokyo",
    "hourly": {
        "time": ["2026-08-09T09:00", "2026-08-09T10:00", "2026-08-09T11:00"],
        "wave_height": [0.8, 1.4, 2.3],
        "wave_period": [5.0, 7.0, 9.5],
        "wave_direction": [120.0, 140.0, 160.0],
        "wind_wave_height": [0.6, 1.0, 1.6],
        "wind_wave_period": [4.0, 5.5, 7.0],
        "wind_wave_direction": [110.0, 130.0, 150.0],
        "swell_wave_height": [0.4, 0.8, 1.1],
        "swell_wave_period": [8.0, 10.0, 12.0],
        "swell_wave_direction": [200.0, 210.0, 220.0],
        "sea_surface_temperature": [24.0, 24.5, 25.0],
    },
}


def test_normalize_units_and_quality():
    norm = marine.normalize(SAMPLE)
    assert norm["source_id"] == "DS-OPEN-METEO-MARINE"
    assert len(norm["points"]) == 3
    assert all(p["quality_flag"] == "OK" for p in norm["points"])
    assert norm["points"][0]["wave_height_m"] == 0.8
    assert norm["points"][2]["swell_wave_height_m"] == 1.1


def test_normalize_missing_flag():
    payload = {"hourly": {"time": ["t1"], "wave_height": [None], "wave_period": [None]}}
    norm = marine.normalize(payload)
    assert norm["points"][0]["quality_flag"] == "MISSING"


@pytest.mark.parametrize(
    "wave_height, wave_period, wave_direction, sst",
    [
        (30.0, 8.0, 120.0, 25.0),   # 波高が上限超過
        (1.0, 40.0, 120.0, 25.0),   # 周期が上限超過
        (1.0, 8.0, 400.0, 25.0),    # 波向が範囲外
        (1.0, 8.0, 120.0, 40.0),    # 水温が上限超過
    ],
)
def test_normalize_outlier_flag(wave_height, wave_period, wave_direction, sst):
    payload = {
        "hourly": {
            "time": ["t1"],
            "wave_height": [wave_height],
            "wave_period": [wave_period],
            "wave_direction": [wave_direction],
            "sea_surface_temperature": [sst],
        }
    }
    norm = marine.normalize(payload)
    assert norm["points"][0]["quality_flag"] == "OUTLIER"


def test_window_reading_extracts_peaks():
    norm = marine.normalize(SAMPLE)
    wr = marine.window_reading(norm["points"])
    assert wr["wave_height_m"] == 2.3
    assert wr["wave_period_s"] == 9.5
    assert wr["swell_wave_height_m"] == 1.1
    assert wr["wave_direction_deg"] == 160.0
    assert wr["missing"] == set()


def test_window_reading_missing():
    payload = {"hourly": {"time": ["t1"], "wave_height": [None], "wave_period": [None]}}
    wr = marine.window_reading(marine.normalize(payload)["points"])
    assert wr["wave_height_m"] is None
    assert {"wave", "wave_period", "swell"} <= wr["missing"]


@pytest.mark.asyncio
async def test_fetch_marine_ok_with_mock():
    def handler(request: httpx.Request) -> httpx.Response:
        assert "latitude" in request.url.params
        assert "wave_height" in request.url.params["hourly"]
        return httpx.Response(200, json=SAMPLE)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        data = await marine.fetch_marine(35.7, 139.7, client=client)
    assert data["status"] == "OK"
    assert len(data["points"]) == 3


@pytest.mark.asyncio
async def test_fetch_marine_error_is_not_hidden():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        data = await marine.fetch_marine(35.7, 139.7, client=client)
    assert data["status"] == "ERROR"
    assert data["points"] == []
    assert data["error"]
