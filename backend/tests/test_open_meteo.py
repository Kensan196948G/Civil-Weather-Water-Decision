"""Open-Meteo コレクタのテスト（httpx.MockTransport でネット非依存）。"""
import httpx
import pytest

from app.services.data_collectors import open_meteo as om

SAMPLE = {
    "timezone": "Asia/Tokyo",
    "hourly": {
        "time": ["2026-06-20T08:00", "2026-06-20T09:00", "2026-06-20T10:00"],
        "temperature_2m": [28.0, 30.5, 31.2],
        "precipitation": [0.0, 1.5, 6.0],
        "wind_speed_10m": [4.0, 6.5, 7.0],
        "wind_gusts_10m": [8.0, 12.0, 14.0],
        "relative_humidity_2m": [70, 65, 60],
        "weather_code": [2, 61, 63],
    },
}


def test_normalize_units_and_quality():
    norm = om.normalize(SAMPLE)
    assert norm["source_id"] == "DS-OPEN-METEO"
    assert len(norm["points"]) == 3
    assert all(p["quality_flag"] == "OK" for p in norm["points"])
    assert norm["points"][0]["wbgt_derived"] is not None  # 推定WBGT


def test_normalize_missing_flag():
    payload = {"hourly": {"time": ["t1"], "temperature_2m": [None], "precipitation": [None]}}
    norm = om.normalize(payload)
    assert norm["points"][0]["quality_flag"] == "MISSING"


@pytest.mark.parametrize(
    "temp, precip, wind",
    [
        (55.0, 1.0, 5.0),   # 気温が上限超過
        (-45.0, 1.0, 5.0),  # 気温が下限未満
        (28.0, 350.0, 5.0),  # 降雨が上限超過
        (28.0, 1.0, 120.0),  # 風速が上限超過
    ],
)
def test_normalize_outlier_flag(temp, precip, wind):
    payload = {
        "hourly": {
            "time": ["t1"],
            "temperature_2m": [temp],
            "precipitation": [precip],
            "wind_speed_10m": [wind],
        }
    }
    norm = om.normalize(payload)
    assert norm["points"][0]["quality_flag"] == "OUTLIER"


def test_normalize_missing_takes_precedence_over_outlier():
    # 気温が欠測なら、他フィールドが範囲外でも OUTLIER ではなく MISSING を優先
    payload = {
        "hourly": {
            "time": ["t1"],
            "temperature_2m": [None],
            "precipitation": [1.0],
            "wind_speed_10m": [120.0],
        }
    }
    norm = om.normalize(payload)
    assert norm["points"][0]["quality_flag"] == "MISSING"


def test_window_reading_extracts_peaks():
    norm = om.normalize(SAMPLE)
    wr = om.window_reading(norm["points"])
    assert wr["precip_mm_h"] == 6.0       # ピーク降雨
    assert wr["temp_c"] == 31.2           # 最高気温
    assert wr["gust_ms"] == 14.0          # 最大瞬間風速
    assert wr["missing"] == set()


@pytest.mark.asyncio
async def test_fetch_forecast_ok_with_mock():
    def handler(request: httpx.Request) -> httpx.Response:
        assert "latitude" in request.url.params
        return httpx.Response(200, json=SAMPLE)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        data = await om.fetch_forecast(35.7, 139.7, client=client)
    assert data["status"] == "OK"
    assert len(data["points"]) == 3


@pytest.mark.asyncio
async def test_fetch_forecast_error_is_not_hidden():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        data = await om.fetch_forecast(35.7, 139.7, client=client)
    assert data["status"] == "ERROR"
    assert data["points"] == []
    assert data["error"]
